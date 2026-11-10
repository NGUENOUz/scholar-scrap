"""
Modèles de données pour Scholarship Discovery
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ScholarshipCandidate(BaseModel):
    """
    Représente une bourse candidate découverte sur une source.
    """
    title: str = Field(..., description="Titre de la bourse")
    official_link: str = Field(..., description="Lien officiel ou lien final de la bourse")
    source_site: str = Field(..., description="Nom du site source (ex: greatyop)")
    discovery_url: str = Field(..., description="URL de la page où l'article a été découvert")
    detection_date: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"),
        description="Date et heure de détection"
    )
    snippet: str = Field("", description="Extrait court tiré de la page (1 à 3 phrases)")
    deadline: Optional[str] = Field(None, description="Date limite si détectable clairement, sinon vide")
    domain_type: str = Field(
        "Autre",
        description="Indicateur de domaine officiel probable : Université, Gouvernement, Fondation, ou Autre"
    )
    status: str = Field("nouveau", description="Statut : nouveau, déjà vu, expiré")
