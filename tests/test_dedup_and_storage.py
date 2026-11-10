"""
Tests pour dedup.py, storage.py et platform_sync.py
"""

import os
from pathlib import Path
from src.dedup import normalize_url, are_titles_similar
from src.storage import Storage
from src.models import ScholarshipCandidate


def test_normalize_url():
    url1 = "https://www.example.com/bourse/master/?utm_source=fb&utm_medium=cpc#details"
    url2 = "http://example.com/bourse/master"
    assert normalize_url(url1) == "https://example.com/bourse/master"
    assert normalize_url(url2) == "https://example.com/bourse/master"
    assert normalize_url(url1) == normalize_url(url2)


def test_are_titles_similar():
    t1 = "Bourses d'Excellence Eiffel pour Master et Doctorat 2026"
    t2 = "Bourse Eiffel d'excellence pour doctorat et master 2026/2027"
    assert are_titles_similar(t1, t2, threshold=75.0) is True

    t3 = "Bourse Chevening au Royaume-Uni"
    assert are_titles_similar(t1, t3) is False


def test_storage(tmp_path):
    test_db = tmp_path / "test_scholarships.db"
    store = Storage(str(test_db))

    cand = ScholarshipCandidate(
        title="Bourse Test A",
        official_link="https://univ-test.edu/bourse",
        source_site="greatyop",
        discovery_url="https://greatyop.com/category/bourses/",
        domain_type="Université",
        status="nouveau"
    )

    # Première insertion : réussie
    assert store.save_candidate(cand) is True

    # Doublon direct par URL : rejeté
    assert store.save_candidate(cand) is False

    # Doublon par vérification d'URL
    assert store.is_url_seen("https://www.univ-test.edu/bourse?utm_source=twitter") is True

    # Doublon par titre similaire
    assert store.is_title_similar_seen("Bourse Test A 2026") is True

    # Récupération
    items = store.get_candidates()
    assert len(items) == 1
    assert items[0].title == "Bourse Test A"
