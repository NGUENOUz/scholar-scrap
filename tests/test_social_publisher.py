import csv
import json
from pathlib import Path
from types import SimpleNamespace

from src.dedup import normalize_url
from src.social_publisher import (
    BufferIdeasClient,
    OpenAIContentWriter,
    prepare_social_posts,
)
from src.storage import Storage


def test_openai_writer_returns_required_copy_fields(monkeypatch):
    captured = {}
    content = {
        "canva_description": "Une bourse pour les étudiants.",
        "social_text": "Découvrez cette opportunité.",
        "image_suggestion": "étudiants sur un campus"
    }

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            ok=True,
            json=lambda: {
                "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]
            }
        )

    monkeypatch.setattr("src.social_publisher.requests.post", fake_post)
    writer = OpenAIContentWriter(api_key="test-key")
    result = writer.write({"titre": "Bourse test"}, "https://drenolearn.com/bourses/test")

    assert result == content
    assert captured["json"]["response_format"] == {"type": "json_object"}
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert "https://drenolearn.com/bourses/test" in captured["json"]["messages"][1]["content"]
    assert "𝐃𝐀𝐓𝐄 𝐋𝐈𝐌𝐈𝐓𝐄" in captured["json"]["messages"][1]["content"]
    assert "https://www.drenolearn.com/abonnement" in captured["json"]["messages"][1]["content"]
    assert "N'invente ni montant" in captured["json"]["messages"][1]["content"]


def test_openai_writer_accepts_openai_key_environment_name(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_KEY", "local-key")

    writer = OpenAIContentWriter()

    assert writer.api_key == "local-key"


def test_buffer_client_creates_unscheduled_idea(monkeypatch):
    calls = []
    headers = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"]["query"])
        headers.append(kwargs["headers"])
        if len(calls) == 1:
            data = {"account": {"organizations": [{"id": "org-1", "name": "DrenoLearn"}]}}
        else:
            data = {"createIdea": {"id": "idea-1"}}
        return SimpleNamespace(ok=True, json=lambda: {"data": data})

    monkeypatch.setattr("src.social_publisher.requests.post", fake_post)
    client = BufferIdeasClient(api_key="test-key")

    assert client.create_idea("Titre test", "Texte test") == "idea-1"
    assert all(header["Authorization"] == "Bearer test-key" for header in headers)
    assert "organizations" in calls[0]
    assert "createIdea" in calls[1]
    assert "mode:" not in calls[1]


class FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, _name):
        return FakeQuery(self.rows)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _fields):
        return self

    def eq(self, _field, _value):
        return self

    def order(self, _field, **_kwargs):
        return self

    def limit(self, _limit):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeWriter:
    def write(self, row, public_url):
        return {
            "canva_description": f"Résumé de {row['titre']}.",
            "social_text": f"Découvrez {row['titre']}.",
            "image_suggestion": "étudiants sur un campus"
        }


class FakeBuffer:
    def __init__(self):
        self.ideas = []

    def create_idea(self, title, text):
        self.ideas.append((title, text))
        return f"idea-{len(self.ideas)}"


class FakePlatformSync:
    def __init__(self, urls):
        self.existing_urls = urls

    def fetch_existing_scholarships(self):
        pass


