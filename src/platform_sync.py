"""
Module platform_sync.py
-----------------------
Récupère au démarrage la liste de TOUTES les bourses existantes sur DrenoLearn :
1. En priorité directement depuis Supabase (table bourses) pour connaître à la fois
   les bourses déjà publiées (is_active = True) ET les brouillons en cours (is_active = False).
2. En fallback via le sitemap XML public si Supabase n'est pas configuré.

Garantit un dédoublonnage infaillible par URL officielle, slug et similarité de titre.
"""

import os
import re
import logging
from typing import Set, List, Optional, Dict, Any
import requests
from bs4 import BeautifulSoup

from src.dedup import normalize_url, clean_title, are_titles_similar

logger = logging.getLogger("PlatformSync")

DEFAULT_SITEMAP_URL = "https://drenolearn.com/sitemap.xml"


class PlatformSync:
    """Synchronisation avec la plateforme (Supabase ou sitemap)."""

    def __init__(self, sitemap_url: str = None, supabase_importer=None):
        self.sitemap_url = sitemap_url or os.getenv("PLATFORM_SITEMAP_URL", DEFAULT_SITEMAP_URL)
        self.supabase_importer = supabase_importer

        # Identifiants Supabase (les plus fiables)
        self.published_urls: Set[str] = set()
        self.draft_urls: Set[str] = set()
        self.published_slugs: Set[str] = set()
        self.draft_slugs: Set[str] = set()
        self.published_titles: List[str] = []
        self.draft_titles: List[str] = []

        # Fallback sitemap
        self.existing_urls: Set[str] = set()
        self.existing_titles: List[str] = []
        self._fetched = False

    def fetch_existing_scholarships(self) -> None:
        """
        Récupère les bourses existantes depuis Supabase (ou à défaut le sitemap).
        """
        if self._fetched:
            return

        # Tentative via Supabase en priorité
        if self.supabase_importer and getattr(self.supabase_importer, "client", None):
            try:
                data = self.supabase_importer.fetch_all_scholarship_identifiers()
                self.published_urls = data.get("published_urls", set())
                self.draft_urls = data.get("draft_urls", set())
                self.published_slugs = data.get("published_slugs", set())
                self.draft_slugs = data.get("draft_slugs", set())
                self.published_titles = data.get("published_titles", [])
                self.draft_titles = data.get("draft_titles", [])

                total = len(self.published_titles) + len(self.draft_titles)
                if total > 0:
                    logger.info(
                        "Dédoublonnage Supabase actif : %d bourses publiées, %d brouillons chargés.",
                        len(self.published_titles), len(self.draft_titles)
                    )
                    self._fetched = True
                    return
            except Exception as e:
                logger.warning("Échec de synchronisation directe Supabase : %s. Bascule sur sitemap.", e)

        # Fallback : analyse du sitemap XML
        logger.info("Lecture du sitemap public de la plateforme : %s", self.sitemap_url)
        try:
            resp = requests.get(
                self.sitemap_url,
                headers={"User-Agent": "Mozilla/5.0 (ScholarshipDiscoveryBot/1.0)"},
                timeout=15
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "xml")
                loc_tags = soup.find_all("loc")

                for loc in loc_tags:
                    url = loc.get_text(strip=True)
                    if "/bourses/" in url:
                        self.existing_urls.add(normalize_url(url))
                        match = re.search(r"/bourses/([^/?#]+)", url)
                        if match:
                            slug = match.group(1).replace("-", " ")
                            self.existing_titles.append(slug)

                logger.info(
                    "Synchronisation sitemap réussie : %d bourses trouvées.",
                    len(self.existing_titles)
                )
            else:
                logger.warning("Impossible de lire le sitemap (HTTP %d).", resp.status_code)
        except Exception as e:
            logger.warning("Erreur lors de la lecture du sitemap plateforme : %s", e)

        self._fetched = True

    def is_already_published(self, title: str, url: str = "") -> bool:
        """Indique si la bourse est déjà publiée sur la plateforme (is_active = True)."""
        if not self._fetched:
            self.fetch_existing_scholarships()

        norm_url = normalize_url(url) if url else ""
        if norm_url and norm_url in self.published_urls:
            return True

        from src.supabase_sync import slugify
        slug = slugify(title)
        if slug in self.published_slugs:
            return True

        for pub_t in self.published_titles:
            if are_titles_similar(title, pub_t, threshold=82.0):
                return True

        return False

    def is_already_draft(self, title: str, url: str = "") -> bool:
        """Indique si la bourse est déjà dans les brouillons de Supabase (is_active = False)."""
        if not self._fetched:
            self.fetch_existing_scholarships()

        norm_url = normalize_url(url) if url else ""
        if norm_url and norm_url in self.draft_urls:
            return True

        from src.supabase_sync import slugify
        slug = slugify(title)
        if slug in self.draft_slugs:
            return True

        for d_t in self.draft_titles:
            if are_titles_similar(title, d_t, threshold=82.0):
                return True

        return False

    def is_present_on_platform(self, title: str, url: str = "") -> bool:
        """
        Vérifie si une bourse candidate est déjà présente sur la plateforme
        (soit déjà publiée, soit déjà présente en brouillon).
        """
        if not self._fetched:
            self.fetch_existing_scholarships()

        # 1. Vérification dans les données Supabase (publiées ou brouillons)
        if self.is_already_published(title, url):
            return True

        if self.is_already_draft(title, url):
            return True

        # 2. Vérification de repli dans le sitemap
        norm_url = normalize_url(url) if url else ""
        if norm_url and norm_url in self.existing_urls:
            return True

        for existing in self.existing_titles:
            if are_titles_similar(title, existing, threshold=80.0):
                return True

        return False
