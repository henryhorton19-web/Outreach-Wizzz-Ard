"""A manually-edited recipient address must persist and must be what gets sent.

Reproduced before this fix: PUT /api/companies/{slug}/email returned 200 and the
stored contact email was unchanged -- even when contact_email was sent explicitly,
because the endpoint had no parameter for it. The UI mutated its local copy, so
the drawer looked correct until reload, and approve derived send_to from the
untouched server-side value.
"""
import time

import pytest

from app import settings as S, store


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


def _h():
    return {"x-wizzard-token": S.SESSION_TOKEN}


def _drafted(client, name=None):
    if not name:
        name = f"Acme Corp {time.time_ns()}"
    client.post("/api/ingest", json={"text": name}, headers=_h())
    slug = client.get("/api/queue", headers=_h()).json()["queue"][0]["slug"]
    client.post(f"/api/queue/{slug}/draft", headers=_h())
    job = client.post("/api/draft", json={"slugs": [slug]}, headers=_h()).json()
    for _ in range(60):
        if client.get(f"/api/draft/job/{job['job_id']}", headers=_h()).json()["state"] != "running":
            break
        time.sleep(0.15)
    return slug


def test_editing_the_recipient_persists(client):
    slug = _drafted(client)
    r = client.put(f"/api/companies/{slug}/email",
                   json={"subject": "S", "email": "Body", "contact_email": "manual@acme.io"},
                   headers=_h())
    assert r.status_code == 200, r.text[:200]
    stored = (store.get_draft(slug).cache.get("contact") or {}).get("email")
    assert stored == "manual@acme.io", f"edit was discarded; stored is still {stored!r}"


def test_the_edited_recipient_is_returned_to_the_ui(client):
    slug = _drafted(client)
    body = client.put(f"/api/companies/{slug}/email",
                      json={"subject": "S", "email": "B", "contact_email": "manual@acme.io"},
                      headers=_h()).json()
    contact = body.get("contact") or {}
    assert contact.get("email") == "manual@acme.io", \
        "the response does not reflect the edit, so the UI cannot verify it landed"


def test_an_invalid_address_is_rejected_not_silently_kept(client):
    slug = _drafted(client)
    r = client.put(f"/api/companies/{slug}/email",
                   json={"subject": "S", "email": "B", "contact_email": "not-an-email"},
                   headers=_h())
    assert r.status_code == 400, f"expected 400 for a malformed address, got {r.status_code}"


def test_omitting_contact_email_leaves_the_recipient_untouched(client):
    """Backwards compatibility: a body-only edit must not blank the recipient."""
    slug = _drafted(client)
    before = (store.get_draft(slug).cache.get("contact") or {}).get("email")
    client.put(f"/api/companies/{slug}/email",
               json={"subject": "S", "email": "Body only"}, headers=_h())
    after = (store.get_draft(slug).cache.get("contact") or {}).get("email")
    assert after == before, "a body-only edit changed the recipient"