def test_prepare_social_exports_full_batch_and_starts_next_batch(tmp_path):
    rows = [
        {
            "id": "bourse-1", "slug": "bourse-1", "titre": "Bourse au Canada",
            "description": "Bourse universitaire.", "date_limite": "2027-01-15",
            "niveau_etudes": ["Master"], "is_active": True
        },
        {
            "id": "stage-1", "slug": "stage-1", "titre": "Stage international au Japon",
            "description": "Stage en ingénierie.", "date_limite": None,
            "niveau_etudes": ["Licence"], "is_active": True
        },
        {
            "id": "bourse-2", "slug": "bourse-2", "titre": "Bourse en France",
            "description": "Bourse de recherche.", "date_limite": "2027-02-01",
            "niveau_etudes": ["Doctorat"], "is_active": True
        },
        {
            "id": "bourse-3", "slug": "bourse-3", "titre": "Bourse en Suisse",
            "description": "Bourse d'étude.", "date_limite": "2027-03-01",
            "niveau_etudes": ["Master"], "is_active": True
        }
    ]
    importer = SimpleNamespace(client=FakeSupabase(rows))
    storage = Storage(str(tmp_path / "social.db"))
    buffer = FakeBuffer()
    platform = FakePlatformSync({
        normalize_url("https://drenolearn.com/bourses/bourse-1"),
        normalize_url("https://drenolearn.com/bourses/stage-1"),
        normalize_url("https://drenolearn.com/bourses/bourse-2"),
        normalize_url("https://drenolearn.com/bourses/bourse-3")
    })

    first = prepare_social_posts(
        importer, storage, FakeWriter(), buffer, platform,
        output_dir=str(tmp_path), batch_size=3, timezone_name="Africa/Douala"
    )

    assert first["created_ideas"] == 3
    assert len(buffer.ideas) == 3
    assert first["batch_progress"] == 0
    assert first["remaining_to_batch"] == 3
    assert len(first["csv_files"]) == 2
    csv_names = {Path(path).name for path in first["csv_files"]}
    assert any(name.startswith("canva_bourses_") for name in csv_names)
    assert any(name.startswith("canva_stages_") for name in csv_names)
    for path in first["csv_files"]:
        with Path(path).open(encoding="utf-8-sig", newline="") as file:
            rows_in_file = list(csv.DictReader(file, delimiter=";"))
        assert all(row["ID_BOURSE"] and row["DESCRIPTION_BLOC"] for row in rows_in_file)

    second = prepare_social_posts(
        importer, storage, FakeWriter(), buffer, platform,
        output_dir=str(tmp_path), batch_size=3, timezone_name="Africa/Douala"
    )

    assert second["created_ideas"] == 1
    assert second["batch_progress"] == 1
    assert second["remaining_to_batch"] == 2
    assert len(buffer.ideas) == 4
    assert second["csv_files"] == []


def test_social_posts_wait_for_full_csv_batch(tmp_path):
    rows = [
        {
            "id": f"bourse-{i}", "slug": f"bourse-{i}", "titre": f"Bourse {i}",
            "description": "Bourse universitaire.", "date_limite": "2027-01-15",
            "niveau_etudes": ["Master"], "is_active": True
        }
        for i in range(1, 4)
    ]
    storage = Storage(str(tmp_path / "social.db"))
    buffer = FakeBuffer()
    platform = FakePlatformSync({
        normalize_url(f"https://drenolearn.com/bourses/bourse-{i}")
        for i in range(1, 4)
    })
    importer = SimpleNamespace(client=FakeSupabase(rows[:2]))

    partial = prepare_social_posts(
        importer, storage, FakeWriter(), buffer, platform,
        output_dir=str(tmp_path), batch_size=3, timezone_name="Africa/Douala"
    )
    assert partial["created_ideas"] == 2
    assert partial["batch_progress"] == 2
    assert partial["remaining_to_batch"] == 1
    assert partial["csv_files"] == []

    importer.client.rows.append(rows[2])
    complete = prepare_social_posts(
        importer, storage, FakeWriter(), buffer, platform,
        output_dir=str(tmp_path), batch_size=3, timezone_name="Africa/Douala"
    )

    assert complete["created_ideas"] == 1
    assert complete["batch_progress"] == 0
    assert len(buffer.ideas) == 3
    assert len(complete["csv_files"]) == 1


def test_default_batch_exports_only_after_21_published_ideas(tmp_path):
    rows = [
        {
            "id": f"bourse-{i}", "slug": f"bourse-{i}", "titre": f"Bourse {i}",
            "description": "Bourse universitaire.", "date_limite": "2027-01-15",
            "niveau_etudes": ["Master"], "is_active": True
        }
        for i in range(1, 22)
    ]
    storage = Storage(str(tmp_path / "social.db"))
    buffer = FakeBuffer()
    platform = FakePlatformSync({
        normalize_url(f"https://drenolearn.com/bourses/bourse-{i}")
        for i in range(1, 22)
    })
    importer = SimpleNamespace(client=FakeSupabase(rows[:20]))

    first = prepare_social_posts(
        importer, storage, FakeWriter(), buffer, platform,
        output_dir=str(tmp_path), timezone_name="Africa/Douala"
    )
    assert first["batch_size"] == 21
    assert first["batch_progress"] == 20
    assert first["csv_files"] == []
    assert len(buffer.ideas) == 20

    importer.client.rows.append(rows[20])
    completed = prepare_social_posts(
        importer, storage, FakeWriter(), buffer, platform,
        output_dir=str(tmp_path), timezone_name="Africa/Douala"
    )

    assert completed["created_ideas"] == 1
    assert completed["batch_progress"] == 0
    assert len(buffer.ideas) == 21
    assert len(completed["csv_files"]) == 1
    with Path(completed["csv_files"][0]).open(encoding="utf-8-sig", newline="") as file:
        rows_in_csv = list(csv.DictReader(file, delimiter=";"))
    assert len(rows_in_csv) == 21
