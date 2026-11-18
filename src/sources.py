"""
Module sources.py
-----------------
Gère la lecture de la configuration YAML et l'extraction des bourses candidates
à partir du HTML retourné par le scraper.
"""

import re
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse, urljoin
import yaml
from bs4 import BeautifulSoup

from src.models import ScholarshipCandidate
from src.scraper import PoliteScraper, ScrapeResult

logger = logging.getLogger("Sources")


def detect_domain_type(url: str) -> str:
    """
    Analyse l'URL pour déterminer s'il s'agit d'une source officielle :
    Université, Gouvernement, Fondation, ou Autre.
    """
    if not url:
        return "Autre"

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # Universités et monde académique
    if any(k in domain for k in [".edu", ".ac.", "univ-", "universit", "college", "campus"]):
        return "Université"

    # Gouvernements
    if any(k in domain for k in [".gouv.", ".gov.", ".gov", ".gouv", "minist"]):
        return "Gouvernement"

    # Fondations et organisations
    if any(k in domain for k in [".org", "unesco", "fondation", "foundation", "daad"]):
        return "Fondation"

    return "Autre"


# Dictionnaires pour le parsing des mois
FRENCH_MONTHS = {
    "janvier": 1, "janv": 1, "février": 2, "fevrier": 2, "févr": 2, "fevr": 2,
    "mars": 3, "avril": 4, "avr": 4, "mai": 5, "juin": 6,
    "juillet": 7, "juil": 7, "août": 8, "aout": 8, "septembre": 9, "sept": 9,
    "octobre": 10, "oct": 10, "novembre": 11, "nov": 11, "décembre": 12, "decembre": 12, "déc": 12
}

ENGLISH_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12
}


def parse_date_safely(date_text: str) -> Optional[datetime]:
    """
    Tente de convertir une chaîne de date (FR ou EN) en objet datetime.
    Supporte les ordinaux (1er, 1st, 2nd, etc.) et les formats variés.
    Retourne None si la date n'est pas analysable avec certitude.
    """
    if not date_text:
        return None

    clean = date_text.strip().lower()
    # Nettoyer les ordinaux : 1er -> 1, 31st -> 31, etc.
    clean = re.sub(r"\b(\d+)(?:er|ère|ere|st|nd|rd|th)\b", r"\1", clean)

    # Formats numériques directs : DD/MM/YYYY, YYYY-MM-DD
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"):
        m = re.search(r"\b(\d{1,4}[-/]\d{1,2}[-/]\d{2,4})\b", clean)
        if m:
            try:
                return datetime.strptime(m.group(1), fmt)
            except ValueError:
                pass

    # Format textuel FR : DD Mois YYYY (ex: 15 juin 2026 ou 1 juin 2026)
    m_fr = re.search(r"\b(\d{1,2})\s+([a-zéèêûô]+)\s+(\d{4})\b", clean)
    if m_fr:
        day = int(m_fr.group(1))
        month_str = m_fr.group(2)
        year = int(m_fr.group(3))
        month = FRENCH_MONTHS.get(month_str) or ENGLISH_MONTHS.get(month_str)
        if month and 1 <= day <= 31:
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

    # Format textuel EN : Mois DD, YYYY (ex: May 15, 2026)
    m_en = re.search(r"\b([a-z]+)\s+(\d{1,2}),?\s+(\d{4})\b", clean)
    if m_en:
        month_str = m_en.group(1)
        day = int(m_en.group(2))
        year = int(m_en.group(3))
        month = ENGLISH_MONTHS.get(month_str)
        if month and 1 <= day <= 31:
            try:
                return datetime(year, month, day)
            except ValueError:
                pass

    # Format textuel : Mois YYYY (ex: juin 2026 / June 2026) -> Dernier jour du mois ou 28
    m_month_year = re.search(r"\b([a-zéèêûô]+)\s+(\d{4})\b", clean)
    if m_month_year:
        month_str = m_month_year.group(1)
        year = int(m_month_year.group(2))
        month = FRENCH_MONTHS.get(month_str) or ENGLISH_MONTHS.get(month_str)
        if month:
            # On prend par défaut le 28 du mois pour rester prudent
            try:
                return datetime(year, month, 28)
            except ValueError:
                pass

    return None


