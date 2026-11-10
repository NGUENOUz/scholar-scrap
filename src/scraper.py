"""
Module scraper.py
-----------------
Seule et unique porte d'entrée vers Crawl4AI pour tout le projet.
Garantit :
- L'exécution économe en RAM (une page à la fois, fermeture propre).
- Le respect strict de robots.txt et une cadence respectueuse (délai configurable).
- Une gestion d'erreur robuste (un échec ne fait jamais planter le reste du scraping).
"""

import asyncio
import logging
import urllib.robotparser
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Optional

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

# Configuration du journal (logs)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("Scraper")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


@dataclass
class ScrapeResult:
    """Résultat standardisé d'une tentative de scraping."""
    url: str
    success: bool
    status_code: Optional[int] = None
    html: str = ""
    markdown: str = ""
    error_message: Optional[str] = None


class PoliteScraper:
    """
    Scraper respectueux des sites et optimisé pour une machine avec peu de RAM.
    """

    def __init__(self, delay_seconds: float = 3.0):
        self.delay_seconds = delay_seconds
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._crawler: Optional[AsyncWebCrawler] = None

    def is_allowed_by_robots(self, url: str) -> bool:
        """
        Vérifie si le fichier robots.txt du site autorise la lecture de cette URL.
        Si robots.txt est indisponible ou illisible, on autorise prudemment.
        """
        try:
            import requests

            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            robots_url = f"{base_url}/robots.txt"

            if base_url not in self._robots_cache:
                rp = urllib.robotparser.RobotFileParser()
                # On utilise requests avec notre User-Agent pour éviter le blocage du header par défaut de Python
                resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=10)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    # Si robots.txt n'existe pas (ex. 404), l'accès est considéré autorisé
                    rp.allow_all = True
                self._robots_cache[base_url] = rp
            else:
                rp = self._robots_cache[base_url]

            allowed = rp.can_fetch(USER_AGENT, url)
            if not allowed:
                # Test avec le wildcard universel *
                allowed = rp.can_fetch("*", url)

            return allowed
        except Exception as e:
            logger.warning("Impossible de lire robots.txt pour %s : %s. Poursuite prudente.", url, e)
            return True

    async def __aenter__(self):
        """Initialisation propre de l'instance Crawl4AI."""
        browser_config = BrowserConfig(
            headless=True,
            verbose=False,
            user_agent=USER_AGENT,
            extra_args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )
        self._crawler = AsyncWebCrawler(config=browser_config)
        await self._crawler.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Fermeture garantie du navigateur pour libérer la mémoire vive (RAM)."""
        if self._crawler:
            await self._crawler.close()
            self._crawler = None

    async def scrape(self, url: str) -> ScrapeResult:
        """
        Scrape une URL unique.
        - Vérifie d'abord robots.txt.
        - Respecte un temps de pause avant la requête pour ne pas saturer le serveur distant.
        - Capture toute exception pour ne jamais interrompre le traitement global.
        """
        logger.info("Vérification robots.txt pour : %s", url)
        if not self.is_allowed_by_robots(url):
            msg = f"Scraping interdit par robots.txt pour {url}"
            logger.warning(msg)
            return ScrapeResult(url=url, success=False, error_message=msg)

        logger.info("Pause de courtoisie (%.1fs) avant requête...", self.delay_seconds)
        await asyncio.sleep(self.delay_seconds)

        if not self._crawler:
            raise RuntimeError("Le scraper doit être utilisé dans un bloc 'async with PoliteScraper()'.")

        try:
            logger.info("Extraction en cours : %s", url)
            run_config = CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                page_timeout=30000,   # 30 secondes max par page
                scan_full_page=False  # Pas de défilement infini superflu = économie de RAM
            )
            result = await self._crawler.arun(url=url, config=run_config)

            if not result.success:
                logger.error("Échec du scraping pour %s : %s", url, result.error_message)
                return ScrapeResult(
                    url=url,
                    success=False,
                    status_code=result.status_code,
                    error_message=result.error_message or "Échec inconnu Crawl4AI"
                )

            logger.info("Succès : %s (HTML récupéré : %d caractères)", url, len(result.html or ""))
            return ScrapeResult(
                url=url,
                success=True,
                status_code=result.status_code or 200,
                html=result.html or "",
                markdown=result.markdown or ""
            )

        except Exception as exc:
            logger.exception("Erreur inattendue pendant le scraping de %s : %s", url, exc)
            return ScrapeResult(url=url, success=False, error_message=str(exc))
