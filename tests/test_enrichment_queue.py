"""
Tests unitaires pour la file d'attente d'enrichissement IA (EnrichmentQueue).
"""

from pathlib import Path
from src.enrichment_queue import EnrichmentQueue
from src.models import ScholarshipCandidate


def test_enrichment_queue_lifecycle(tmp_path):
    queue_file = tmp_path / "pending_enrichment.json"
    queue = EnrichmentQueue(queue_path=queue_file)

    assert queue.count() == 0

    candidate1 = ScholarshipCandidate(
        title="Bourse d'Excellence Master 2026",
        official_link="https://univ-paris.fr/bourse",
        source_site="greatyop",
        discovery_url="https://greatyop.com/bourse-paris"
    )

    # 1. Ajout dans la file
    added = queue.enqueue(
        candidate=candidate1,
        scraped_context="Détails du programme...",
        country="France",
        deadline="2026-06-30",
        reason="quota_exhausted"
    )
    assert added is True
    assert queue.count() == 1

    # 2. Doublon évité
    added_again = queue.enqueue(
        candidate=candidate1,
        reason="quota_exhausted"
    )
    assert added_again is False
    assert queue.count() == 1

    # 3. Vérification de la présence
    assert queue.is_queued("Bourse d'Excellence Master 2026", "https://univ-paris.fr/bourse") is True
    assert queue.is_queued("Autre Bourse Inconnue") is False

    # 4. Retrait après traitement
    removed = queue.remove("Bourse d'Excellence Master 2026", "https://univ-paris.fr/bourse")
    assert removed is True
    assert queue.count() == 0
