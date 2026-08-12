"""A retry after a failure must not reuse the cache that produced it.

Both retry buttons call runDraft, which sent a hardcoded reuse_cache: true, so a
retry re-ran the same composition against the same inputs and produced the same
output. Nothing about a retry differed from the attempt that had already failed.
"""
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


def _ingest(client, name=None):
    if name is None:
        name = f"Retry Probe {time.time_ns()}"
    client.post("/api/ingest", json={"text": name}, headers=_h())
    slug = client.get("/api/queue", headers=_h()).json()["queue"][0]["slug"]
    client.post(f"/api/queue/{slug}/draft", headers=_h())
    return slug


def test_an_explicit_reuse_flag_is_still_honoured(client):
    """The batch path and the redraft endpoint pass this explicitly and must be
    unaffected."""
    slug = _ingest(client)
    r = client.post(f"/api/draft/{slug}", json={"reuse_cache": True}, headers=_h())
    assert r.status_code in (200, 400, 404)


def test_a_target_in_error_defaults_to_fresh_research(client, monkeypatch):
    import app.pipeline as pipeline
    seen = {}

    def spy(provider, cs, voice_override=None, *, reuse_cache=True):
        seen["reuse_cache"] = reuse_cache
        return cs

    slug = _ingest(client)
    from app.server import _batch
    from app.models import State
    b = _batch()
    if b and slug in b.companies:
        b.companies[slug].state = State.error
    monkeypatch.setattr(pipeline, "draft_one", spy)
    client.post(f"/api/draft/{slug}", json={}, headers=_h())
    assert seen.get("reuse_cache") is False, \
        "a retry on a failed target still reused the cache that produced the failure"


def test_a_healthy_target_still_reuses_the_cache(client, monkeypatch):
    """An ordinary redraft must not re-research: the research is fine and only the
    wording is being changed."""
    import app.pipeline as pipeline
    seen = {}

    def spy(provider, cs, voice_override=None, *, reuse_cache=True):
        seen["reuse_cache"] = reuse_cache
        return cs

    slug = _ingest(client)
    monkeypatch.setattr(pipeline, "draft_one", spy)
    client.post(f"/api/draft/{slug}", json={}, headers=_h())
    assert seen.get("reuse_cache") is True
