"""
Module official_link_finder.py
------------------------------
Retrouve le lien officiel d'une bourse par recherche web (Gemini + Google Search).

Utilisé pour les sources qui ne publient aucun lien sortant vers l'organisme
(ex: youthmedia.net, dont le lien est réservé aux abonnés connectés).

Principe :
1. Cache persistant : chaque titre déjà tenté n'est jamais recherché deux fois
   (y compris les échecs), ce qui protège le quota gratuit de l'API.
2. Budget par exécution : seules N recherches sont autorisées par lancement.
   Comme les tentatives sont mises en cache, les exécutions suivantes
   progressent automatiquement dans la liste des bourses restantes.
3. Vérification : une URL n'est acceptée que si elle répond et que la page
   parle bien de la bourse, ou à défaut si son domaine est officiel connu.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse, urlunparse

import requests
from google import genai
from google.genai import types

logger = logging.getLogger("OfficialLinkFinder")

GEMINI_TIMEOUT_MS = 30_000
MODELS_TO_TRY = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-3.5-flash-lite"]
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "official_links_cache.json"
NEGATIVE_CACHE_DAYS = 30
DEFAULT_MAX_SEARCHES_PER_RUN = 25

# Mots trop génériques pour prouver qu'une page parle bien de la bourse visée.
STOPWORDS = {
    "bourse", "bourses", "scholarship", "scholarships", "grant", "grants",
    "fellowship", "fellowships", "pour", "avec", "dans", "les", "des", "une",
    "aux", "sur", "par", "and", "the", "for", "with", "study", "studies",
    "etudes", "étude", "études", "students", "etudiants", "étudiants",
    "international", "internationales", "programme", "program", "programs",
}

_client: Optional[genai.Client] = None
_cache: Optional[dict] = None
_searches_done = 0


def _get_clients() -> list[genai.Client]:
    global _client
    if _client is not None:
        return [_client]

    clients = []
    # Priorité à GEMINI_API_KEY2 si renseignée, puis GEMINI_API_KEY
    for key_name in ["GEMINI_API_KEY2", "GEMINI_API_KEY", "GEMINI_API_KEY_BACKUP"]:
        val = os.getenv(key_name)
        if val and val.strip():
            try:
                c = genai.Client(
                    api_key=val.strip(),
                    http_options=types.HttpOptions(
                        timeout=GEMINI_TIMEOUT_MS,
                        retry_options=types.HttpRetryOptions(attempts=1)
                    )
                )
                clients.append(c)
            except Exception as exc:
                logger.error("Impossible d'initialiser Gemini (%s) pour la recherche de lien : %s", key_name, exc)
    return clients


def _get_client() -> Optional[genai.Client]:
    clients = _get_clients()
    return clients[0] if clients else None


# --------------------------------------------------------------------------
# Cache persistant / budget
# --------------------------------------------------------------------------

def _cache_key(title: str) -> str:
    key = re.sub(r"[^\wÀ-ÿ]+", " ", (title or "").lower())
    return " ".join(key.split())


def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache

    _cache = {}
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                _cache = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cache des liens officiels illisible (%s), on repart de zéro.", exc)
            _cache = {}
    return _cache


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "w", encoding="utf-8") as handle:
            json.dump(_cache, handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        logger.warning("Impossible d'écrire le cache des liens officiels : %s", exc)


def _cache_get(title: str) -> tuple[bool, str]:
    """Retourne (tentative déjà faite, lien trouvé ou chaîne vide)."""
    entry = _load_cache().get(_cache_key(title))
    if not isinstance(entry, dict):
        return False, ""

    url = entry.get("url") or ""
    if url:
        return True, url

    attempted_at = entry.get("ts")
    try:
        attempted = datetime.fromisoformat(attempted_at)
    except (TypeError, ValueError):
        return False, ""
    if datetime.now() - attempted > timedelta(days=NEGATIVE_CACHE_DAYS):
        return False, ""
    return True, ""


def _cache_set(title: str, url: str) -> None:
    _load_cache()[_cache_key(title)] = {
        "url": url,
        "ts": datetime.now().isoformat(timespec="seconds")
    }
    _save_cache()


def reset_run_budget() -> None:
    """Remet le compteur de recherches à zéro (à appeler au début d'une exécution)."""
    global _searches_done
    _searches_done = 0


def _max_searches_per_run() -> int:
    try:
        return max(0, int(os.getenv("LINK_SEARCH_MAX_PER_RUN", DEFAULT_MAX_SEARCHES_PER_RUN)))
    except ValueError:
        return DEFAULT_MAX_SEARCHES_PER_RUN


# --------------------------------------------------------------------------
# Recherche et vérification
# --------------------------------------------------------------------------

def _significant_tokens(text: str) -> set:
    tokens = set()
    for word in re.split(r"[^\wÀ-ÿ]+", text.lower()):
        if len(word) < 4 or word in STOPWORDS:
            continue
        if word.isdigit():
            continue
        tokens.add(word)
    return tokens


def _extract_url(text: str) -> str:
    match = re.search(r"https?://[^\s\"'<>)\\]+", text or "")
    if not match:
        return ""
    url = match.group(0).rstrip(".,;")
    if "vertexaisearch.cloud.google.com" in url or "googleusercontent" in url:
        return ""
    return url


def _is_excluded(host: str, exclude_domains: Iterable[str]) -> bool:
    for domain in exclude_domains:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _is_trusted(host: str, trusted_hints: Iterable[str]) -> bool:
    return any(hint in host for hint in trusted_hints)


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme or "https", parsed.netloc, parsed.path or "/", "", parsed.query, ""))


