"""
Module link_resolver.py
-----------------------
Résout le VRAI lien officiel de la bourse à partir de la page de l'article agrégateur
en éliminant tout lien renvoyant vers un concurrent ou un réseau social.
"""

import logging
from urllib.parse import urlparse
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

KEYWORDS_HIGH = [
    "postuler", "apply", "site officiel", "page officielle", "official website",
    "official link", "official page", "candidater", "apply now", "application portal"
]

KEYWORDS_MEDIUM = [
    "cliquez ici", "click here", "en savoir plus", "learn more", "plus d'infos",
    "admissions", "guidelines", "call for applications"
]


def resolve_official_link(article_url: str, source_domain: str = None) -> str:
    """
    Visite la page de l'article et retourne l'URL officielle externe la plus pertinente.
    Si aucun lien externe officiel n'est trouvé, retourne l'article_url.
    """
    if not source_domain:
        source_domain = urlparse(article_url).netloc.lower()

    try:
        resp = requests.get(
            article_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=12
        )
        if resp.status_code != 200:
            return article_url

        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = []

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True).lower()
            h_low = href.lower()
            parsed = urlparse(href)

            netloc = parsed.netloc.lower()
            if not netloc or netloc == source_domain or netloc.endswith(source_domain):
                continue

            if any(ign in netloc for ign in IGNORED_DOMAINS):
                continue

            score = 0
            if any(k in text for k in KEYWORDS_HIGH):
                score += 15
            elif any(k in text for k in KEYWORDS_MEDIUM):
                score += 5

            if any(k in h_low for k in ["apply", "admission", "scholarship", "fellowship", "form"]):
                score += 5

            if any(dom in netloc for dom in [".edu", ".ac.", "univ-", ".gouv", ".gov", ".org"]):
                score += 6

            if score > 0:
                candidates.append((score, href))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            top_link = candidates[0][1]
            logger.info("Lien officiel résolu pour %s -> %s", article_url, top_link)
            return top_link

    except Exception as e:
        logger.warning("Erreur lors de la résolution du lien officiel pour %s : %s", article_url, e)

    return article_url
