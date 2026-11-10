from src.sources import (
    detect_domain_type,
    extract_deadline,
    is_deadline_expired,
    parse_date_safely,
    SourceManager
)


def test_is_deadline_expired():
    # Dates passées -> expirées
    assert is_deadline_expired("Date limite : 01/01/2020") is True
    assert is_deadline_expired("Clos") is True
    assert is_deadline_expired("0 jours restant") is True

    # Dates futures -> valides
    assert is_deadline_expired("Date limite : 31/12/2030") is False
    assert is_deadline_expired("90 Jours restant") is False
    assert is_deadline_expired(None) is False  # Non précisée = acceptée prudemment



def test_detect_domain_type():
    assert detect_domain_type("https://ox.ac.uk/scholarships") == "Université"
    assert detect_domain_type("https://harvard.edu/financial-aid") == "Université"
    assert detect_domain_type("https://etudiant.gouv.fr/bourses") == "Gouvernement"
    assert detect_domain_type("https://daad.de/scholarships") == "Fondation"
    assert detect_domain_type("https://greatyop.com/post-1") == "Autre"


def test_extract_deadline():
    assert extract_deadline("Date limite : 31 mai 2026") == "31 mai 2026"
    assert extract_deadline("Plus que 15 jours restants pour postuler") == "15 jours restants"
    assert extract_deadline("Aucune date mentionnée ici") is None


def test_source_manager_config():
    mgr = SourceManager("config/sources.yaml")
    src = mgr.get_source("greatyop")
    assert src is not None
    assert src["name"] == "greatyop"
    assert "url" in src


def test_extraction_from_html():
    mgr = SourceManager("config/sources.yaml")
    src = mgr.get_source("greatyop")

    html_sample = """
    <html>
        <body>
            <article>
                <h2 class="entry-title"><a href="/bourse-paris-2026/">Bourse d'Excellence Paris 2026</a></h2>
                <div class="entry-summary">Une superbe bourse pour financer un master en France. Date limite : 15 Juin 2026.</div>
            </article>
        </body>
    </html>
    """
    candidates = mgr.extract_from_html(src, html_sample, "https://greatyop.com/category/bourses/")
    assert len(candidates) == 1
    c = candidates[0]
    assert c.title == "Bourse d'Excellence Paris 2026"
    assert c.official_link == "https://greatyop.com/bourse-paris-2026/"
    assert "15 Juin 2026" in (c.deadline or "")
    assert c.source_site == "greatyop"
