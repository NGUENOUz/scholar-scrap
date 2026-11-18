"""
Module ai_enricher.py
---------------------
Enrichit les nouvelles bourses candidates via l'API Google Gemini.
RÈGLE D'OR :
- Les données vérifiées par le scraper (Titre, Vrai Lien Officiel, Pays d'accueil, Date limite)
  sont STRICTEMENT VERROUILLÉES et ne sont JAMAIS modifiées par l'IA.
- L'IA n'intervient que pour compléter avec pertinence les champs détaillés :
  organisme exact, montant, description complète, critères, avantages, couverture,
  documents requis, processus et filières.
"""

import os
import json
import re
import logging
from typing import Dict, Any, Optional
from google import genai
from google.genai import types

logger = logging.getLogger("AIEnricher")

ALLOWED_FINANCEMENT = ["Entièrement financé", "Partiellement financé", "Non financé"]
ALLOWED_NIVEAUX = ["Licence", "Master", "Doctorat", "Tous niveaux"]
GEMINI_TIMEOUT_MS = 20_000


class AIEnricher:
    """Enrichisseur de fiches de bourses via Gemini."""

    def __init__(self):
        # Charger toutes les clés API Gemini disponibles pour bascule automatique
        self.api_keys = []
        for key_name in ["GEMINI_API_KEY", "GEMINI_API_KEY2", "GEMINI_API_KEY_BACKUP"]:
            val = os.getenv(key_name)
            if val and val.strip() and val.strip() not in self.api_keys:
                self.api_keys.append(val.strip())

        self.clients: list[genai.Client] = []
        for idx, key in enumerate(self.api_keys, 1):
            try:
                c = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(
                        timeout=GEMINI_TIMEOUT_MS,
                        retry_options=types.HttpRetryOptions(attempts=1)
                    )
                )
                self.clients.append(c)
            except Exception as e:
                logger.error("Erreur d'initialisation de la clé Gemini #%d : %s", idx, e)

        self.client: Optional[genai.Client] = self.clients[0] if self.clients else None
        self.is_quota_exhausted: bool = False
        self.last_failure_reason: Optional[str] = None

        if self.clients:
            logger.info("Module AI Enricher initialisé avec %d clé(s) API Gemini configurée(s).", len(self.clients))
        else:
            logger.warning("Aucune clé GEMINI_API_KEY trouvée. L'enrichissement IA sera désactivé.")

    def enrich(
        self,
        title: str,
        official_link: str,
        country: str,
        deadline: str,
        scraped_context: str = "",
        model_names: Optional[list[str]] = None
    ) -> Dict[str, Any]:
        """
        Complète les informations détaillées d'une bourse en verrouillant les données terrain.
        """
        clients = getattr(self, "clients", None) or ([self.client] if getattr(self, "client", None) else [])
        if not clients:
            logger.info("Enrichissement IA sauté (aucune clé configurée).")
            return {}

        prompt = f"""Tu es un expert senior spécialisé dans les bourses d'études universitaires internationales pour la plateforme DrenoLearn.
Ton objectif est de produire une fiche d'opportunité d'études d'une qualité exceptionnelle, riche, complète et prête à être publiée (comme sur les meilleures fiches de DrenoLearn).

Voici les INFORMATIONS VÉRIFIÉES SUR LE TERRAIN que tu dois STRICTEMENT RESPECTER :
- Nom exact du programme : "{title}"
- Vrai lien officiel : {official_link}
- Pays d'accueil vérifié : {country}
- Date limite de candidature vérifiée : {deadline}
- Contexte extrait de la page source : {scraped_context or "Non précisé"}

IMPORTANT - DIRECTIVES D'EXCELLENCE :
1. Ne JAMAIS modifier le pays d'accueil ni la date limite vérifiée ci-dessus.
2. N'invente aucun montant, critère farfelu ou mensonger : appuie-toi en priorité sur les faits du contexte, et complète intelligemment avec ton expertise approfondie des bourses universitaires internationales et des standards académiques pour fournir une fiche complète, dense et attractive.
3. RÈGLE D'OR : Les listes (criteres, avantages, couverture, documents_requis, processus, filieres) ne doivent JAMAIS être vides. Fournis pour chacune 3 à 4 éléments précis, professionnels et réalistes.
4. Rédige TOUT en FRANÇAIS soigné, clair, direct et valorisant pour les étudiants et chercheurs.

Règles de rédaction des champs :
- "description" : 3 à 4 phrases denses et percutantes. Présente l'organisme hôte, les objectifs du programme, le profil recherché et les opportunités académiques et professionnelles offertes.
- "organisme" : Le nom officiel et précis de l'université, institut, fondation ou ministère (ex: 'Alexander von Humboldt Institute for Internet and Society', 'Politecnico di Milano', 'Consortium Erasmus Mundus').
- "montant" : Précise le montant exact s'il apparaît dans le contexte (ex: '5 000 €', '10 000 €/an', '1 000 €/mois'), ou résume la prise en charge (ex: 'Exonération complète des frais de scolarité et allocation de subsistance').
- "type_financement" : "Entièrement financé" si tous les frais majeurs sont couverts, sinon "Partiellement financé".
- "niveau_etudes" : Liste parmi : ["Licence", "Master", "Doctorat", "Tous niveaux"].
- "domaine_etudes" : 2 à 4 grands domaines académiques généraux (ex: ["Intelligence Artificielle", "Informatique", "Sciences Sociales"]).
- "criteres" : Fournis 3 à 4 critères d'éligibilité concrets et bien formulés (niveau de diplôme préalable requis, statut d'étudiant international/chercheur, compétences linguistiques comme anglais B2/C1 ou français, excellence du dossier académique).
- "avantages" : Fournis 3 à 4 avantages majeurs concrets (allocation financière, expérience de recherche ou d'études internationale, accès à un réseau d'experts et d'alumni, encadrement de haut niveau).
- "couverture" : 2 à 3 postes pris en charge selon le type de financement (frais de scolarité, allocation mensuelle ou bourse forfaitaire, participation aux frais de séjour ou de déplacement).
- "documents_requis" : Fournis 3 à 4 documents indispensables pour candidater (Curriculum Vitae actualisé au format international, lettre de motivation ou projet de recherche, relevés de notes et diplômes certifiés, lettres de recommandation académiques).
- "processus" : Décris 3 étapes claires et chronologiques pour postuler (1. Consulter les directives officielles et vérifier l'éligibilité, 2. Rassembler l'ensemble des pièces requises, 3. Soumettre la candidature en ligne sur le portail officiel avant la date limite).
- "filieres" : 3 à 5 mentions ou spécialités précises adaptées au programme (ex: ["Intelligence Artificielle", "Éthique du numérique", "Science des données", "Gouvernance de l'information"]).
- "date_limite_detectee" : Si une date limite apparaît clairement dans le contexte, indique-la (format YYYY-MM-DD ou textuel), sinon indique null.

Retourne UNIQUEMENT un objet JSON valide, sans balises markdown (pas de ```json), avec cette structure exacte :
{{
  "organisme": "Nom officiel de l'organisme ou université",
  "description": "Description détaillée et engageante de 3 à 4 phrases...",
  "montant": "Montant indicatif ou prise en charge",
  "type_financement": "Partiellement financé",
  "niveau_etudes": ["Master"],
  "domaine_etudes": ["Technologies", "Sciences Sociales"],
  "criteres": [
    "Être titulaire d'un diplôme universitaire pertinent...",
    "Démontrer un excellent dossier académique ou professionnel...",
    "Avoir une bonne maîtrise de l'anglais..."
  ],
  "avantages": [
    "Bourse financière pour soutenir les travaux de recherche...",
    "Intégration au sein d'un réseau international d'experts...",
    "Accès aux ressources et événements du programme..."
  ],
  "couverture": [
    "Indemnité forfaitaire de recherche...",
    "Prise en charge des frais de participation aux ateliers..."
  ],
  "documents_requis": [
    "Curriculum Vitae détaillé et à jour",
    "Lettre de motivation présentant le projet ou l'intérêt",
    "Copies des diplômes et relevés de notes",
    "Deux lettres de recommandation"
  ],
  "processus": [
    "Vérifier les conditions d'éligibilité sur le site officiel",
    "Préparer l'ensemble des pièces justificatives demandées",
    "Soumettre la candidature complète via le formulaire en ligne avant la clôture"
  ],
  "filieres": ["Spécialité 1", "Spécialité 2", "Spécialité 3"],
  "date_limite_detectee": null
}}"""

        logger.info("Appel Gemini pour enrichir : '%s'...", title)
        # Ordre de priorité :
        # 1. gemini-flash-latest   → quota souple, stable
        # 2. gemini-3.5-flash-lite → nouveau modèle léger recommandé par Google
        # 3. gemini-2.5-flash      → quota Free Tier limité (20 req/jour), en dernier recours
        models_to_try = model_names or ["gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-2.5-flash"]
        response = None
        has_quota_error = False

        # Essayer chaque client / clé API configurée successivement
        for client_idx, cl in enumerate(clients):
            self.client = cl
            client_had_quota_error = False

            for mod in models_to_try:
                try:
                    response = cl.models.generate_content(
                        model=mod,
                        contents=prompt
                    )

                    if response and response.text:
                        self.last_failure_reason = None
                        break

                except Exception as e:
                    err_str = str(e)
                    # Quota journalier ou minute épuisé
                    if "RESOURCE_EXHAUSTED" in err_str or "429" in err_str:
                        logger.warning(
                            "Quota épuisé pour %s sur clé Gemini #%d (bourse: '%s').",
                            mod, client_idx + 1, title
                        )
                        client_had_quota_error = True
                        has_quota_error = True
                        continue
                    # Service momentanément indisponible
                    elif "503" in err_str or "UNAVAILABLE" in err_str:
                        logger.warning(
                            "Modèle %s indisponible pour '%s'. Passage au modèle suivant.", mod, title
                        )
                        continue
                    # Modèle inexistant/supprimé
                    elif "404" in err_str or "NOT_FOUND" in err_str:
                        logger.warning(
                            "Modèle %s introuvable. Passage au modèle suivant.", mod
                        )
                        continue
                    else:
                        logger.warning("Échec avec %s pour '%s' : %s. Essai suivant...", mod, title, e)

            if response and response.text:
                break

            if client_had_quota_error and client_idx + 1 < len(self.clients):
                logger.info(
                    "🔄 Bascule automatique vers la clé API Gemini #%d pour '%s'...",
                    client_idx + 2, title
                )

        if not response or not response.text:
            if has_quota_error:
                self.is_quota_exhausted = True
                self.last_failure_reason = "quota_exhausted"
            else:
                self.last_failure_reason = "model_unavailable"
            logger.error("Aucun modèle Gemini n'a pu répondre pour '%s' (Raison: %s)", title, self.last_failure_reason)
            return {}

        try:
            raw_text = response.text or ""
            clean_text = raw_text.replace("```json", "").replace("```", "").strip()

            # Extraction du JSON via Regex au cas où Gemini ajoute du texte avant/après
            match = re.search(r"\{[\s\S]*\}", clean_text)
            if not match:
                self.last_failure_reason = "invalid_json"
                logger.error("Réponse Gemini non-JSON reçue pour '%s'", title)
                return {}

            data = json.loads(match.group(0))

            # Validation et normalisation des valeurs strictes
            if data.get("type_financement") not in ALLOWED_FINANCEMENT:
                data["type_financement"] = "Entièrement financé"

            if isinstance(data.get("niveau_etudes"), list):
                valid_niveaux = [n for n in data["niveau_etudes"] if n in ALLOWED_NIVEAUX]
                data["niveau_etudes"] = valid_niveaux if valid_niveaux else ["Master"]
            else:
                data["niveau_etudes"] = ["Master"]

            # Garantir les listes
            for field in ["domaine_etudes", "criteres", "avantages", "couverture", "documents_requis", "processus", "filieres"]:
                if not isinstance(data.get(field), list):
                    data[field] = []

            logger.info("Enrichissement IA réussi pour '%s' (Organisme: %s)", title, data.get("organisme"))
            return data

        except Exception as e:
            self.last_failure_reason = f"exception_{e}"
            logger.error("Erreur lors du décodage de la réponse Gemini pour '%s' : %s", title, e)
            return {}
