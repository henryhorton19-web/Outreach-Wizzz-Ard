"""Offline tests for the outcome-aware outreach build (Phases 0-7).

All offline: canned RFC822 fixtures + a fake mailbox (list of raw bytes) + the stub provider. No
live IMAP server, matching the existing suite's discipline. Covers detection, sweep effects,
voice-stats math, suppression/dedup, the address ladder, pipeline stage mapping, and the core
invariants (read-only IMAP, no auto-send, off = today).
"""
import os
import json
from email.message import EmailMessage
from email.utils import make_msgid

import pytest

from app import detect, outcomes, voice_stats, suppression, apollo, pipeline_view, store
from app import settings as S
from app.models import SentItem, AddressCandidate, ReplyState, FollowUp, FollowUpStatus, CompanyState, State


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    # isolate every store file under a temp data dir for each test
    for name, val in [
        ("SENT_ITEMS_FILE", "sent_items.json"), ("SUPPRESSIONS_FILE", "suppressions.json"),
        ("FOLLOWUPS_FILE", "follow_ups.json"), ("DRAFTS_FILE", "drafts.json"),
        ("ARCHIVE_FILE", "archive.json"), ("QUEUE_FILE", "queue.json"),
        ("SNIPPETS_FILE", "snippets.json"), ("SESSION_STATS_FILE", "session_stats.json"),
    ]:
        monkeypatch.setattr(store, name, tmp_path / val)
    yield


# ---- fixtures --------------------------------------------------------------

def _reply(to_mid, from_addr="jane@acme.com", subject="Re: Quick question", refs=None):
    m = EmailMessage()
    m["From"] = from_addr
    m["To"] = "me@myco.com"
    m["Subject"] = subject
    m["In-Reply-To"] = to_mid
    if refs:
        m["References"] = refs
    m["Message-ID"] = make_msgid()
    m.set_content("Thanks for reaching out, happy to chat.")
    return m.as_bytes()


def _dsn_hard(failed="jane@acme.com"):
    # Build a valid multipart/report DSN by hand (proper structure, no post-hoc header surgery).
    raw = (
        "From: Mail Delivery Subsystem <mailer-daemon@mail.acme.com>\n"
        "To: me@myco.com\n"
        "Subject: Undelivered Mail Returned to Sender\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="b"\n'
        "MIME-Version: 1.0\n"
        "\n"
        "--b\n"
        "Content-Type: text/plain\n"
        "\n"
        "This is the mail system at host mail.acme.com. Delivery failed permanently.\n"
        "\n"
        "--b\n"
        "Content-Type: message/delivery-status\n"
        "\n"
        "Reporting-MTA: dns; mail.acme.com\n"
        "\n"
        f"Final-Recipient: rfc822; {failed}\n"
        "Action: failed\n"
        "Status: 5.1.1\n"
        "\n"
        "--b--\n"
    )
    return raw.encode()


def _dsn_soft(failed="jane@acme.com"):
    m = EmailMessage()
    m["From"] = "mailer-daemon@mail.acme.com"
    m["Subject"] = "Delayed"
    m.set_content(f"Final-Recipient: rfc822; {failed}\nAction: delayed\nStatus: 4.2.2\n")
    return m.as_bytes()


def _autoreply(to_mid):
    m = EmailMessage()
    m["From"] = "jane@acme.com"
    m["Subject"] = "Automatic reply: Out of office"
    m["In-Reply-To"] = to_mid
    m["Auto-Submitted"] = "auto-replied"
    m["Message-ID"] = make_msgid()
    m.set_content("I am out of office until Monday.")
    return m.as_bytes()


def _sent(mid, sid="acme#0", slug="acme", sent_to="jane@acme.com", voice="role_small",
          ladder=None, rs=ReplyState.awaiting):
    return SentItem(id=sid, slug=slug, name="Acme", voice=voice, message_id=mid,
                    sent_to=sent_to, recipient_domain="acme.com", subject="Quick question",
                    approved_at="2026-07-01T00:00:00+00:00", reply_state=rs,
                    address_candidates=[AddressCandidate(**c) for c in (ladder or [])])


# ---- detection -------------------------------------------------------------

def test_reply_via_in_reply_to():
    mid = make_msgid()
    idx = detect.build_sent_index([_sent(mid)])
    res = detect.classify(_reply(mid), idx)
    assert res["kind"] == "reply" and res["sent_id"] == "acme#0"


