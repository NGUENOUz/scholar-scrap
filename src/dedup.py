"""
Module dedup.py
---------------
Fonctions de normalisation et de détection de doublons :
- Normalisation d'URL (suppression des tags UTM, ancres, www, trailing slash).
- Normalisation de texte / titre.
- Comparaison floue de titres (via rapidfuzz).
"""

import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from rapidfuzz import fuzz

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "source", "mc_cid", "mc_eid"
}


def normalize_url(url: str) -> str:
    """
    Normalise une URL pour comparaison :
    - Force en minuscules
    - Supprime http vs https (uniformise)
    - Supprime le préfixe 'www.'
    - Supprime les ancres (#...)
    - Supprime les paramètres de tracking (utm, fbclid...)
    - Supprime le slash final
    """
    if not url:
        return ""

    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    # Filtrer les paramètres de tracking dans la query string
    query_params = parse_qsl(parsed.query, keep_blank_values=False)
    clean_params = [
        (k, v) for k, v in query_params if k.lower() not in TRACKING_PARAMS
    ]
    # Trier pour garantir l'ordre déterministe
    clean_params.sort(key=lambda x: x[0])
    clean_query = urlencode(clean_params)

    path = parsed.path.rstrip("/")

    # On uniformise toujours en https
    clean_url = urlunparse(("https", netloc, path, "", clean_query, ""))
    return clean_url


def clean_title(title: str) -> str:
    """
    Nettoie un titre pour faciliter la comparaison :
    - Minuscules
    - Suppression des années (ex: 2025, 2026, 2027, 2028)
    - Suppression de la ponctuation superflue
    """
    if not title:
        return ""

    t = title.lower()
    # Supprimer les années (2020 à 2035) et motifs de type 2026-2027
    t = re.sub(r"\b20[2-3][0-9](?:[-/–]20[2-3][0-9])?\b", " ", t)
    # Remplacer la ponctuation par des espaces
    t = re.sub(r"[^\w\s]", " ", t)
    # Réduire les espaces multiples
    t = " ".join(t.split())
    return t


def are_titles_similar(title1: str, title2: str, threshold: float = 85.0) -> bool:
    """
    Compare deux titres avec similarité floue.
    Utilise token_set_ratio pour tolérer les variations mineures et les compléments.
    Retourne True si le score est supérieur ou égal au seuil (ex: 85%).
    """
    clean1 = clean_title(title1)
    clean2 = clean_title(title2)

    if not clean1 or not clean2:
        return False

    if clean1 == clean2:
        return True

    # token_set_ratio est particulièrement efficace pour les titres avec ajout de mots clés
    score = fuzz.token_set_ratio(clean1, clean2)
    return score >= threshold
