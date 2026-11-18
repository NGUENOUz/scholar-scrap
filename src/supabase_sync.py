"""
Module supabase_sync.py
-----------------------
Gestionnaire d'importation des nouvelles bourses dans Supabase (DrenoLearn).
Insère chaque nouvelle opportunité avec le statut brouillon (is_active = False)
afin que l'administrateur n'ait plus qu'à vérifier et publier en un clic.
"""

import os
import re
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from supabase import create_client, Client

from src.models import ScholarshipCandidate
from src.dedup import clean_title
from src.sources import parse_date_safely

logger = logging.getLogger("SupabaseSync")

# Détection heuristique de pays courants (le pays explicitement étiqueté est prioritaire)
COUNTRIES_MAP = {
    "afrique du sud": "Afrique du Sud",
    "côte d'ivoire": "Côte d'Ivoire", "cote d'ivoire": "Côte d'Ivoire",
    "arabie saoudite": "Arabie saoudite",
    "états-unis": "États-Unis", "etats-unis": "États-Unis", "usa": "États-Unis",
    "américain": "États-Unis", "americain": "États-Unis",
    "royaume-uni": "Royaume-Uni", "angleterre": "Royaume-Uni", "britannique": "Royaume-Uni", "uk": "Royaume-Uni",
    "pays-bas": "Pays-Bas", "pays bas": "Pays-Bas",
    "nouvelle-zélande": "Nouvelle-Zélande", "nouvelle-zelande": "Nouvelle-Zélande",
    "france": "France", "français": "France", "francais": "France",
    "suisse": "Suisse", "canada": "Canada", "canadien": "Canada",
    "allemagne": "Allemagne", "allemand": "Allemagne",
    "chine": "Chine", "chinois": "Chine",
    "japon": "Japon", "japonais": "Japon",
    "corée": "Corée du Sud", "coree": "Corée du Sud", "korea": "Corée du Sud",
    "turquie": "Turquie", "turc": "Turquie",
    "italie": "Italie", "italien": "Italie",
    "espagne": "Espagne", "espagnol": "Espagne",
    "belgique": "Belgique", "belge": "Belgique",
    "finlande": "Finlande", "finlandais": "Finlande",
    "roumanie": "Roumanie", "roumain": "Roumanie",
    "maroc": "Maroc", "marocain": "Maroc",
    "slovaquie": "Slovaquie",
    "autriche": "Autriche", "autrichien": "Autriche",
    "oman": "Oman", "singapour": "Singapour", "malaisie": "Malaisie",
    "australie": "Australie", "australien": "Australie",
    "bénin": "Bénin", "benin": "Bénin",
    "brésil": "Brésil", "bresil": "Brésil",
    "bulgarie": "Bulgarie",
    "burkina": "Burkina Faso",
    "colombie": "Colombie",
    "danemark": "Danemark",
    "ghana": "Ghana", "ghanéen": "Ghana",
    "irlande": "Irlande", "irlandais": "Irlande",
    "kenya": "Kenya", "kénya": "Kenya",
    "maurice": "Maurice", "mauricien": "Maurice",
    "nigeria": "Nigeria", "nigérian": "Nigeria",
    "ouganda": "Ouganda",
    "pakistan": "Pakistan", "pakistanais": "Pakistan",
    "portugal": "Portugal", "portugais": "Portugal",
    "qatar": "Qatar",
    "russie": "Russie", "russe": "Russie",
    "rwanda": "Rwanda",
    "sénégal": "Sénégal", "senegal": "Sénégal", "sénégalais": "Sénégal",
    "suède": "Suède", "suede": "Suède", "suédois": "Suède",
    "tchéquie": "Tchéquie", "republique tcheque": "Tchéquie",
    "thaïlande": "Thaïlande", "thailande": "Thaïlande",
    "togo": "Togo", "togolais": "Togo",
    "égypte": "Égypte", "egypte": "Égypte",
    "inde": "Inde", "indien": "Inde",
    "vietnam": "Vietnam",
}

COUNTRY_LABEL_PATTERN = re.compile(
    r"pays\s+d[’']accueil\s*[:\-]\s*([A-Za-zÀ-ÿ'’\-. ]{3,40})",
    re.IGNORECASE
)


DETAIL_FIELDS = (
    "description", "montant", "type_financement", "niveau_etudes", "domaine_etudes",
    "criteres", "avantages", "couverture", "documents_requis", "processus", "filieres"
)


def _is_missing_detail(value: Any) -> bool:
    return value is None or value == "" or value == [] or (
        isinstance(value, str) and value.lower().startswith("non précisé")
    )


def slugify(text: str) -> str:
    """Génère un slug propre pour l'URL de la bourse."""
    cleaned = clean_title(text)
    slug = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return slug[:80] if slug else "bourse-decouverte"


