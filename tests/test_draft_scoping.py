"""POST /api/draft must draft exactly the companies it is given.

Reproduced on fa0eba8: with 4 companies already drafted and 5 newly promoted,
pressing Draft 5 produced a job with total=9. draft5() promotes 5 specific slugs
then calls POST /api/draft with no reference to them, and the server rebuilds its
work set from the whole batch.
"""
import time

import pytest

from app import settings as S


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_state():
    from app import store, server
    server._STATE["batch"] = None
    server._DRAFT_JOBS.clear()
    store.clear_drafts()
    store.clear_queue()


def _h():
    return {"x-wizzard-token": S.SESSION_TOKEN}


def _queue_and_promote(client, names):
    client.post("/api/ingest", json={"text": "\n".join(names)}, headers=_h())
    slugs = []
    for row in client.get("/api/queue", headers=_h()).json().get("queue", []):
        if row["slug"] not in slugs:
            client.post(f"/api/queue/{row['slug']}/draft", headers=_h())
            slugs.append(row["slug"])
    return slugs


def _await(client, job_id):
    for _ in range(80):
        b = client.get(f"/api/draft/job/{job_id}", headers=_h()).json()
        if b.get("state") != "running":
            return b
        time.sleep(0.15)
    return client.get(f"/api/draft/job/{job_id}", headers=_h()).json()


def test_draft_honours_an_explicit_slug_list(client):
    slugs = _queue_and_promote(client, [f"Scoped {i}" for i in range(6)])
    r = client.post("/api/draft", json={"slugs": slugs[:3]}, headers=_h())
    assert r.status_code == 200, r.text[:300]
    assert r.json()["total"] == 3, f"asked for 3, job scoped to {r.json()['total']}"


def test_draft_without_slugs_still_drafts_the_whole_batch(client):
    _queue_and_promote(client, [f"Whole {i}" for i in range(4)])
    r = client.post("/api/draft", json={}, headers=_h())
    assert r.json()["total"] == 4


def test_already_drafted_companies_are_not_swept_in(client):
    """The reported symptom exactly."""
    first = _queue_and_promote(client, [f"First {i}" for i in range(4)])
    _await(client, client.post("/api/draft", json={"slugs": first}, headers=_h()).json()["job_id"])
    second = _queue_and_promote(client, [f"Second {i}" for i in range(9)])
    new_only = [s for s in second if s not in first][:5]
    r = client.post("/api/draft", json={"slugs": new_only}, headers=_h())
    assert r.json()["total"] == 5, \
        f"expected 5, got {r.json()['total']} -- earlier drafts were swept in again"


def test_unknown_slugs_are_reported_not_dropped(client):
    _queue_and_promote(client, ["Known One", "Known Two"])
    r = client.post("/api/draft", json={"slugs": ["known_one", "does_not_exist"]}, headers=_h())
    body = r.json()
    assert body["total"] == 1
    assert "does_not_exist" in (body.get("skipped") or []), f"silently dropped; body was {body}"