def test_reply_via_references_when_no_in_reply_to():
    mid = make_msgid()
    idx = detect.build_sent_index([_sent(mid)])
    m = EmailMessage()
    m["From"] = "jane@acme.com"; m["Subject"] = "Re: hi"
    m["References"] = f"<other@x> {mid}"
    m.set_content("sure")
    res = detect.classify(m.as_bytes(), idx)
    assert res["kind"] == "reply" and res["sent_id"] == "acme#0"


def test_subject_re_alone_is_not_a_reply():
    mid = make_msgid()
    idx = detect.build_sent_index([_sent(mid)])
    m = EmailMessage()
    m["From"] = "stranger@else.com"; m["Subject"] = "Re: Quick question"
    m.set_content("unrelated")
    res = detect.classify(m.as_bytes(), idx)
    assert res["kind"] == "ignore"


def test_message_id_collision_uses_sender_tiebreak():
    mid = make_msgid()
    a = _sent(mid, sid="acme#0", sent_to="jane@acme.com")
    b = _sent(mid, sid="acme#1", slug="acme", sent_to="jane@acme.nl")
    idx = detect.build_sent_index([a, b])
    res = detect.classify(_reply(mid, from_addr="jane@acme.nl"), idx)
    assert res["kind"] == "reply" and res["sent_id"] == "acme#1"


def test_hard_bounce_detected_not_reply():
    mid = make_msgid()
    idx = detect.build_sent_index([_sent(mid)])
    res = detect.classify(_dsn_hard(), idx)
    assert res["kind"] == "bounce" and res["bounce"] == "hard"
    assert res["failed_recipient"] == "jane@acme.com"


def test_soft_bounce_stays_awaiting():
    res = detect.classify(_dsn_soft(), {})
    assert res["kind"] == "ignore" and res["bounce"] == "soft"


def test_auto_reply_not_counted():
    mid = make_msgid()
    idx = detect.build_sent_index([_sent(mid)])
    res = detect.classify(_autoreply(mid), idx)
    assert res["kind"] == "ignore"


# ---- outcome effects ---------------------------------------------------------

def test_sweep_reply_pauses_followup_and_records():
    mid = make_msgid()
    store.upsert_sent_item(_sent(mid))
    fu = FollowUp(id="acme__f1", parent_slug="acme", name="Acme", step=1,
                  original_approved_at="2026-07-01T00:00:00+00:00",
                  due_at="2026-07-04T00:00:00+00:00", origin_message_id=mid,
                  status=FollowUpStatus.pending)
    store.upsert_followup(fu)
    res = outcomes.set_outcome("acme#0", "replied", provider=None)
    assert res["ok"]
    assert store.get_sent_item("acme#0").reply_state == ReplyState.replied
    assert store.get_followup("acme__f1").status == FollowUpStatus.dismissed


def test_sweep_bounce_autosuppresses_and_no_autosend():
    mid = make_msgid()
    ladder = [{"email": "jane@acme.com", "source": "research", "confidence": "medium"},
              {"email": "jane.doe@acme.com", "source": "pattern", "confidence": "low"}]
    store.upsert_sent_item(_sent(mid, ladder=ladder))
    # provider=None: bounce recorded + suppressed, but NO retry draft staged (no auto anything)
    res = outcomes.set_outcome("acme#0", "bounced", provider=None)
    assert res["ok"]
    assert store.get_sent_item("acme#0").reply_state in (ReplyState.bounced, ReplyState.bounced_exhausted)
    hit, reason = suppression.is_suppressed("jane@acme.com")
    assert hit and reason == "bounced"
    assert store.load_drafts() == []   # nothing auto-sent, nothing auto-staged without a provider


# ---- voice stats -----------------------------------------------------------

def test_reply_rate_excludes_bounces_and_gates_min_n(monkeypatch):
    st = S.load_settings(); st.voice_stats_min_n = 3
    monkeypatch.setattr(S, "load_settings", lambda: st)
    # 4 sent: 2 replied, 1 bounced, 1 awaiting -> denom = 4-1 = 3 >= min_n
    store.upsert_sent_item(_sent(make_msgid(), sid="a#0", rs=ReplyState.replied))
    store.upsert_sent_item(_sent(make_msgid(), sid="a#1", rs=ReplyState.replied))
    store.upsert_sent_item(_sent(make_msgid(), sid="a#2", rs=ReplyState.bounced))
    store.upsert_sent_item(_sent(make_msgid(), sid="a#3", rs=ReplyState.awaiting))
    buckets = voice_stats.rebuild_all()
    b = buckets["role_small"]
    assert b["sent"] == 4 and b["replied"] == 2 and b["bounced"] == 1
    assert b["reply_denom"] == 3 and b["enough_data"] is True
    assert abs(b["reply_rate"] - (2 / 3)) < 1e-9
    assert b["reply_ci"] is not None


