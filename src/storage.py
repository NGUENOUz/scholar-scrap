"""
Module storage.py
-----------------
Gestion de la persistance locale via SQLite (fichier data/scholarships.db).
Garantit qu'une bourse déjà signalée ne sera jamais renvoyée une seconde fois.
"""

import sqlite3
import logging
from pathlib import Path
from typing import List, Optional

from src.models import ScholarshipCandidate
from src.dedup import normalize_url, clean_title, are_titles_similar

logger = logging.getLogger("Storage")

DEFAULT_DB_PATH = "data/scholarships.db"


class Storage:
    """Gestionnaire de base de données SQLite."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self) -> None:
        """Initialise la table si elle n'existe pas encore."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    clean_title TEXT NOT NULL,
                    normalized_url TEXT UNIQUE NOT NULL,
                    official_link TEXT NOT NULL,
                    source_site TEXT NOT NULL,
                    discovery_url TEXT NOT NULL,
                    detection_date TEXT NOT NULL,
                    snippet TEXT,
                    deadline TEXT,
                    domain_type TEXT,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_normalized_url ON candidates(normalized_url);
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_status ON candidates(status);
            """)
            conn.commit()

    def is_url_seen(self, url: str) -> bool:
        """Vérifie si cette URL normalisée existe déjà dans la base."""
        norm_url = normalize_url(url)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM candidates WHERE normalized_url = ? LIMIT 1", (norm_url,))
            return cursor.fetchone() is not None

    def is_title_similar_seen(self, title: str, threshold: float = 85.0) -> bool:
        """Vérifie si un titre très similaire est déjà présent en base."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT title FROM candidates")
            rows = cursor.fetchall()
            for row in rows:
                if are_titles_similar(title, row["title"], threshold=threshold):
                    return True
        return False

    def save_candidate(self, candidate: ScholarshipCandidate) -> bool:
        """
        Enregistre un candidat.
        Retourne True s'il a été inséré (nouveau), ou False s'il s'agit d'un doublon.
        """
        norm_url = normalize_url(candidate.official_link)
        cl_title = clean_title(candidate.title)

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO candidates (
                        title, clean_title, normalized_url, official_link,
                        source_site, discovery_url, detection_date,
                        snippet, deadline, domain_type, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    candidate.title,
                    cl_title,
                    norm_url,
                    candidate.official_link,
                    candidate.source_site,
                    candidate.discovery_url,
                    candidate.detection_date,
                    candidate.snippet,
                    candidate.deadline,
                    candidate.domain_type,
                    candidate.status
                ))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            # Doublon d'URL (contrainte UNIQUE)
            return False
        except Exception as e:
            logger.error("Erreur lors de l'enregistrement de %s : %s", candidate.title, e)
            return False

    def get_candidates(self, status: Optional[str] = None, limit: int = 100) -> List[ScholarshipCandidate]:
        """Récupère la liste des candidats enregistrés."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT * FROM candidates WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (status, limit)
                )
            else:
                cursor.execute("SELECT * FROM candidates ORDER BY id DESC LIMIT ?", (limit,))

            rows = cursor.fetchall()
            candidates = []
            for r in rows:
                c = ScholarshipCandidate(
                    title=r["title"],
                    official_link=r["official_link"],
                    source_site=r["source_site"],
                    discovery_url=r["discovery_url"],
                    detection_date=r["detection_date"],
                    snippet=r["snippet"] or "",
                    deadline=r["deadline"],
                    domain_type=r["domain_type"] or "Autre",
                    status=r["status"]
                )
                candidates.append(c)
            return candidates
