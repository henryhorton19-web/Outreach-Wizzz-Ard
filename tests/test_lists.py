"""Tests for Named Company Lists creation, switching, persistence, sanitization, and queue scoping."""
import re
from pathlib import Path
from fastapi.testclient import TestClient
from app.server import app, S
from app import store


def test_named_company_lists_crud_and_queue_scoping(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WIZZARD_PROFILE_SOURCE", raising=False)

    client = TestClient(app)
    headers = {"x-wizzard-token": S.SESSION_TOKEN}

    # 1. Fetch initial lists -> default list present
    res_lists = client.get("/api/lists", headers=headers)
    assert res_lists.status_code == 200
    body = res_lists.json()
    assert body["active"] == "default"
    assert any(l["id"] == "default" for l in body["lists"])

    # 2. Ingest into default list
    res_ingest_def = client.post("/api/ingest", json={"text": "Acme Co, https://acme.example", "list_id": "default"}, headers=headers)
    assert res_ingest_def.status_code == 200
    assert res_ingest_def.json()["added"] == 1

    # 3. Create a new named list
    res_create = client.post("/api/lists", json={"name": "Growth Funds Paris"}, headers=headers)
    assert res_create.status_code == 200
    create_body = res_create.json()
    new_list_id = create_body["list"]["id"]
    assert new_list_id != "default"
    assert create_body["active"] == new_list_id

    # 4. Active list persistence endpoint & GET check
    res_active = client.post("/api/lists/active", json={"id": new_list_id}, headers=headers)
    assert res_active.status_code == 200
    assert res_active.json()["active"] == new_list_id
    assert client.get("/api/lists", headers=headers).json()["active"] == new_list_id
    assert store.active_list_id() == new_list_id

    # 5. Ingest into new named list
    res_ingest_new = client.post("/api/ingest", json={"text": "Paris Growth Cap, https://pariscap.example", "list_id": new_list_id}, headers=headers)
    assert res_ingest_new.status_code == 200
    assert res_ingest_new.json()["added"] == 1

    # 6. Verify queues are completely separate and scoped
    q_default = client.get("/api/queue?list_id=default", headers=headers).json()["queue"]
    q_new = client.get(f"/api/queue?list_id={new_list_id}", headers=headers).json()["queue"]

    assert any(r["name"] == "Acme Co" for r in q_default)
    assert not any(r["name"] == "Paris Growth Cap" for r in q_default)

    assert any(r["name"] == "Paris Growth Cap" for r in q_new)
    assert not any(r["name"] == "Acme Co" for r in q_new)

    # 7. Delete active list -> resets active to "default"
    res_del = client.delete(f"/api/lists/{new_list_id}", headers=headers)
    assert res_del.status_code == 200
    del_body = res_del.json()
    assert del_body["active"] == "default"
    assert not any(l["id"] == new_list_id for l in del_body["lists"])
    assert store.active_list_id() == "default"


def test_list_id_sanitization_and_resilience(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))

    # 1. Reserved name 'con' -> 'con_list'
    c_rec = store.create_list("con")
    assert c_rec["id"] == "con_list"
    assert c_rec["id"] != "con"

    # 2. 200-character name yields ID of at most 40 chars matching ^[a-z0-9][a-z0-9_-]*$
    long_name = "a" * 200
    l_rec = store.create_list(long_name)
    assert len(l_rec["id"]) <= 40
    assert re.match(r"^[a-z0-9][a-z0-9_-]*$", l_rec["id"])

    # 3. Path traversal name contains no . or /
    p_rec = store.create_list("../../etc/passwd")
    assert "." not in p_rec["id"]
    assert "/" not in p_rec["id"]
    assert re.match(r"^[a-z0-9][a-z0-9_-]*$", p_rec["id"])

    # 4. Fallback when active list is missing from lists.json
    store.save_lists([{"id": "default", "name": "Default List", "count": 0}], active_id="nonexistent_id")
    assert store.active_list_id() == "default"
