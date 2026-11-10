"""
Module platform_sync.py
-----------------------
Récupère UNE SEULE FOIS par exécution la liste des bourses déjà publiées
sur la plateforme (via le sitemap XML de drenolearn.com).
Aucune charge sur la base de données Vercel.
"""

import os
import re
import logging
from typing import Set, List
import requests
from bs4 import BeautifulSoup

from src.dedup import normalize_url, clean_title, are_titles_similar

logger = logging.getLogger("PlatformSync")

DEFAULT_SITEMAP_URL = "https://drenolearn.com/sitemap.xml"


class PlatformSync:
    """Synchronisation légère avec le sitemap public de la plateforme."""

    def __init__(self, sitemap_url: str = None):
        self.sitemap_url = sitemap_url or os.getenv("PLATFORM_SITEMAP_URL", DEFAULT_SITEMAP_URL)
        self.existing_urls: Set[str] = set()
        self.existing_titles: List[str] = []
        self._fetched = False

    def fetch_existing_scholarships(self) -> None:
        """
        Télécharge et analyse le sitemap XML une seule fois.
        Extrait les slugs des bourses (ex: /bourses/chevening-scholarship-uk).
        """
        if self._fetched:
            return

        logger.info("Lecture unique du sitemap de la plateforme : %s", self.sitemap_url)
        try:
            resp = requests.get(
                self.sitemap_url,
                headers={"User-Agent": "Mozilla/5.0 (ScholarshipDiscoveryBot/1.0)"},
                timeout=15
            )
            if resp.status_code != 200:
                logger.warning(
                    "Impossible de lire le sitemap (HTTP %d). Poursuite sans dédoublonnage plateforme.",
                    resp.status_code
                )
                self._fetched = True
                return

            soup = BeautifulSoup(resp.text, "xml")
            loc_tags = soup.find_all("loc")

            for loc in loc_tags:
                url = loc.get_text(strip=True)
                # On ne garde que les URLs relatives aux bourses
                if "/bourses/" in url:
                    self.existing_urls.add(normalize_url(url))
                    # Extraire le slug après /bourses/
                    match = re.search(r"/bourses/([^/?#]+)", url)
                    if match:
                        slug = match.group(1).replace("-", " ")
                        self.existing_titles.append(slug)

            logger.info(
                "Synchronisation plateforme réussie : %d bourses trouvées sur la plateforme.",
                len(self.existing_titles)
            )
            self._fetched = True

        except Exception as e:
            logger.warning(
                "Erreur lors de la lecture du sitemap plateforme : %s. Poursuite sans blocage.", e
            )
            self._fetched = True

    def is_present_on_platform(self, title: str, url: str = "") -> bool:
        """
        Vérifie si une bourse candidate est déjà présente sur la plateforme.
        """
        if not self._fetched:
            self.fetch_existing_scholarships()

        # 1. Vérification par URL normalisée
        if url and normalize_url(url) in self.existing_urls:
            return True

        # 2. Vérification par similarité de titre avec les slugs existants
        for existing in self.existing_titles:
            if are_titles_similar(title, existing, threshold=80.0):
                return True

        return False