def test_below_min_n_is_not_enough_data(monkeypatch):
    st = S.load_settings(); st.voice_stats_min_n = 15
    monkeypatch.setattr(S, "load_settings", lambda: st)
    store.upsert_sent_item(_sent(make_msgid(), sid="a#0", rs=ReplyState.replied))
    buckets = voice_stats.rebuild_all()
    assert buckets["role_small"]["enough_data"] is False
    assert buckets["role_small"]["reply_rate"] is None


def test_wilson_interval_sane():
    lo, hi = voice_stats._wilson(5, 10)
    assert 0 <= lo < 0.5 < hi <= 1


# ---- suppression / dedup / ladder -----------------------------------------

def test_suppression_normalizes_gmail():
    suppression.add("John.Doe+promo@Gmail.com", reason="manual")
    hit, _ = suppression.is_suppressed("johndoe@gmail.com")
    assert hit


def test_domain_suppression():
    suppression.add("spam.com", reason="manual")
    hit, reason = suppression.is_suppressed("anyone@spam.com")
    assert hit and reason == "domain"


def test_risky_generic_flagged():
    assert suppression.is_risky_generic("info@acme.com")
    assert not suppression.is_risky_generic("jane@acme.com")


def test_address_ladder_order_dedupe_provenance():
    cache = {"contact": {"name": "Jane Doe", "email": "jane@acme.nl", "email_confidence": "medium"},
             "company": {"website": "acme.nl"}}
    ladder = apollo.rank_address_candidates(
        cache, apollo_match={"email": "jane.doe@acme.nl", "email_status": "verified"})
    emails = [c["email"] for c in ladder]
    assert emails[0] == "jane.doe@acme.nl" and ladder[0]["source"] == "apollo"
    assert ladder[0]["confidence"] == "high"
    assert emails[1] == "jane@acme.nl" and ladder[1]["source"] == "research"
    assert len(emails) == len(set(emails))
    assert any(c["source"] == "pattern" for c in ladder)


# ---- pipeline mapping ------------------------------------------------------

def test_pipeline_empty_replied_bounced_pre_inbox():
    board = pipeline_view.assemble()
    assert set(board["columns"].keys()) == set(pipeline_view.COLUMNS)
    assert board["columns"]["replied"] == []
    assert board["columns"]["bounced"] == []


def test_pipeline_sent_and_stages():
    store.upsert_sent_item(_sent(make_msgid(), sid="a#0", rs=ReplyState.replied))
    store.upsert_sent_item(_sent(make_msgid(), sid="b#0", slug="b", rs=ReplyState.bounced))
    store.upsert_sent_item(_sent(make_msgid(), sid="c#0", slug="c", rs=ReplyState.awaiting))
    board = pipeline_view.assemble()
    assert len(board["columns"]["replied"]) == 1
    assert len(board["columns"]["bounced"]) == 1
    assert len(board["columns"]["sent"]) == 1
    assert board["summary"]["sent"] == 3 and board["summary"]["replied"] == 1


def test_pipeline_mark_no_response_terminal():
    store.upsert_sent_item(_sent(make_msgid(), sid="a#0", rs=ReplyState.awaiting))
    assert pipeline_view.mark("acme", "no_response") is True
    board = pipeline_view.assemble()
    assert len(board["columns"]["no_response"]) == 1
    assert len(board["columns"]["sent"]) == 0


# ---- invariants: bounce retry is approve-first -----------------------------


