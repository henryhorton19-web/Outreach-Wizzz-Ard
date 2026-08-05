"""Tests for Named Company Lists creation, switching, and queue scoping."""
from pathlib import Path
from fastapi.testclient import TestClient
from app.server import app, S


def test_named_company_lists_crud_and_queue_scoping(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WIZZARD_PROFILE_SOURCE", raising=False)

    client = TestClient(app)
    headers = {"x-wizzard-token": S.SESSION_TOKEN}

    # 1. Fetch initial lists -> default list present
    res_lists = client.get("/api/lists", headers=headers)
    assert res_lists.status_code == 200
    lists = res_lists.json()["lists"]
    assert any(l["id"] == "default" for l in lists)

    # 2. Ingest into default list
    res_ingest_def = client.post("/api/ingest", json={"text": "Acme Co, https://acme.example", "list_id": "default"}, headers=headers)
    assert res_ingest_def.status_code == 200
    assert res_ingest_def.json()["added"] == 1

    # 3. Create a new named list
    res_create = client.post("/api/lists", json={"name": "Growth Funds Paris"}, headers=headers)
    assert res_create.status_code == 200
    new_list_id = res_create.json()["list"]["id"]
    assert new_list_id != "default"

    # 4. Ingest into new named list
    res_ingest_new = client.post("/api/ingest", json={"text": "Paris Growth Cap, https://pariscap.example", "list_id": new_list_id}, headers=headers)
    assert res_ingest_new.status_code == 200
    assert res_ingest_new.json()["added"] == 1

    # 5. Verify queues are completely separate and scoped
    q_default = client.get("/api/queue?list_id=default", headers=headers).json()["queue"]
    q_new = client.get(f"/api/queue?list_id={new_list_id}", headers=headers).json()["queue"]

    assert any(r["name"] == "Acme Co" for r in q_default)
    assert not any(r["name"] == "Paris Growth Cap" for r in q_default)

    assert any(r["name"] == "Paris Growth Cap" for r in q_new)
    assert not any(r["name"] == "Acme Co" for r in q_new)

    # 6. Delete named list
    res_del = client.delete(f"/api/lists/{new_list_id}", headers=headers)
    assert res_del.status_code == 200
    assert not any(l["id"] == new_list_id for l in res_del.json()["lists"])
