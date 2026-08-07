"""Route-level coverage for the BATCH draft path (POST /api/draft).

Why this file exists: POST /api/draft was rewritten into a background job, and the
rewrite shipped calling threading.Thread(...) while `threading` was never imported
in app/server.py -- so every batch draft returned HTTP 500 and every target stayed
stuck at "queued". The test suite was fully green at the time.

The reason it was missed: three existing tests exercise POST /api/draft/{slug}
(the SINGLE-company path) and none exercised POST /api/draft (the BATCH path that
actually changed).

Do not merge these into the single-company tests. They are different endpoints with
different implementations, and conflating them is what allowed a crash on the
primary action to ship green.
"""
import ast
import pathlib
import time

import pytest

from app import settings as S


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


def _h():
    return {"x-wizzard-token": S.SESSION_TOKEN}


def _seed_batch(client, n=3):
    """Ingest names, then promote them to drafts so a batch exists to draft."""
    client.post("/api/ingest",
                json={"text": "\n".join(f"Batch Probe {i}" for i in range(n))},
                headers=_h())
    queue = client.get("/api/queue", headers=_h()).json().get("queue", [])
    for row in queue[:n]:
        client.post(f"/api/queue/{row['slug']}/draft", headers=_h())
    return queue[:n]


def test_batch_draft_starts_and_returns_a_job_id(client):
    """The exact regression: this returned 500 with NameError: threading."""
    _seed_batch(client)
    r = client.post("/api/draft", json={}, headers=_h())
    assert r.status_code == 200, f"batch draft failed: {r.status_code} {r.text[:300]}"
    body = r.json()
    assert body.get("job_id"), f"no job_id returned: {sorted(body.keys())}"
    assert isinstance(body.get("total"), int)


def test_batch_draft_returns_immediately(client):
    """The request must hand back a job id, not block until the batch finishes."""
    _seed_batch(client)
    t0 = time.time()
    r = client.post("/api/draft", json={}, headers=_h())
    elapsed = time.time() - t0
    assert r.status_code == 200
    assert elapsed < 2.0, f"POST /api/draft blocked for {elapsed:.1f}s -- it should return a job id"


def test_draft_job_status_is_pollable(client):
    _seed_batch(client)
    job_id = client.post("/api/draft", json={}, headers=_h()).json()["job_id"]
    r = client.get(f"/api/draft/job/{job_id}", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body.get("state") in ("running", "done", "cancelled", "error")
    assert "done" in body and "total" in body


def test_targets_actually_reach_the_drafted_state(client):
    """The reported symptom, as a test: everything sat at 'queued' forever."""
    from app import store
    _seed_batch(client, n=2)
    job_id = client.post("/api/draft", json={}, headers=_h()).json()["job_id"]
    for _ in range(60):
        state = client.get(f"/api/draft/job/{job_id}", headers=_h()).json().get("state")
        if state != "running":
            break
        time.sleep(0.2)
    drafts = store.load_drafts()
    assert drafts, "no drafts were produced at all"
    assert any(d.machine_email for d in drafts), \
        "the job finished but no target has a drafted email -- they are still stuck"


def test_other_endpoints_respond_while_a_batch_draft_is_running(client):
    """Pipeline/Triage/Follow-ups rendered blank while emails drafted."""
    _seed_batch(client, n=5)
    client.post("/api/draft", json={}, headers=_h())
    for ep in ("/api/status", "/api/pipeline", "/api/triage", "/api/followups"):
        r = client.get(ep, headers=_h())
        assert r.status_code == 200, f"{ep} returned {r.status_code} during a draft run"


def test_settings_are_writable_during_a_batch_draft(client):
    """The second reported symptom. NOTE: settings is POST, not PUT."""
    _seed_batch(client, n=5)
    client.post("/api/draft", json={}, headers=_h())
    r = client.post("/api/settings", json={"pipeline_stale_days": 9}, headers=_h())
    assert r.status_code == 200, f"settings write failed during a draft run: {r.text[:200]}"


def test_server_imports_every_stdlib_module_it_uses():
    """A structural guard against this exact class of defect: a module used inside
    a function body but never imported. This is the check that found the `re` bug
    after the `threading` one was fixed.
    """
    src = pathlib.Path(__file__).parent.parent / "app" / "server.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                imported.add(a.asname or a.name)

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            used.add(node.value.id)

    stdlib_like = {"threading", "uuid", "os", "sys", "json", "time", "re", "io",
                   "datetime", "pathlib", "asyncio", "subprocess", "shutil", "traceback"}
    missing = sorted((used & stdlib_like) - imported)
    assert not missing, (
        f"app/server.py uses {missing} at runtime but never imports them. This is the "
        f"NameError class that made every batch draft return 500 while the suite was green."
    )