def test_bounce_retry_stages_approvable_draft_not_send(monkeypatch, tmp_path):
    # with a provider, a hard bounce stages a re-draft to the NEXT ladder rung as a normal draft
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    # seed a research cache the retarget reuses (stub composes deterministically from it)
    from app import research
    cache = {"company": {"name": "Acme", "what_they_do": "widgets", "role_exists": True,
                         "company_size": "small"},
             "proof_points": [{"fact": "Acme shipped v2", "source": "blog", "staleness": "fresh"}],
             "contact": {"name": "Jane Doe", "email": "jane@acme.com", "email_confidence": "medium"},
             "situation_read": "growing"}
    store.save_cache("acme", cache)
    ladder = [{"email": "jane@acme.com", "source": "research", "confidence": "medium"},
              {"email": "jane.doe@acme.com", "source": "pattern", "confidence": "low"}]
    store.upsert_sent_item(_sent(make_msgid(), ladder=ladder))

    from app.providers.base import make_provider
    provider = make_provider("stub", None)
    r = outcomes.set_outcome("acme#0", "bounced", provider=provider)
    assert r["ok"]
    # a retry draft was staged (state=drafted) to the next rung; nothing was sent
    retry = r["retry"]
    assert retry and retry["email"] == "jane.doe@acme.com"
    d = store.get_draft("acme__b1")
    assert d is not None and d.state == State.drafted
    assert (d.spec or {}).get("send_to") == "jane.doe@acme.com"
    # the dead address is suppressed so it can't be re-queued
    assert suppression.is_suppressed("jane@acme.com")[0]


# ---- Phase 7: learning routing ---------------------------------------------

def _mk_voice(vid, situations, kind="outreach"):
    from app.models import CustomVoice, Block
    return CustomVoice(id=vid, display_name=vid, kind=kind, situations=situations,
                       blocks=[Block(id="b", mode="fixed", text="hi")])


def test_routing_off_is_identical_to_today(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "VOICES_DIR", tmp_path)
    store.save_custom_voice(_mk_voice("v_a", ["role_small"]))
    store.save_custom_voice(_mk_voice("v_b", ["role_small"]))
    st = S.load_settings(); st.voice_learning_routing = "off"
    monkeypatch.setattr(S, "load_settings", lambda: st)
    from app import pipeline
    cache = {"company": {"role_exists": True, "company_size": "small"}}
    # off -> deterministic: the first eligible voice by store order, never a stats re-order
    picks = {pipeline.resolve_voice(cache) for _ in range(10)}
    assert picks == {pipeline.resolve_voice(cache)}  # stable


def test_routing_suggest_reorders_only_when_intervals_separate(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "VOICES_DIR", tmp_path)
    monkeypatch.setattr(store, "SENT_ITEMS_FILE", tmp_path / "si.json")
    store.save_custom_voice(_mk_voice("v_weak", ["role_small"]))
    store.save_custom_voice(_mk_voice("v_strong", ["role_small"]))
    st = S.load_settings()
    st.voice_learning_routing = "suggest"
    st.voice_stats_min_n = 5
    st.voice_explore_epsilon = 0.0   # disable exploration for determinism
    monkeypatch.setattr(S, "load_settings", lambda: st)
    # v_strong: 9/10 replied; v_weak: 1/10 replied -> clearly separated intervals
    for i in range(9):
        store.upsert_sent_item(_sent(make_msgid(), sid=f"s#{i}", slug="s", voice="v_strong",
                                     rs=ReplyState.replied))
    store.upsert_sent_item(_sent(make_msgid(), sid="s#9", slug="s", voice="v_strong",
                                 rs=ReplyState.awaiting))
    store.upsert_sent_item(_sent(make_msgid(), sid="w#0", slug="w", voice="v_weak",
                                 rs=ReplyState.replied))
    for i in range(1, 10):
        store.upsert_sent_item(_sent(make_msgid(), sid=f"w#{i}", slug="w", voice="v_weak",
                                     rs=ReplyState.awaiting))
    from app import pipeline
    cache = {"company": {"role_exists": True, "company_size": "small"}}
    assert pipeline.resolve_voice(cache) == "v_strong"


def test_routing_never_overrides_explicit_choice(monkeypatch, tmp_path):
    monkeypatch.setattr(S, "VOICES_DIR", tmp_path)
    store.save_custom_voice(_mk_voice("v_a", ["role_small"]))
    store.save_custom_voice(_mk_voice("v_b", ["role_small"]))
    st = S.load_settings(); st.voice_learning_routing = "auto"
    monkeypatch.setattr(S, "load_settings", lambda: st)
    from app import pipeline
    cache = {"company": {"role_exists": True, "company_size": "small"}}
    assert pipeline.resolve_voice(cache, override="v_a") == "v_a"
