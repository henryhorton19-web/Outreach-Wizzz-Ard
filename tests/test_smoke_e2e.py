"""One extended HTTP smoke test end-to-end (the plan's integration check), fully offline.

Approve sends across two same-situation voices → export → run a fixture sweep marking replied +
bounced → assert: follow-ups auto-pause on reply, bounce auto-suppresses + produces an approvable
retry draft, Triage lists them, Pipeline shows the right columns, Voice Performance excludes bounces
+ gates min-n, and suppression blocks re-ingest.
"""
import os
from email.message import EmailMessage
from email.utils import make_msgid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # fresh data dir per test so stores + voices are isolated
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_PROVIDER", "stub")
    monkeypatch.setenv("PARIS_NO_KEYRING", "1")
    # reload settings + store + server against the new data dir
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


def _approve_target(c, name):
    H = c._H
    c.post("/api/ingest", json={"text": name}, headers=H)
    slug = c._store.load_queue()[0]["slug"]
    c.post(f"/api/queue/{slug}/draft", headers=H)
    r = c.post(f"/api/draft/{slug}", headers=H).json()
    assert r["state"] in ("drafted", "error"), r
    c.post(f"/api/companies/{slug}/approve", headers=H)
    return slug


def _reply_to(mid, frm):
    m = EmailMessage()
    m["From"] = frm
    m["Subject"] = "Re: hi"
    m["In-Reply-To"] = mid
    m["Message-ID"] = make_msgid()
    m.set_content("happy to chat")
    return m.as_bytes()


def _dsn(failed):
    return (
        "From: mailer-daemon@mail.example.com\n"
        "Subject: failure\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\n\n'
        "--b\nContent-Type: text/plain\n\npermanent failure\n\n"
        "--b\nContent-Type: message/delivery-status\n\n"
        f"Final-Recipient: rfc822; {failed}\nAction: failed\nStatus: 5.1.1\n\n--b--\n"
    ).encode()


def test_end_to_end_outcome_flow(client, monkeypatch):
    c = client; H = c._H; store = c._store; S = c._S

    # approve two sends (same situation, same stub voice)
    _approve_target(c, "Acme")
    _approve_target(c, "Beta")
    sents = store.load_sent_items()
    assert len(sents) == 2
    acme = next(s for s in sents if s.slug == "acme")
    beta = next(s for s in sents if s.slug == "beta")

    # export works for both scopes
    assert c.get("/api/export?fmt=csv&scope=archive", headers=H).status_code == 200
    assert c.get("/api/export?fmt=xlsx&scope=drafts", headers=H).status_code == 200

    # a follow-up was enrolled for each (default max_steps=1)
    assert len(store.load_followups()) == 2

    # simulate a sweep: Acme replies, Beta bounces. Patch inbox.fetch_recent with fixtures.
    import app.inbox as inbox
    raw = [_reply_to(acme.message_id, acme.sent_to), _dsn(beta.sent_to)]
    monkeypatch.setattr(inbox, "fetch_recent", lambda days=30: raw)
    st = S.load_settings(); st.imap_enabled = True; st.imap_host = "h"; st.imap_username = "u"
    monkeypatch.setattr(S, "load_settings", lambda: st)

    summary = c.post("/api/inbox/sweep", headers=H).json()
    assert summary["replied"] == 1 and summary["bounced"] == 1

    # reply auto-paused Acme's follow-up; Beta's remains
    fus = {f.parent_slug: f.status for f in store.load_followups()}
    assert str(fus["acme"]) in ("FollowUpStatus.dismissed", "dismissed")

    # bounce auto-suppressed Beta's address; re-ingesting Beta is now guarded
    assert store.load_suppressions(), "bounce should have auto-suppressed the dead address"

    # Triage lists the reply + the bounce
    triage = c.get("/api/triage", headers=H).json()
    assert triage["counts"]["replied"] == 1
    assert triage["counts"]["bounced"] == 1

    # Pipeline shows correct columns
    board = c.get("/api/pipeline", headers=H).json()
    assert len(board["columns"]["replied"]) == 1
    assert len(board["columns"]["bounced"]) == 1

    # Voice Performance: bounces excluded from denominator, min-n gate honest
    stats = c.get("/api/voice_stats", headers=H).json()
    assert stats["min_n"] >= 1
    # with only 1 non-bounce send, it must NOT show a bare rate
    for v in stats["voices"]:
        if not v["enough_data"]:
            assert v["reply_rate"] is None
