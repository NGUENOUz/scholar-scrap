"""Tests unitaires pour l'enrichissement des bourses."""

import json
from types import SimpleNamespace

from src import ai_enricher
from src.ai_enricher import AIEnricher


def test_client_uses_bounded_timeout_and_disables_retries(monkeypatch):
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(ai_enricher.genai, "Client", fake_client)

    AIEnricher()

    options = captured["http_options"]
    assert options.timeout == ai_enricher.GEMINI_TIMEOUT_MS
    assert options.retry_options.attempts == 1


def test_enrich_uses_official_page_context_without_inventing_details():
    official_context = "Dell Scholars: candidats issus de foyers à faibles revenus; bourse de 20 000 $."
    captured_prompt = {}

    class FakeModels:
        def generate_content(self, model, contents):
            captured_prompt["text"] = contents
            return SimpleNamespace(text=json.dumps({
                "organisme": "Dell Scholars Program",
                "description": "Programme de bourses Dell.",
                "montant": "20 000 $",
                "type_financement": "Partiellement financé",
                "niveau_etudes": ["Licence"],
                "domaine_etudes": [],
                "criteres": [],
                "avantages": [],
                "couverture": [],
                "documents_requis": [],
                "processus": [],
                "filieres": []
            }))

    enricher = AIEnricher.__new__(AIEnricher)
    enricher.client = SimpleNamespace(models=FakeModels())

    result = enricher.enrich(
        title="Programme de Bourses Dell",
        official_link="https://example.org/dell",
        country="États-Unis",
        deadline="2027-01-15",
        scraped_context=official_context
    )

    assert result["montant"] == "20 000 $"
    assert official_context in captured_prompt["text"]
    assert "N'invente aucun montant, critère" in captured_prompt["text"]