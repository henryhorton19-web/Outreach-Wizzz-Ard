"""The website on a queued target is editable until it is drafted.

It is the identity anchor research uses (research._identity_anchor), so a wrong one is worse than
a missing one. Unlike the CSV path, which silently discards a non-URL column 2, a deliberate manual
edit that is not a URL is rejected so the operator sees the mistake.
"""
import pytest
from fastapi.testclient import TestClient

from app import store, settings as S
from app.server import app


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    monkeypatch.setattr(store, "LISTS_FILE", tmp_path / "lists.json")
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    yield


@pytest.fixture
def client():
    c = TestClient(app)
    c.headers.update({"x-wizzard-token": S.SESSION_TOKEN})
    return c


def _queue_one(website=None):
    store.upsert_queue("elix_ai", "Northwind AI", None, None, list_id="default", website=website)


def _rec():
    return [r for r in store.load_queue(list_id="default") if r["slug"] == "elix_ai"][0]


def test_set_a_website_on_a_queued_target(client):
    _queue_one()
    r = client.put("/api/queue/elix_ai/website", json={"website": "https://northwind-ai.test"})
    assert r.status_code == 200
    assert _rec()["website"] == "https://northwind-ai.test"


def test_correct_a_wrong_website(client):
    _queue_one("https://elix-inc.com")
    client.put("/api/queue/elix_ai/website", json={"website": "northwind-ai.test"})
    assert _rec()["website"] == "northwind-ai.test"


def test_clear_a_website_with_an_empty_string(client):
    _queue_one("https://northwind-ai.test")
    r = client.put("/api/queue/elix_ai/website", json={"website": ""})
    assert r.status_code == 200
    assert not _rec().get("website")


def test_a_non_url_is_rejected_not_silently_dropped(client):
    _queue_one()
    r = client.put("/api/queue/elix_ai/website", json={"website": "the paris one"})
    assert r.status_code == 400
    assert not _rec().get("website")


def test_unknown_slug_is_404(client):
    r = client.put("/api/queue/nope/website", json={"website": "https://x.com"})
    assert r.status_code == 404


def test_edited_website_reaches_the_draft(client):
    _queue_one()
    client.put("/api/queue/elix_ai/website", json={"website": "https://northwind-ai.test"})
    client.post("/api/queue/elix_ai/draft")
    cs = store.get_draft("elix_ai")
    assert cs is not None
    assert cs.website == "https://northwind-ai.test"
