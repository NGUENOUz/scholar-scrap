"""
Module storage.py
-----------------
Gestion de la persistance locale via SQLite (fichier data/scholarships.db).
Garantit qu'une bourse déjà signalée ne sera jamais renvoyée une seconde fois.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

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
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS social_posts (
                    scholarship_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    template_type TEXT NOT NULL,
                    deadline TEXT,
                    study_levels TEXT NOT NULL,
                    canva_description TEXT NOT NULL,
                    image_suggestion TEXT NOT NULL,
                    social_text TEXT NOT NULL,
                    public_url TEXT NOT NULL,
                    buffer_status TEXT NOT NULL DEFAULT 'pending',
                    buffer_idea_id TEXT,
                    buffer_error TEXT,
                    buffer_attempted_at TEXT,
                    buffer_idea_created_at TEXT,
                    canva_exported_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
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

    def get_social_post(self, scholarship_id: str) -> Optional[Dict[str, Any]]:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM social_posts WHERE scholarship_id = ?",
                (scholarship_id,)
            ).fetchone()
            return dict(row) if row else None

    def save_social_post(self, post: Dict[str, Any]) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO social_posts (
                    scholarship_id, title, slug, template_type, deadline, study_levels,
                    canva_description, image_suggestion, social_text, public_url,
                    buffer_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """, (
                post["scholarship_id"], post["title"], post["slug"], post["template_type"],
                post["deadline"], post["study_levels"], post["canva_description"],
                post["image_suggestion"], post["social_text"], post["public_url"]
            ))
            return cursor.rowcount == 1

    def get_social_posts_pending_buffer(self, limit: int) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM social_posts
                WHERE buffer_status = 'pending'
                ORDER BY created_at, scholarship_id
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def mark_buffer_idea_creating(self, scholarship_id: str, attempted_at: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE social_posts
                SET buffer_status = 'creating', buffer_error = NULL, buffer_attempted_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE scholarship_id = ? AND buffer_status = 'pending'
            """, (attempted_at, scholarship_id))
            return cursor.rowcount == 1

    def mark_buffer_idea_created(self, scholarship_id: str, idea_id: str, created_at: str) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE social_posts
                SET buffer_status = 'created', buffer_idea_id = ?, buffer_idea_created_at = ?,
                    buffer_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE scholarship_id = ?
            """, (idea_id, created_at, scholarship_id))

    def mark_buffer_idea_failed(self, scholarship_id: str, error: str, uncertain: bool = False) -> None:
        status = "uncertain" if uncertain else "pending"
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE social_posts
                SET buffer_status = ?, buffer_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE scholarship_id = ?
            """, (status, error, scholarship_id))

    def get_social_posts_pending_csv(self, limit: int) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM social_posts
                WHERE canva_exported_at IS NULL AND buffer_status = 'created'
                ORDER BY created_at, scholarship_id
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(row) for row in rows]

    def count_social_posts_pending_csv(self) -> int:
        with self._get_connection() as conn:
            row = conn.execute("""
                SELECT COUNT(*) AS count FROM social_posts
                WHERE canva_exported_at IS NULL
            """).fetchone()
            return int(row["count"])

    def mark_social_posts_csv_exported(self, scholarship_ids: List[str], exported_at: str) -> None:
        with self._get_connection() as conn:
            conn.executemany("""
                UPDATE social_posts
                SET canva_exported_at = ?, updated_at = CURRENT_TIMESTAMP
                WHERE scholarship_id = ?
            """, [(exported_at, scholarship_id) for scholarship_id in scholarship_ids])

    def get_social_posts_buffer_review(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute("""
                SELECT scholarship_id, title, buffer_status, buffer_error
                FROM social_posts
                WHERE buffer_status IN ('creating', 'uncertain')
                ORDER BY created_at
            """).fetchall()
            return [dict(row) for row in rows]

    def reset_social_buffer_for_retry(self, scholarship_id: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.execute("""
                UPDATE social_posts
                SET buffer_status = 'pending', buffer_error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE scholarship_id = ? AND buffer_status IN ('creating', 'uncertain')
            """, (scholarship_id,))
            return cursor.rowcount == 1
