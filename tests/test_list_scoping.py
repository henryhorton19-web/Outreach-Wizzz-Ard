"""Regression tests for the list-scoping defect class.

Ten nodes on this path were mis-scoped: two server routes, two service-layer
calls, six client calls. Each test here fails on dbe3abb and must keep passing.

Do not weaken an assertion to make a test pass.
"""
import json
import pathlib
import re

import pytest

from app import settings as S
from app import store


# ---------------------------------------------------------------------------
# store: list id validation (was an arbitrary file write outside DATA_DIR)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["../../etc/passwd", "..", ".", "a/b", "a\\b",
                                 "CON", "con", "nul", "lpt1", "", "A", "-x", "x" * 41])
def test_queue_file_rejects_malformed_list_ids(bad):
    with pytest.raises(store.StorageError):
        store._queue_file(bad)


def test_queue_file_accepts_default_and_valid_ids():
    assert store._queue_file("default").name == "queue.json"
    assert store._queue_file("yc_sourcing_list").name == "yc_sourcing_list.json"


def test_sanitize_list_id_output_always_passes_validation():
    """create_list feeds sanitize_list_id output straight into _queue_file."""
    for name in ["YC Sourcing List", "con", "NUL", "   ", "---", "a" * 60,
                 "Séries A / B", "list?with*chars", "1st"]:
        sid = store.sanitize_list_id(name)
        store._queue_file(sid)  # must not raise


# ---------------------------------------------------------------------------
# store: a malformed entry must degrade ITSELF, not hide every list
# ---------------------------------------------------------------------------

def test_one_malformed_list_id_does_not_hide_the_others(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LISTS_FILE", tmp_path / "lists.json")
    monkeypatch.setattr(store, "QUEUES_DIR", tmp_path / "queues")
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    store.LISTS_FILE.write_text(json.dumps({"active": "default", "lists": [
        {"id": "default", "name": "Default List", "created_at": "2026-01-01T00:00:00Z"},
        {"id": "yc_sourcing_list", "name": "YC Sourcing List", "created_at": "2026-02-01T00:00:00Z"},
        {"id": "YC-Legacy", "name": "Legacy Uppercase", "created_at": "2025-01-01T00:00:00Z"},
    ]}), encoding="utf-8")

    lists = store.load_lists()
    assert len(lists) == 3, f"a malformed id hid the others: {[l['id'] for l in lists]}"
    bad = [l for l in lists if l.get("unavailable")]
    assert len(bad) == 1 and bad[0]["id"] == "YC-Legacy"
    assert bad[0].get("reason"), "an unavailable list must say why"


def test_delete_list_tolerates_a_malformed_id(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "LISTS_FILE", tmp_path / "lists.json")
    monkeypatch.setattr(store, "QUEUES_DIR", tmp_path / "queues")
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    store.LISTS_FILE.write_text(json.dumps({"active": "default", "lists": [
        {"id": "default", "name": "Default List", "created_at": "2026-01-01T00:00:00Z"},
        {"id": "YC-Legacy", "name": "Legacy", "created_at": "2025-01-01T00:00:00Z"},
    ]}), encoding="utf-8")
    assert store.delete_list("YC-Legacy") is True  # must not raise


# ---------------------------------------------------------------------------
# store: absent vs unreadable are different outcomes
# ---------------------------------------------------------------------------

def test_absent_queue_is_empty_without_degrading(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "QUEUES_DIR", tmp_path / "queues")
    monkeypatch.setattr(store, "DEGRADED", None)
    assert store.load_queue(list_id="never_created") == []
    assert store.DEGRADED is None


def test_corrupt_queue_is_empty_AND_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "QUEUES_DIR", tmp_path / "queues")
    monkeypatch.setattr(store, "DEGRADED", None)
    (tmp_path / "queues").mkdir(parents=True, exist_ok=True)
    (tmp_path / "queues" / "broken.json").write_text('{"items": [{"slug":', encoding="utf-8")
    assert store.load_queue(list_id="broken") == []
    assert store.DEGRADED, "a corrupt file must be distinguishable from an empty one"


# ---------------------------------------------------------------------------
# API: an omitted list_id resolves to the ACTIVE list, not the literal "default"
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


HDR = None


def _h():
    return {"x-wizzard-token": S.SESSION_TOKEN}


def test_omitted_list_id_uses_the_active_list(client):
    lid = client.post("/api/lists", json={"name": "Scoping Probe"}, headers=_h()).json()["list"]["id"]
    client.post("/api/ingest", json={"text": "Qonto\nPennylane", "list_id": lid}, headers=_h())
    scoped = client.get(f"/api/queue?list_id={lid}", headers=_h()).json()["queue"]
    implicit = client.get("/api/queue", headers=_h()).json()["queue"]
    assert len(scoped) == len(implicit) == 2
    client.delete(f"/api/lists/{lid}", headers=_h())


def test_status_reports_the_active_list_and_degraded_flag(client):
    body = client.get("/api/status", headers=_h()).json()
    assert "active_list" in body
    assert "degraded" in body


def test_ingest_file_honours_list_id(client):
    lid = client.post("/api/lists", json={"name": "Upload Probe"}, headers=_h()).json()["list"]["id"]
    r = client.post(f"/api/ingest_file?list_id={lid}",
                    files={"file": ("t.csv", b"Company Name\nFigma\n", "text/csv")},
                    headers=_h())
    assert r.status_code == 200 and r.json()["added"] == 1
    assert any(x["name"] == "Figma" for x in
               client.get(f"/api/queue?list_id={lid}", headers=_h()).json()["queue"])
    client.delete(f"/api/lists/{lid}", headers=_h())


@pytest.mark.parametrize("bad", ["../../../../tmp/pwned", "..", "CON"])
def test_traversal_via_list_id_is_a_clean_400_not_a_500(client, bad):
    r = client.post(f"/api/queue/clear?list_id={bad}", headers=_h())
    assert r.status_code == 400, f"expected 400, got {r.status_code}"
    assert not pathlib.Path("/tmp/pwned.json").exists()


# ---------------------------------------------------------------------------
# static: no UI call may reach a list-scoped endpoint without list_id
# ---------------------------------------------------------------------------

def test_no_unscoped_queue_calls_in_the_ui():
    js = (pathlib.Path(__file__).parent.parent / "ui" / "app.js").read_text(encoding="utf-8")
    offenders = [
        (n, line.strip())
        for n, line in enumerate(js.splitlines(), 1)
        if "api(" in line
        and re.search(r"/api/(queue|ingest_file)\b", line)
        and "list_id" not in line
    ]
    assert not offenders, f"unscoped list calls: {offenders}"


def test_sourcing_undo_reverses_the_list_it_added_to():
    """The job records added_list_id; undo must use it, not the active list."""
    src = (pathlib.Path(__file__).parent.parent / "app" / "sourcing" / "research_job.py").read_text(encoding="utf-8")
    assert 'job["added_list_id"]' in src, "the sourcing job must record its destination"
    assert "remove_from_queue(s, list_id=undo_list_id)" in src, "undo must use the recorded list"
