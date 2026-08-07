"""Tests for Stage 4: Draft All persistence, checkpointing, and resume."""
import time
import pytest
from app import settings as S, store, server

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    return TestClient(server.app)

def _h():
    return {"x-wizzard-token": S.SESSION_TOKEN}

@pytest.fixture(autouse=True)
def _reset_state():
    server._STATE["batch"] = None
    server._DRAFT_JOBS.clear()
    store.clear_drafts()
    store.clear_queue()
    if S.DRAFT_JOBS_FILE.exists():
        S.DRAFT_JOBS_FILE.unlink()

def _queue_and_promote(client, names):
    client.post("/api/ingest", json={"text": "\n".join(names)}, headers=_h())
    q = client.get("/api/queue", headers=_h()).json().get("queue", [])
    slugs = []
    for row in q:
        client.post(f"/api/queue/{row['slug']}/draft", headers=_h())
        slugs.append(row["slug"])
    return slugs

def test_draft_job_persists_to_disk(client):
    slugs = _queue_and_promote(client, ["Co 1", "Co 2"])
    r = client.post("/api/draft", json={"slugs": slugs}, headers=_h())
    job_id = r.json()["job_id"]
    
    for _ in range(50):
        if client.get(f"/api/draft/job/{job_id}", headers=_h()).json().get("state") == "done":
            break
        time.sleep(0.1)
        
    persisted = store.load_draft_jobs()
    assert job_id in persisted
    assert persisted[job_id]["state"] == "done"
    assert len(persisted[job_id].get("completed", [])) == 2

def test_resume_draft_job(client):
    job_id = "test_job_resume"
    fake_job = {
        "job_id": job_id,
        "done": 1,
        "total": 3,
        "current_slug": "co_1",
        "errors": [],
        "state": "cancelled",
        "cancelled": True,
        "all_slugs": ["co_1", "co_2", "co_3"],
        "completed": ["co_1"],
    }
    store.save_draft_jobs({job_id: fake_job})
    
    _queue_and_promote(client, ["Co 1", "Co 2", "Co 3"])
    
    res = client.post(f"/api/draft/job/{job_id}/resume", headers=_h())
    assert res.status_code == 200
    assert res.json()["remaining"] == 2
    
    for _ in range(50):
        st = client.get(f"/api/draft/job/{job_id}", headers=_h()).json()
        if st.get("state") == "done":
            break
        time.sleep(0.1)
        
    persisted = store.load_draft_jobs()
    assert set(persisted[job_id].get("completed", [])) == {"co_1", "co_2", "co_3"}
