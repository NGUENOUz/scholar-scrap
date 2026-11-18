"""
Module link_resolver.py
-----------------------
Résout le VRAI lien officiel de la bourse à partir de la page de l'article agrégateur
en éliminant tout lien renvoyant vers un concurrent ou un réseau social.
"""

import logging
from urllib.parse import urljoin, urlparse
from typing import Optional
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("LinkResolver")

IGNORED_DOMAINS = {
    "facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "t.me", "telegram.org", "whatsapp.com", "pinterest.com",
    "youtube.com", "tiktok.com", "google.com", "bit.ly", "tinyurl.com",
    "wp.me", "feedburner.com", "wordpress.org"
}

AGGREGATOR_DOMAINS = {
    "greatyop.com",
    "scholarships365.info",
    "scholarshipscorner.website",
    "maboursedetudes.com",
    "mastere.tn",
    "youthmedia.net",
}

KEYWORDS_HIGH = [
    "postuler", "apply", "site officiel", "page officielle", "official website",
    "official link", "official page", "candidater", "apply now", "application portal",
    "portail de candidature", "dépôt de candidature", "lien officiel", "soumettre sa candidature"
]

KEYWORDS_MEDIUM = [
    "en savoir plus", "learn more", "plus d'informations", "source", "cliquez ici",
    "formulaire", "application form", "portal", "website", "consulter l'offre", "détails"
]

OFFICIAL_DOMAIN_HINTS = (
    ".edu", ".ac.", ".gov", ".gouv", "univ", "universit", "college",
    "ministry", "ministere", "foundation", "fondation", "daad", "unesco",
    "who.int", "europa.eu",
)


def _is_domain_or_subdomain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _is_aggregator(host: str) -> bool:
    return any(_is_domain_or_subdomain(host, domain) for domain in AGGREGATOR_DOMAINS)


def resolve_official_link(
    article_url: str,
    source_domain: str = None,
    title: str = None,
    hint_text: str = ""
) -> str:
    """
    Retourne un lien externe fortement identifié comme officiel.
    Si la page de l'article n'expose aucun lien sortant exploitable (source à accès
    restreint, ex: youthmedia), une recherche web est lancée à partir du titre.
    Une chaîne vide signifie qu'aucun lien officiel fiable n'a été trouvé.
    """
    article_host = (urlparse(article_url).hostname or "").lower()

    try:
        resp = requests.get(
            article_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=12
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            candidates = []

            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                text = a.get_text(strip=True).lower()
                absolute_url = urljoin(article_url, href)
                parsed_u = urlparse(absolute_url)
                host = (parsed_u.hostname or "").lower()

                if not host or host == article_host or _is_domain_or_subdomain(host, article_host):
                    continue

                if any(_is_domain_or_subdomain(host, domain) for domain in IGNORED_DOMAINS):
                    continue
                if _is_aggregator(host):
                    continue

                explicit_official_label = any(
                    keyword in text for keyword in ("site officiel", "page officielle", "official website", "official page", "lien officiel")
                )
                has_application_label = any(keyword in text for keyword in KEYWORDS_HIGH)
                has_medium_label = any(keyword in text for keyword in KEYWORDS_MEDIUM)
                trusted_domain = any(hint in host for hint in OFFICIAL_DOMAIN_HINTS)

                score = 0
                if explicit_official_label:
                    score += 20
                if has_application_label:
                    score += 15
                if trusted_domain:
                    score += 10
                if has_medium_label:
                    score += 5

                # Accepter le lien s'il a un signal officiel explicite, un lien de candidature, ou un domaine institutionnel qualifié
                if score >= 10:
                    candidates.append((score, absolute_url))

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                top_link = candidates[0][1]
                logger.info("Lien officiel résolu pour %s -> %s", article_url, top_link)
                return top_link

    except Exception as e:
        logger.warning("Erreur lors de la résolution du lien officiel pour %s : %s", article_url, e)

    if title:
        # Dernier recours : recherche web (sources sans lien sortant, ex: youthmedia).
        try:
            from src.official_link_finder import find_official_link

            excluded = set(AGGREGATOR_DOMAINS) | set(IGNORED_DOMAINS)
            if article_host:
                excluded.add(article_host)
            found = find_official_link(
                title=title,
                snippet=hint_text,
                exclude_domains=excluded,
                trusted_hints=OFFICIAL_DOMAIN_HINTS
            )
            if found:
                return found
        except Exception as e:  # noqa: BLE001
            logger.warning("Recherche web du lien officiel impossible pour %s : %s", title, e)

    return ""
