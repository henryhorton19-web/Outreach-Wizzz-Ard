"""HTTP end-to-end for manual outcome control + person-aware retarget, fully offline.

Drives the real FastAPI app (session-token auth, stub provider): approve a target whose research
cache carries an alternate contact, then mark it BOUNCED by hand via /api/sent/{id}/outcome and
assert the app auto-suppresses the dead address and stages an approvable re-draft RE-ADDRESSED to a
DIFFERENT person — never sending. Also exercises the manual reply-mark (follow-up pause), the
awaiting bucket, and the explicit retarget endpoint.
"""
import os
from email.message import EmailMessage
from email.utils import make_msgid
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


def _sid(x):
    # SentItem ids contain '#' (slug#n); it must be percent-encoded in a URL path (the frontend
    # uses encodeURIComponent). Without this the '#' is parsed as a fragment and the route 404s.
    return quote(x, safe="")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_PROVIDER", "stub")
    monkeypatch.setenv("PARIS_NO_KEYRING", "1")
    import importlib
    import app.settings as S
    importlib.reload(S)
    import app.store as store
    importlib.reload(store)
    import app.server as server
    importlib.reload(server)
    S.ensure_seeded()
    c = TestClient(server.app)
    c._H = {"x-paris-token": S.SESSION_TOKEN}
    c._S = S
    c._store = store
    return c


CACHE_WITH_ALT = {
    "company": {"name": "Acme", "what_they_do": "widgets", "role_exists": True,
                "company_size": "small", "work_mode": "remote_english",
                "working_language": "English", "disqualified": False},
    "proof_points": [{"fact": "Acme shipped v2", "source": "https://x/a", "staleness": "fresh"}],
    "recent_point": {"present": True, "kind": "raise", "detail": "seed round",
                     "source": "https://x/c", "staleness": "fresh"},
    "contact": {"status": "found", "name": "Jane Doe", "title": "CEO", "role_basis": "founder",
                "email": "jane@acme.com", "email_confidence": "medium", "contact_verified": True},
    "contacts_alt": [{"name": "Sam Alt", "title": "COO", "role_basis": "founder",
                      "email": "sam@acme.com", "email_confidence": "low"}],
    "situation_read": "growing",
    "evidence_sources": ["https://x/a"], "overall_confidence": "medium",
}


def _approve_with_alt(c, name="Acme"):
    H = c._H
    c.post("/api/ingest", json={"text": name}, headers=H)
    slug = c._store.load_queue()[0]["slug"]
    c.post(f"/api/queue/{slug}/draft", headers=H)
    c._store.save_cache(slug, CACHE_WITH_ALT)          # reused by draft (reuse_cache=True)
    r = c.post(f"/api/draft/{slug}", json={"reuse_cache": True}, headers=H).json()
    assert r["state"] == "drafted", r
    c.post(f"/api/companies/{slug}/approve", headers=H)
    return slug


def test_manual_bounce_stages_readdressed_retry_over_http(client):
    c = client; H = c._H; store = c._store
    slug = _approve_with_alt(c)
    sent = store.load_sent_items()
    assert len(sent) == 1
    si = sent[0]
    assert si.sent_to == "jane@acme.com"
    # the persisted ladder carries the alt person (known-before-patterns ordering)
    assert any(a.tier == "alt_person" and a.person_name == "Sam Alt" for a in si.address_candidates)

    # mark bounced BY HAND — same effects as the sweep, approve-first
    res = c.post(f"/api/sent/{_sid(si.id)}/outcome", json={"outcome": "bounced"}, headers=H).json()
    assert res["ok"] and res["retry"] and res["retry"]["person"] == "Sam Alt"
    assert res["retry"]["tier"] == "alt_person"

    # dead address auto-suppressed; nothing sent; a re-addressed retry draft is staged
    assert store.load_suppressions(), "manual bounce must auto-suppress the dead address"
    d = store.get_draft(f"{slug}__b1")
    assert d is not None and d.state.value == "drafted"
    assert (d.spec or {}).get("send_to") == "sam@acme.com"
    assert d.cache["contact"]["name"] == "Sam Alt"           # re-addressed to a DIFFERENT person
    # parent cache untouched
    assert store.load_cache(slug)["contact"]["name"] == "Jane Doe"

    # Triage reflects the bounce with a next-rung preview and marked-by-hand provenance
    triage = c.get("/api/triage", headers=H).json()
    assert triage["counts"]["bounced"] == 1
    row = triage["bounced"][0]
    assert row["outcome_source"] == "manual"


def test_manual_reply_mark_pauses_followup_over_http(client):
    c = client; H = c._H; store = c._store
    slug = _approve_with_alt(c)
    assert len(store.load_followups()) == 1                   # enrolled on approval
    si = store.load_sent_items()[0]
    r = c.post(f"/api/sent/{_sid(si.id)}/outcome", json={"outcome": "replied"}, headers=H).json()
    assert r["ok"] and r["followup_paused"] is True
    assert str(store.load_followups()[0].status) in ("FollowUpStatus.dismissed", "dismissed")
    # correcting a false positive flips it back to awaiting and into that bucket
    c.post(f"/api/sent/{_sid(si.id)}/outcome", json={"outcome": "awaiting"}, headers=H)
    triage = c.get("/api/triage", headers=H).json()
    assert triage["counts"]["awaiting"] == 1


def test_explicit_retarget_endpoint_over_http(client):
    c = client; H = c._H; store = c._store
    slug = _approve_with_alt(c)
    si = store.load_sent_items()[0]
    r = c.post(f"/api/sent/{_sid(si.id)}/retarget",
               json={"email": "jordan@acme.com", "name": "Jordan Lee", "title": "Head of Ops"},
               headers=H).json()
    assert r["ok"] and r["email"] == "jordan@acme.com"
    d = store.get_draft(f"{slug}__b1")
    assert d.cache["contact"]["name"] == "Jordan Lee" and d.spec["send_to"] == "jordan@acme.com"


def test_bad_outcome_rejected_over_http(client):
    c = client; H = c._H; store = c._store
    _approve_with_alt(c)
    si = store.load_sent_items()[0]
    resp = c.post(f"/api/sent/{_sid(si.id)}/outcome", json={"outcome": "nonsense"}, headers=H)
    assert resp.status_code == 400

def test_sent_card_shows_and_marks_outcome_over_http(client):
    c = client; H = c._H
    _approve_with_alt(c)
    # the Sent list joins in the outcome: a fresh send is "awaiting" and carries its sent_id
    arch = c.get("/api/archive", headers=H).json()["archive"]
    assert arch and arch[0].get("sent_id")
    assert arch[0].get("reply_state") == "awaiting"
    sid = arch[0]["sent_id"]

    # mark it bounced straight from the Sent view's endpoint
    res = c.post(f"/api/sent/{_sid(sid)}/outcome", json={"outcome": "bounced"}, headers=H)
    assert res.status_code == 200

    # the Sent list now reflects the new outcome + manual provenance
    arch2 = c.get("/api/archive", headers=H).json()["archive"]
    row = next(r for r in arch2 if r.get("sent_id") == sid)
    assert row["reply_state"] in ("bounced", "bounced_exhausted")
    assert row["outcome_source"] == "manual"