def extract_country(text: str) -> str:
    """
    Détecte le pays hôte.
    Priorité au libellé explicite « Pays d'accueil : ... » (le plus fiable),
    puis balayage des noms de pays connus dans le texte.
    """
    if not text:
        return "International"

    label = COUNTRY_LABEL_PATTERN.search(text)
    if label:
        label_text = re.split(r"[,;(\-–/]", label.group(1))[0].strip(" .")
        label_lower = label_text.lower()
        for key, country in COUNTRIES_MAP.items():
            if re.search(rf"\b{re.escape(key)}\b", label_lower):
                return country

    t_lower = text.lower()
    for key, country in COUNTRIES_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", t_lower):
            return country
    return "International"


def is_deadline_estimated(deadline_text: Optional[str]) -> bool:
    """
    Indique si la date limite n'a pas pu être trouvée avec certitude
    et nécessite une vérification manuelle par l'administrateur.
    """
    if not deadline_text:
        return True
    lower = deadline_text.lower()
    if re.search(r"(\d+)\s*jours?\s*restant", lower):
        return False
    return parse_date_safely(deadline_text) is None


def format_sql_date(deadline_text: Optional[str]) -> str:
    """
    Formate une date limite au format SQL YYYY-MM-DD.
    Respecte la contrainte NOT NULL de la table.
    """
    if deadline_text:
        # Cas 1 : "X jours restants"
        m_days = re.search(r"(\d+)\s*jours?\s*restant", deadline_text.lower())
        if m_days:
            days = int(m_days.group(1))
            target_date = datetime.now() + timedelta(days=days)
            return target_date.strftime("%Y-%m-%d")

        # Cas 2 : Date textuelle FR ou EN
        parsed = parse_date_safely(deadline_text)
        if parsed:
            return parsed.strftime("%Y-%m-%d")

    # Fallback par défaut : dans 90 jours
    fallback_date = datetime.now() + timedelta(days=90)
    return fallback_date.strftime("%Y-%m-%d")