def is_deadline_expired(deadline_text: Optional[str]) -> bool:
    """
    Vérifie si la bourse est encore valide ou déjà expirée.
    - Si aucune date n'est détectée : on considère la bourse valide (ne pas jeter par erreur).
    - Si la date est passée : retourne True (expirée).
    - Si formulée en 'X jours restants' avec X > 0 : valide (False).
    """
    if not deadline_text:
        return False

    lower = deadline_text.lower()

    # Mots-clés explicites d'expiration
    if any(k in lower for k in ["expiré", "expire", "fermé", "ferme", "clos", "closed"]):
        return True

    # "0 jour restant" ou négatif
    m_days = re.search(r"(\d+)\s*jours?\s*restant", lower)
    if m_days and int(m_days.group(1)) == 0:
        return True

    # Vérification par date calendaire
    parsed_date = parse_date_safely(deadline_text)
    if parsed_date:
        # Si la date limite est strictement antérieure à aujourd'hui
        return parsed_date.date() < datetime.now().date()

    return False


def extract_deadline(text: str) -> Optional[str]:
    """
    Extrait une date limite si elle est clairement formulée dans le texte ou un badge.
    Exemples: 'Date limite : 15 mai 2026', 'Deadline: May 30, 2026', 'Candidater avant le 1er juin 2026',
    '90 Jours restant', 'Jusqu'au 30/04/2026', etc.
    Ne rien inventer : si non trouvé, retourne None.
    """
    if not text:
        return None

    # Motif 1 : "X jours restants"
    match_days = re.search(r"(\d+\s*jours?\s*restants?)", text, re.IGNORECASE)
    if match_days:
        return match_days.group(1).strip()

    # Motif 2 : Formulations avec mot-clé explicite
    deadline_pattern = re.compile(
        r"(?:date\s+limite(?:\s+de\s+(?:candidature|dépôt|soumission))?|"
        r"date\s+de\s+clôture|clôture(?:\s+des\s+candidatures)?(?:\s+le)?|"
        r"fin\s+des\s+candidatures|limite\s+de\s+dépôt|"
        r"candidater\s+avant\s+le|postuler\s+avant\s+le|au\s+plus\s+tard\s+le|avant\s+le|jusqu['’]au|"
        r"application\s+deadline|closing\s+date|closing\s+on|closes\s+on|apply\s+before|apply\s+by|"
        r"deadline(?:\s+is)?)\s*[:\-]?\s*([0-9a-zA-ZÀ-ÿ\s,./–-]{4,35})",
        re.IGNORECASE
    )
    match_deadline = deadline_pattern.search(text)
    if match_deadline:
        candidate_date = match_deadline.group(1).strip()
        # Nettoyer d'éventuels retours à la ligne ou fins de phrase
        candidate_date = re.split(r"[\n\r;.]", candidate_date)[0].strip(" ,-–:")
        # Ne garder que si la date a un contenu raisonnable et comporte au moins un chiffre
        if len(candidate_date) >= 4 and any(char.isdigit() for char in candidate_date):
            return candidate_date

    # Motif 3 : Recherche directe de "jusqu'au DD Mois YYYY" ou "avant le DD/MM/YYYY"
    direct_pattern = re.compile(
        r"(?:avant\s+le|jusqu['’]au|au\s+plus\s+tard\s+le)\s+(\d{1,2}(?:er)?\s+[a-zA-ZÀ-ÿ]+\s+\d{4})",
        re.IGNORECASE
    )
    m_direct = direct_pattern.search(text)
    if m_direct:
        return m_direct.group(1).strip()

    return None