def _page_matches(url: str, tokens: set, trusted_hints: Iterable[str]) -> bool:
    """
    Vérifie que l'URL répond et que son contenu parle bien de la bourse.
    - Page inaccessible ou bloquée (403/429/503/erreur réseau) : acceptée seulement
      si le domaine est un domaine officiel connu (.edu, .gov, univ...).
    - Page 404 : refusée, l'URL proposée est probablement inventée.
    """
    host = (urlparse(url).hostname or "").lower()

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=20
        )
    except Exception as exc:  # noqa: BLE001
        if _is_trusted(host, trusted_hints):
            logger.warning(
                "Lien non vérifiable (%s) mais domaine officiel reconnu, conservé : %s", exc, url
            )
            return True
        logger.info("Lien candidat non vérifiable et domaine inconnu, refusé : %s (%s)", url, exc)
        return False

    if resp.status_code in (403, 429, 503) and _is_trusted(host, trusted_hints):
        logger.warning("Lien protégé (HTTP %s) sur domaine officiel, conservé : %s", resp.status_code, url)
        return True

    if resp.status_code != 200 or not resp.text:
        logger.info("Lien candidat refusé (HTTP %s) : %s", resp.status_code, url)
        return False

    if not tokens:
        return True

    page_text = resp.text.lower()
    found = {token for token in tokens if token in page_text}
    ratio = len(found) / len(tokens)
    logger.info(
        "Vérification '%s' : %d/%d mots du titre retrouvés (%.0f%%)",
        url, len(found), len(tokens), ratio * 100
    )
    return ratio >= 0.5


def find_official_link(
    title: str,
    snippet: str = "",
    exclude_domains: Iterable[str] = (),
    trusted_hints: Iterable[str] = ()
) -> str:
    """
    Recherche le lien officiel d'une bourse à partir de son titre.
    Retourne une chaîne vide si aucun lien fiable n'est confirmé.
    """
    global _searches_done

    if not title:
        return ""

    already_attempted, cached_url = _cache_get(title)
    if already_attempted:
        if cached_url:
            logger.info("Lien officiel déjà connu (cache) pour '%s' : %s", title, cached_url)
        return cached_url

    if _searches_done >= _max_searches_per_run():
        logger.warning(
            "Budget de recherches de liens atteint (%d). '%s' sera traité à la prochaine exécution.",
            _max_searches_per_run(), title
        )
        return ""

    clients = _get_clients()
    single = _get_client()
    if single and single not in clients:
        clients = [single] + clients

    if not clients:
        return ""

    prompt = f"""Tu es un documentaliste spécialisé dans les bourses d'études internationales.

Retrouve la PAGE OFFICIELLE de cette bourse, c'est-à-dire celle de l'organisme qui l'attribue
(université, ministère, fondation, agence gouvernementale, programme européen...).

Bourse : "{title}"
Contexte publié par le site d'actualité : {snippet[:1500] or "Non précisé"}

Règles strictes :
1. Donne l'URL officielle de l'organisme, jamais un article de presse, un blog, un agrégateur de bourses ni un réseau social.
2. L'URL doit être une page réelle et précise (page de la bourse ou de l'appel à candidatures), pas une page d'accueil générique.
3. Si tu n'es pas certain de l'URL exacte, renvoie une chaîne vide pour "lien_officiel".

Réponds UNIQUEMENT par un JSON valide, sans balise markdown :
{{"lien_officiel": "https://...", "organisme": "Nom de l'organisme"}}"""

    response = None
    _searches_done += 1

    for client_idx, client in enumerate(clients):
        for model in MODELS_TO_TRY:
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0
                    )
                )
                if response and response.text:
                    break
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                if "RESOURCE_EXHAUSTED" in err or "429" in err:
                    logger.warning("Recherche de lien : %s quota épuisé sur clé #%d.", model, client_idx + 1)
                    continue
                elif "503" in err or "NOT_FOUND" in err or "404" in err:
                    continue
                else:
                    # En cas d'erreur de l'outil de recherche, tenter directement sans outil
                    try:
                        response = client.models.generate_content(
                            model=model,
                            contents=prompt,
                            config=types.GenerateContentConfig(temperature=0)
                        )
                        if response and response.text:
                            break
                    except Exception:
                        pass

        if response and response.text:
            break

        if client_idx + 1 < len(clients):
            logger.info("🔄 Bascule recherche de lien vers clé suivante (%d/%d)...", client_idx + 2, len(clients))

    if not response or not response.text:
        logger.warning("Aucun modèle n'a répondu pour la recherche : %s", title)
        return ""

    raw = response.text.replace("```json", "").replace("```", "").strip()
    candidate_url = ""
    try:
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if json_match:
            candidate_url = str(json.loads(json_match.group(0)).get("lien_officiel") or "").strip()
    except Exception:  # noqa: BLE001
        candidate_url = ""

    if not candidate_url:
        candidate_url = _extract_url(raw)

    if not candidate_url.startswith("http"):
        logger.info("Aucun lien officiel proposé pour : %s", title)
        _cache_set(title, "")
        return ""

    candidate_url = _normalize_url(candidate_url)
    host = (urlparse(candidate_url).hostname or "").lower()
    if not host or _is_excluded(host, exclude_domains):
        logger.info("Lien officiel rejeté (domaine exclu) pour '%s' : %s", title, candidate_url)
        _cache_set(title, "")
        return ""

    if not _page_matches(candidate_url, _significant_tokens(title), trusted_hints):
        logger.warning("Lien officiel rejeté après vérification pour '%s' : %s", title, candidate_url)
        _cache_set(title, "")
        return ""

    logger.info("Lien officiel trouvé pour '%s' -> %s", title, candidate_url)
    _cache_set(title, candidate_url)
    return candidate_url