class SupabaseBourseImporter:
    """Importateur de bourses en mode brouillon dans la table bourses de Supabase."""

    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL", "https://mlztlexcgdhgnnmkuioc.supabase.co")
        # Clé service role (requise pour contourner la RLS et insérer depuis le backend)
        self.service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")
        self.client: Optional[Client] = None
        self._init_client()

    def _init_client(self):
        if self.supabase_url and self.service_key:
            try:
                self.client = create_client(self.supabase_url, self.service_key)
                logger.info("Connexion Supabase initialisée vers %s.", self.supabase_url)
            except Exception as e:
                logger.error("Erreur lors de l'initialisation du client Supabase : %s", e)

    def get_incomplete_drafts(self, limit: int = 20) -> list[Dict[str, Any]]:
        """Retourne au maximum ``limit`` brouillons ayant des champs de détail vides."""
        if not self.client:
            logger.warning("Supabase non connecté. Recherche des brouillons ignorée.")
            return []

        fields = ",".join(("id", "titre", "organisme", "pays", "date_limite", "lien_officiel", "is_active", *DETAIL_FIELDS))
        try:
            response = self.client.table("bourses").select(fields).eq("is_active", False).limit(500).execute()
            rows = response.data or []
            incomplete = [row for row in rows if any(_is_missing_detail(row.get(field)) for field in DETAIL_FIELDS)]
            return incomplete[:max(0, limit)]
        except Exception as e:
            logger.error("Impossible de récupérer les brouillons incomplets : %s", e)
            return []

    def update_incomplete_draft(self, bourse_id: Any, enriched_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Remplit uniquement les champs encore vides d'un brouillon inactif."""
        if not self.client:
            return None

        try:
            current = self.client.table("bourses").select(",".join(("id", "is_active", *DETAIL_FIELDS))) \
                .eq("id", bourse_id).eq("is_active", False).limit(1).execute()
            if not current.data:
                return None

            row = current.data[0]
            payload = {
                field: enriched_data[field]
                for field in DETAIL_FIELDS
                if _is_missing_detail(row.get(field))
                and field in enriched_data
                and not _is_missing_detail(enriched_data[field])
            }
            if not payload:
                return None

            response = self.client.table("bourses").update(payload).eq("id", bourse_id).eq("is_active", False).execute()
            return response.data[0] if response.data else None
        except Exception as e:
            logger.error("Échec de mise à jour des détails du brouillon %s : %s", bourse_id, e)
            return None

    def fetch_all_scholarship_identifiers(self) -> Dict[str, Any]:
        """
        Récupère tous les identifiants de bourses (URLs officielles, slugs, titres)
        séparés par statut (publié vs brouillon) pour un dédoublonnage infaillible.
        """
        result = {
            "published_urls": set(),
            "draft_urls": set(),
            "published_slugs": set(),
            "draft_slugs": set(),
            "published_titles": [],
            "draft_titles": [],
        }
        if not self.client:
            return result

        try:
            fields = "id, titre, slug, lien_officiel, is_active"
            response = self.client.table("bourses").select(fields).limit(2000).execute()
            rows = response.data or []
            from src.dedup import normalize_url
            for r in rows:
                is_pub = bool(r.get("is_active"))
                url = normalize_url(r.get("lien_officiel") or "")
                slug = (r.get("slug") or "").strip()
                title = (r.get("titre") or "").strip()

                if url:
                    if is_pub:
                        result["published_urls"].add(url)
                    else:
                        result["draft_urls"].add(url)

                if slug:
                    if is_pub:
                        result["published_slugs"].add(slug)
                    else:
                        result["draft_slugs"].add(slug)

                if title:
                    if is_pub:
                        result["published_titles"].append(title)
                    else:
                        result["draft_titles"].append(title)

            logger.info(
                "Supabase identifiants chargés : %d bourses publiées, %d brouillons.",
                len(result["published_titles"]),
                len(result["draft_titles"])
            )
        except Exception as e:
            logger.error("Erreur lors de la récupération des bourses Supabase pour dédoublonnage : %s", e)

        return result

    def insert_as_draft(
        self,
        candidate: ScholarshipCandidate,
        official_url: str,
        enriched_data: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Insère une bourse découverte directement dans public.bourses avec is_active = False (Brouillon).
        RÈGLES STRICTES :
        1. Ne JAMAIS écraser ou rétrograder une bourse déjà publiée (is_active = True).
        2. Si la bourse existe déjà en brouillon, met à jour uniquement les champs incomplets.
        3. Si la date limite est estimée/introuvable, signale clairement le besoin de saisie manuelle.
        """
        if not self.client:
            logger.warning("Supabase non connecté (SUPABASE_SERVICE_ROLE_KEY manquante). Import sauté.")
            return None

        # Données de vérité terrain verrouillées
        base_slug = slugify(candidate.title)
        pays = extract_country(
            " ".join(
                part for part in (candidate.title, candidate.snippet, candidate.source_details) if part
            )
        )
        date_limite = format_sql_date(candidate.deadline)
        is_estimated = is_deadline_estimated(candidate.deadline)

        # Récupération des champs IA ou valeurs par défaut
        enriched = enriched_data or {}

        organisme = enriched.get("organisme") or (
            candidate.domain_type if candidate.domain_type != "Autre" else "Organisme Officiel"
        )
        description_text = enriched.get("description") or candidate.snippet or (
            f"Bourse d'études '{candidate.title}' découverte automatiquement. "
            "En attente de relecture et de finalisation par l'administrateur."
        )

        # Si la date limite n'a pas pu être trouvée avec certitude, avertir visiblement
        if is_estimated:
            description_text = f"⚠️ [DATE LIMITE À RENSEIGNER MANUELLEMENT]\n\n{description_text}"

        payload = {
            "slug": base_slug,
            "titre": candidate.title,
            "organisme": organisme,
            "pays": pays,
            "description": description_text,
            "montant": enriched.get("montant", ""),
            "type_financement": enriched.get("type_financement", "Entièrement financé"),
            "niveau_etudes": enriched.get("niveau_etudes", ["Master"]),
            "domaine_etudes": enriched.get("domaine_etudes", ["Toutes filières"]),
            "criteres": enriched.get("criteres", []),
            "avantages": enriched.get("avantages", []),
            "couverture": enriched.get("couverture", []),
            "documents_requis": enriched.get("documents_requis", []),
            "processus": enriched.get("processus", []),
            "filieres": enriched.get("filieres", []),
            "date_limite": date_limite,
            "lien_officiel": official_url,
            "is_active": False  # BROUILLON (en attente de validation)
        }

        try:
            # 1. Vérifier si existant par slug
            existing = self.client.table("bourses").select("id, is_active, titre").eq("slug", base_slug).execute()
            # 2. Si non trouvé par slug, vérifier par lien officiel
            if not existing.data and official_url:
                existing = self.client.table("bourses").select("id, is_active, titre").eq("lien_officiel", official_url).execute()

            if existing.data:
                existing_row = existing.data[0]
                bourse_id = existing_row["id"]
                # SÉCURITÉ ABSOLUE : Si déjà active/publiée, interdiction formelle d'y toucher !
                if existing_row.get("is_active"):
                    logger.info("Bourse '%s' déjà PUBLIÉE sur DrenoLearn (ID: %s). Insertion ignorée.", candidate.title, bourse_id)
                    return None

                # Si déjà présente en brouillon, compléter uniquement les champs manquants
                logger.info("Bourse '%s' déjà présente en brouillon (ID: %s). Complétion des champs vides.", candidate.title, bourse_id)
                return self.update_incomplete_draft(bourse_id, enriched)

            # 3. Insertion nouvelle bourse en brouillon
            res = self.client.table("bourses").insert(payload).execute()
            if res.data:
                inserted = res.data[0]
                logger.info("Bourse insérée en brouillon sur DrenoLearn (ID: %s, Slug: %s)", inserted.get("id"), payload["slug"])
                return inserted
        except Exception as e:
            logger.error("Échec de l'insertion en brouillon pour '%s' : %s", candidate.title, e)

        return None