class SourceManager:
    """Gestionnaire de sources basé sur config/sources.yaml."""

    def __init__(self, config_path: str = "config/sources.yaml"):
        p = Path(config_path)
        if not p.exists():
            fallback = Path(__file__).resolve().parent.parent / config_path
            if fallback.exists():
                p = fallback
        self.config_path = p
        self.sources = self._load_config()

    def _load_config(self) -> list:
        if not self.config_path.exists():
            raise FileNotFoundError(f"Fichier de configuration introuvable : {self.config_path}")
        with open(self.config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data.get("sources", [])

    def get_source(self, name: str) -> Optional[dict]:
        """Retourne la configuration d'une source spécifique par son nom."""
        for src in self.sources:
            if src.get("name") == name:
                return src
        return None

    @staticmethod
    def _extract_labeled_value(soup, label: str) -> Optional[str]:
        """
        Récupère la valeur associée à un libellé, par exemple :
        <dt>Date limite</dt><dd>15/11/2026</dd>.
        Utile pour les sources dont les dates ne sont pas dans une classe CSS dédiée.
        """
        label_lower = label.strip().lower()
        for tag in soup.find_all(["dt", "th", "span", "div", "strong", "p", "label"]):
            if tag.get_text(" ", strip=True).lower() != label_lower:
                continue
            sibling = tag.find_next_sibling()
            if sibling is None:
                continue
            value = " ".join(sibling.get_text(" ", strip=True).split())
            if value:
                return value
        return None

    @staticmethod
    def _extract_jsonld_description(soup) -> str:
        """
        Récupère la description JSON-LD "Article" d'une page de détail.
        Elle contient souvent les critères structurés (niveau, âge, date limite...)
        absents du HTML visible : matière première idéale pour l'enrichissement IA.
        """
        for script in soup.find_all("script", type="application/ld+json"):
            raw = script.string or script.get_text()
            if not raw or '"Article"' not in raw:
                continue
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            description = data.get("description") if isinstance(data, dict) else None
            if isinstance(description, str) and description.strip():
                # On conserve les retours à la ligne : ils séparent les critères structurés
                # (« - Niveau : ... », « - Date limite : ... ») exploitables par l'IA.
                lines = [" ".join(line.split()) for line in description.splitlines()]
                return "\n".join(line for line in lines if line)
        return ""

    def extract_from_html(self, source_config: dict, html: str, page_url: str) -> List[ScholarshipCandidate]:
        """
        Extrait la liste des candidats à partir du code HTML de la page de listing.
        """
        soup = BeautifulSoup(html, "html.parser")
        candidates: List[ScholarshipCandidate] = []

        article_selector = source_config.get("article_selector", "article")
        title_selector = source_config.get("title_selector", "h2 a, h3 a")
        link_selector = source_config.get("link_selector", "h2 a, a")
        snippet_selector = source_config.get("snippet_selector", "p")
        deadline_selector = source_config.get("deadline_selector", ".deadline")

        articles = soup.select(article_selector)
        logger.info(
            "[%s] %d éléments trouvés avec le sélecteur '%s'",
            source_config["name"],
            len(articles),
            article_selector
        )

        for art in articles:
            # 1. Extraction du titre
            title_elem = art.select_one(title_selector)
            title = title_elem.get_text(strip=True) if title_elem else ""
            if not title:
                continue

            # 2. Extraction du lien (relatif -> absolu)
            link_elem = art.select_one(link_selector) or title_elem
            raw_link = link_elem.get("href", "") if link_elem and link_elem.name == "a" else ""
            if not raw_link and link_elem:
                parent_a = link_elem.find_parent("a")
                if parent_a:
                    raw_link = parent_a.get("href", "")

            if not raw_link:
                continue

            official_link = urljoin(page_url, raw_link)

            # 3. Extraction de l'extrait (1 à 3 phrases)
            snippet = ""
            snippet_elem = art.select_one(snippet_selector)
            if snippet_elem:
                snippet = " ".join(snippet_elem.get_text(strip=True).split())
                # Limite à 3 phrases simples max
                sentences = re.split(r"(?<=[.!?])\s+", snippet)
                snippet = " ".join(sentences[:3])

            # 4. Extraction de la date limite
            deadline = None
            if deadline_selector:
                dl_elem = art.select_one(deadline_selector)
                if dl_elem:
                    deadline = extract_deadline(dl_elem.get_text(strip=True))
            if not deadline and snippet:
                deadline = extract_deadline(snippet)

            # 5. Détection du type de domaine officiel probable
            domain_type = detect_domain_type(official_link)

            candidate = ScholarshipCandidate(
                title=title,
                official_link=official_link,
                source_site=source_config["name"],
                discovery_url=page_url,
                snippet=snippet,
                deadline=deadline,
                domain_type=domain_type,
                status="nouveau"
            )
            candidates.append(candidate)

        return candidates

    async def scrape_source(self, scraper: PoliteScraper, source_name: str) -> List[ScholarshipCandidate]:
        """
        Scrape une source donnée sur une ou plusieurs pages,
        ou via son sitemap XML si configuré.
        """
        src_cfg = self.get_source(source_name)
        if not src_cfg:
            logger.error("Source inconnue : %s", source_name)
            return []

        # Cas 1 : Source configurée via Sitemap XML (ex: maboursedetudes.com, youthmedia)
        sitemap_url = src_cfg.get("sitemap_url")
        if sitemap_url:
            logger.info("[%s] Découverte via sitemap : %s", source_name, sitemap_url)
            try:
                import requests
                # URLs alternatives si échec de résolution (avec et sans www)
                urls_to_try = [sitemap_url]
                if "://www." in sitemap_url:
                    urls_to_try.append(sitemap_url.replace("://www.", "://"))
                elif "://" in sitemap_url:
                    urls_to_try.append(sitemap_url.replace("://", "://www."))

                resp = None
                for u in urls_to_try:
                    try:
                        resp = requests.get(
                            u,
                            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                            timeout=20
                        )
                        if resp.status_code == 200:
                            break
                    except Exception as err:
                        logger.warning("[%s] Échec sur %s (%s). Essai alternative...", source_name, u, err)

                if resp and resp.status_code == 200:
                    soup_xml = BeautifulSoup(resp.text, "xml")
                    filter_str = src_cfg.get("sitemap_filter", "")
                    sitemap_urls = []
                    for loc in soup_xml.find_all("loc"):
                        url_item = loc.get_text(strip=True)
                        if filter_str and filter_str not in url_item:
                            continue
                        sitemap_urls.append(url_item)

                    max_items = src_cfg.get("max_items", 30)
                    if len(sitemap_urls) > max_items:
                        # Priorité aux URLs les plus récentes (généralement en tête ou queue)
                        sitemap_urls = sitemap_urls[:max_items]

                    logger.info("[%s] %d URLs candidates retenues du sitemap.", source_name, len(sitemap_urls))

                    # Visiter chaque page de détail pour extraire le vrai contenu
                    sitemap_candidates = []
                    detail_title_selector = src_cfg.get("detail_title_selector", "h1, h2")
                    detail_snippet_selector = src_cfg.get("detail_snippet_selector", "article p, .content p, p")
                    detail_deadline_selector = src_cfg.get("detail_deadline_selector", "")

                    for url_item in sitemap_urls:
                        # Fallback : titre depuis le slug si le scraping échoue
                        slug = url_item.rstrip("/").split("/")[-1]
                        fallback_title = slug.replace("-", " ").capitalize()

                        detail_result: ScrapeResult = await scraper.scrape(url_item)

                        if detail_result.success and detail_result.html:
                            detail_soup = BeautifulSoup(detail_result.html, "html.parser")

                            # Titre réel
                            title_elem = detail_soup.select_one(detail_title_selector)
                            title = title_elem.get_text(strip=True) if title_elem else fallback_title

                            # Snippet réel (jusqu'à 3 premières phrases du contenu)
                            snippet = ""
                            for sel in detail_snippet_selector.split(","):
                                snippet_elem = detail_soup.select_one(sel.strip())
                                if snippet_elem:
                                    raw = " ".join(snippet_elem.get_text(" ", strip=True).split())
                                    sentences = re.split(r"(?<=[.!?])\s+", raw)
                                    snippet = " ".join(sentences[:3])
                                    if snippet:
                                        break

                            # Détails structurés (JSON-LD) pour l'enrichissement IA
                            source_details = ""
                            if src_cfg.get("use_jsonld_description"):
                                source_details = self._extract_jsonld_description(detail_soup)

                            # Date limite réelle
                            deadline = None
                            detail_deadline_label = src_cfg.get("detail_deadline_label")
                            if detail_deadline_label:
                                labeled_value = self._extract_labeled_value(detail_soup, detail_deadline_label)
                                if labeled_value:
                                    deadline = (
                                        labeled_value
                                        if parse_date_safely(labeled_value)
                                        else extract_deadline(labeled_value)
                                    )
                            if not deadline and detail_deadline_selector:
                                dl_elem = detail_soup.select_one(detail_deadline_selector)
                                if dl_elem:
                                    deadline = extract_deadline(dl_elem.get_text(strip=True))

                            if not deadline and source_details:
                                # La description structurée mentionne souvent la date limite
                                deadline = extract_deadline(source_details)

                            if not deadline:
                                # Chercher dans tout le texte de la page
                                full_text = detail_soup.get_text(" ", strip=True)
                                deadline = extract_deadline(full_text)

                            logger.info(
                                "[%s] Détail récupéré pour '%s' | deadline=%s | snippet=%d car. | détails=%d car.",
                                source_name, title, deadline or "N/A", len(snippet), len(source_details)
                            )
                        else:
                            logger.warning("[%s] Impossible de récupérer la page de détail : %s", source_name, url_item)
                            title = fallback_title
                            snippet = f"Bourse identifiée dans le catalogue {source_name}."
                            source_details = ""
                            deadline = None

                        c = ScholarshipCandidate(
                            title=title,
                            official_link=url_item,
                            source_site=source_name,
                            discovery_url=sitemap_url,
                            snippet=snippet,
                            source_details=source_details,
                            deadline=deadline,
                            domain_type=detect_domain_type(url_item),
                            status="nouveau"
                        )
                        sitemap_candidates.append(c)

                    logger.info("[%s] %d bourses candidates enrichies depuis le sitemap.", source_name, len(sitemap_candidates))
                    return sitemap_candidates
            except Exception as e:
                logger.error("[%s] Erreur lecture sitemap : %s", source_name, e)
                return []

        # Cas 2 : Scraping standard multi-pages HTML via Crawl4AI
        max_pages = int(src_cfg.get("max_pages", 1))
        page_pattern = src_cfg.get("page_url_pattern", "")
        all_candidates: List[ScholarshipCandidate] = []

        for page in range(1, max_pages + 1):
            if page == 1:
                target_url = src_cfg["url"]
            elif page_pattern:
                target_url = page_pattern.format(page=page)
            else:
                break

            logger.info("[%s] Analyse de la page %d/%d : %s", source_name, page, max_pages, target_url)
            scrape_result: ScrapeResult = await scraper.scrape(target_url)

            if not scrape_result.success:
                logger.warning("[%s] Page %d non récupérée : %s", source_name, page, scrape_result.error_message)
                break

            page_candidates = self.extract_from_html(src_cfg, scrape_result.html, target_url)
            if not page_candidates:
                logger.info("[%s] Aucun candidat sur la page %d, arrêt de la pagination.", source_name, page)
                break

            all_candidates.extend(page_candidates)

        return all_candidates
