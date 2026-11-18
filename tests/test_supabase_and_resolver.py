"""
Tests pour link_resolver.py et supabase_sync.py
"""

from datetime import datetime
from unittest.mock import Mock

from src import link_resolver
from src.supabase_sync import slugify, extract_country, format_sql_date


def test_slugify():
    slug = slugify("Bourse d'Excellence Eiffel pour Master & Doctorat 2026/2027 !")
    assert "bourse-d-excellence-eiffel" in slug or "bourse-excellence-eiffel" in slug
    assert not any(c in slug for c in ["!", "&", "/", "?"])


def test_extract_country():
    assert extract_country("Bourse du Gouvernement Français pour étudiants") == "France"
    assert extract_country("Study in Canada - Fully Funded Scholarship") == "Canada"
    assert extract_country("Bourse d'études en Suisse à Lausanne") == "Suisse"
    assert extract_country("Global Leadership Program") == "International"


def test_extract_country_prefers_explicit_label():
    # Le libellé « Pays d'accueil » doit primer sur un adjectif ambigu du texte.
    text = "Programme entièrement en français\n- Pays d'accueil : Canada, Université d'Ottawa"
    assert extract_country(text) == "Canada"

    # Nouveaux pays couverts (catalogue youthmedia)
    assert extract_country("- Pays d'accueil : Ghana") == "Ghana"
    assert extract_country("Bourse d'études au Rwanda pour jeunes Africains") == "Rwanda"
    assert extract_country("- Pays d'accueil : Tous les pays") == "International"


def test_format_sql_date():
    # Format X jours restants
    d1 = format_sql_date("30 Jours restant")
    assert re_is_valid_date(d1)

    # Format date textuelle
    d2 = format_sql_date("Date limite : 15 juin 2027")
    assert d2 == "2027-06-15"

    # Fallback si absent
    d3 = format_sql_date(None)
    assert re_is_valid_date(d3)


def re_is_valid_date(date_str: str) -> bool:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def test_resolve_official_link_skips_competitor_aggregator(monkeypatch):
    response = Mock(status_code=200, text="""
        <a href="https://scholarships365.info/another-listing">Apply now</a>
        <a href="https://scholarships.university.edu/apply">Apply now</a>
    """)
    monkeypatch.setattr(link_resolver.requests, "get", lambda *args, **kwargs: response)

    assert link_resolver.resolve_official_link(
        "https://greatyop.com/scholarship-article/",
        "greatyop"
    ) == "https://scholarships.university.edu/apply"


def test_resolve_official_link_does_not_fall_back_to_aggregator(monkeypatch):
    response = Mock(status_code=200, text="""
        <a href="https://scholarshipscorner.website/another-listing">Apply now</a>
        <a href="https://random-blog.com/bursary">Learn more</a>
    """)
    monkeypatch.setattr(link_resolver.requests, "get", lambda *args, **kwargs: response)

    assert link_resolver.resolve_official_link(
        "https://greatyop.com/scholarship-article/",
        "greatyop"
    ) == ""


def test_finder_rejects_excluded_domains():
    from src import official_link_finder as finder

    assert finder._is_excluded("youthmedia.net", {"youthmedia.net"}) is True
    assert finder._is_excluded("www.youthmedia.net", {"youthmedia.net"}) is True
    assert finder._is_excluded("uottawa.ca", {"youthmedia.net"}) is False


def test_finder_extracts_url_and_ignores_redirects():
    from src import official_link_finder as finder

    assert finder._extract_url('{"lien_officiel": "https://www.uottawa.ca/bourses"}') == "https://www.uottawa.ca/bourses"
    assert finder._extract_url(
        "https://vertexaisearch.cloud.google.com/grounding-api-redirect/ABC"
    ) == ""


def test_finder_significant_tokens_drop_generic_words():
    from src import official_link_finder as finder

    tokens = finder._significant_tokens("Bourses d'excellence Université d'Ottawa 2027")
    assert "uottawa" in tokens or "ottawa" in tokens
    assert "bourses" not in tokens
    assert "2027" not in tokens


def test_finder_cache_and_budget(monkeypatch, tmp_path):
    from src import official_link_finder as finder

    class _FakeModels:
        def generate_content(self, **kwargs):
            class _Response:
                text = '{"lien_officiel": ""}'
            return _Response()

    class _FakeClient:
        models = _FakeModels()

    monkeypatch.setattr(finder, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(finder, "_cache", {})
    monkeypatch.setattr(finder, "_searches_done", 0)
    monkeypatch.setattr(finder, "_get_client", lambda: _FakeClient())
    monkeypatch.setenv("LINK_SEARCH_MAX_PER_RUN", "2")

    # Deux recherches autorisées : chaque échec est mémorisé.
    assert finder.find_official_link("Bourse sans résultat 1") == ""
    assert finder._searches_done == 1
    assert finder.find_official_link("Bourse sans résultat 2") == ""
    assert finder._searches_done == 2

    # Budget épuisé : plus aucun appel, et rien n'est mis en cache.
    assert finder.find_official_link("Troisième bourse") == ""
    assert finder._searches_done == 2

    # Un lien déjà trouvé est servi depuis le cache sans consommer de quota.
    finder._cache_set("Bourse en cache", "https://example.edu/bourse")
    assert finder.find_official_link("Bourse en cache") == "https://example.edu/bourse"
    assert finder._searches_done == 2

    # Un échec mémorisé n'est pas retenté avant expiration du cache négatif.
    finder.reset_run_budget()
    assert finder.find_official_link("Bourse sans résultat 1") == ""
    assert finder._searches_done == 0


def test_is_deadline_estimated():
    from src.supabase_sync import is_deadline_estimated

    assert is_deadline_estimated(None) is True
    assert is_deadline_estimated("") is True
    assert is_deadline_estimated("À déterminer") is True
    assert is_deadline_estimated("Date limite : 15 juin 2027") is False
    assert is_deadline_estimated("30 Jours restant") is False


def test_insert_as_draft_never_overwrites_published_scholarship():
    from src.supabase_sync import SupabaseBourseImporter
    from src.models import ScholarshipCandidate

    importer = SupabaseBourseImporter.__new__(SupabaseBourseImporter)

    # Simuler un client Supabase qui retourne une bourse déjà active/publiée
    class FakeTable:
        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            # La bourse existe et est publiée (is_active = True)
            return Mock(data=[{"id": 42, "is_active": True, "titre": "Bourse Eiffel"}])

    class FakeClient:
        def table(self, name):
            return FakeTable()

    importer.client = FakeClient()

    cand = ScholarshipCandidate(
        title="Bourse Eiffel 2026/2027",
        official_link="https://campusfrance.org/bourse-eiffel",
        source_site="greatyop",
        discovery_url="https://greatyop.com/eiffel"
    )

    # Ne doit pas mettre à jour ni écraser la bourse publiée
    res = importer.insert_as_draft(cand, "https://campusfrance.org/bourse-eiffel", {})
    assert res is None

