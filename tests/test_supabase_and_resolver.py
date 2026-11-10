"""
Tests pour link_resolver.py et supabase_sync.py
"""

from datetime import datetime
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
