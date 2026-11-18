"""
Module enrichment_queue.py
--------------------------
Gère la file d'attente persistante (data/pending_enrichment.json)
pour les bourses dont l'enrichissement IA (Gemini) a échoué
(ex: quota 429 épuisé, timeout, indisponibilité de modèle).

Au prochain démarrage, le service dépile en priorité cette file d'attente
dès que les quotas sont à nouveau disponibles, et insère les brouillons complets.
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

from src.models import ScholarshipCandidate
from src.dedup import clean_title, normalize_url, are_titles_similar

logger = logging.getLogger("EnrichmentQueue")

DEFAULT_QUEUE_PATH = Path("data/pending_enrichment.json")


class EnrichmentQueue:
    """Gestionnaire de file d'attente persistante pour l'enrichissement IA."""

    def __init__(self, queue_path: Path = DEFAULT_QUEUE_PATH):
        self.queue_path = Path(queue_path)
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.queue_path.exists():
            try:
                with open(self.queue_path, "w", encoding="utf-8") as f:
                    json.dump([], f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.error("Impossible d'initialiser le fichier de file d'attente : %s", e)

    def _load_queue(self) -> List[Dict[str, Any]]:
        if not self.queue_path.exists():
            return []
        try:
            with open(self.queue_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception as e:
            logger.error("Erreur lors de la lecture de la file d'attente : %s", e)
            return []

    def _save_queue(self, items: List[Dict[str, Any]]) -> None:
        try:
            temp_path = self.queue_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
            temp_path.replace(self.queue_path)
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement de la file d'attente : %s", e)

    def count(self) -> int:
        """Retourne le nombre de bourses en attente dans la file."""
        return len(self._load_queue())

    def get_all(self) -> List[Dict[str, Any]]:
        """Retourne tous les éléments en attente."""
        return self._load_queue()

    def is_queued(self, title: str, official_link: str = "") -> bool:
        """Vérifie si une bourse est déjà dans la file d'attente."""
        norm_url = normalize_url(official_link) if official_link else ""
        items = self._load_queue()
        for item in items:
            cand = item.get("candidate", {})
            if norm_url and normalize_url(cand.get("official_link", "")) == norm_url:
                return True
            if are_titles_similar(title, cand.get("title", ""), threshold=85.0):
                return True
        return False

    def enqueue(
        self,
        candidate: ScholarshipCandidate,
        scraped_context: str = "",
        country: str = "International",
        deadline: Optional[str] = None,
        reason: str = "quota_exhausted"
    ) -> bool:
        """
        Ajoute une bourse à la file d'attente si elle n'y figure pas déjà.
        """
        if self.is_queued(candidate.title, candidate.official_link):
            logger.debug("Bourse déjà dans la file d'attente : '%s'", candidate.title)
            return False

        items = self._load_queue()
        queue_item = {
            "candidate": candidate.model_dump(),
            "scraped_context": (scraped_context or "")[:25000],
            "country": country,
            "deadline": deadline,
            "queued_at": datetime.now().isoformat(),
            "reason": reason,
            "retry_count": 0
        }
        items.append(queue_item)
        self._save_queue(items)
        logger.info("Bourse ajoutée à la file d'attente IA ('%s' - Motif: %s)", candidate.title, reason)
        return True

    def remove(self, title: str, official_link: str = "") -> bool:
        """
        Retire une bourse de la file d'attente après traitement réussi.
        """
        items = self._load_queue()
        norm_url = normalize_url(official_link) if official_link else ""
        initial_len = len(items)

        filtered = []
        for item in items:
            cand = item.get("candidate", {})
            cand_url = normalize_url(cand.get("official_link", ""))
            cand_title = cand.get("title", "")

            if norm_url and cand_url == norm_url:
                continue
            if are_titles_similar(title, cand_title, threshold=85.0):
                continue
            filtered.append(item)

        if len(filtered) < initial_len:
            self._save_queue(filtered)
            logger.info("Bourse retirée de la file d'attente : '%s'", title)
            return True
        return False

    def increment_retry(self, official_link: str) -> None:
        """Incrémente le compteur de tentatives pour une bourse."""
        items = self._load_queue()
        norm_url = normalize_url(official_link)
        for item in items:
            cand = item.get("candidate", {})
            if normalize_url(cand.get("official_link", "")) == norm_url:
                item["retry_count"] = item.get("retry_count", 0) + 1
                item["last_attempt"] = datetime.now().isoformat()
                break
        self._save_queue(items)
