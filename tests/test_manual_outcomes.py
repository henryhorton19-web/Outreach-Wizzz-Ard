"""Offline tests for manual outcome control + person-aware bounce re-targeting.

All offline: the stub provider + isolated store files + hand-built caches/ladders. Covers the manual
detection surface (mark replied/bounced/no-response/reset by hand → the SAME effects the automated
sweep fires), the person-aware address ladder, and the re-addressed bounce re-draft to a DIFFERENT
person. Mirrors the discipline of test_outcome_aware.py.
"""
import pytest

from app import outcomes, apollo, pipeline, research, suppression, store, voice_stats
from app import settings as S
from app.models import SentItem, AddressCandidate, ReplyState, FollowUp, FollowUpStatus, State
from app.providers.base import make_provider


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    for name, val in [
        ("SENT_ITEMS_FILE", "sent_items.json"), ("SUPPRESSIONS_FILE", "suppressions.json"),
        ("FOLLOWUPS_FILE", "follow_ups.json"), ("DRAFTS_FILE", "drafts.json"),
        ("ARCHIVE_FILE", "archive.json"), ("QUEUE_FILE", "queue.json"),
        ("SNIPPETS_FILE", "snippets.json"), ("SESSION_STATS_FILE", "session_stats.json"),
    ]:
        monkeypatch.setattr(store, name, tmp_path / val)
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path)
    yield


CACHE = {
    "company": {"name": "Acme", "what_they_do": "widgets", "role_exists": True, "company_size": "small"},
    "proof_points": [{"fact": "Acme shipped v2", "source": "blog", "staleness": "fresh"}],
    "contact": {"name": "Jane Doe", "email": "jane@acme.com", "email_confidence": "medium"},
    "contacts_alt": [{"name": "Sam Alt", "title": "COO", "email": "sam@acme.com", "email_confidence": "low"}],
    "situation_read": "growing",
}


def _sent(sid="acme#0", slug="acme", sent_to="jane@acme.com", rs=ReplyState.awaiting,
          ladder=None, voice="role_small"):
    return SentItem(id=sid, slug=slug, name="Acme", voice=voice, message_id="<m@x>",
                    sent_to=sent_to, recipient_domain="acme.com", subject="Quick question",
                    approved_at="2026-07-01T00:00:00+00:00", reply_state=rs,
                    address_candidates=[AddressCandidate(**c) for c in (ladder or [])])


# ---- manual outcomes -------------------------------------------------------

def test_manual_mark_replied_pauses_followup_and_counts():
    store.upsert_sent_item(_sent())
    store.upsert_followup(FollowUp(id="acme__f1", parent_slug="acme", name="Acme",
                                   origin_message_id="<m@x>", step=1,
                                   status=FollowUpStatus.pending))
    res = outcomes.set_outcome("acme#0", "replied", source="manual")
    assert res["ok"] and res["followup_paused"] is True
    si = store.get_sent_item("acme#0")
    assert si.reply_state == ReplyState.replied and si.outcome_source == "manual"
    assert store.get_followup("acme__f1").status == FollowUpStatus.dismissed
    # voice_stats folds live over reply_state — the reply is counted with no separate write
    b = voice_stats.rebuild_all().get("role_small")
    assert b and b["replied"] == 1


def test_manual_mark_bounced_suppresses_and_stages_next_rung():
    store.save_cache("acme", CACHE)
    ladder = [{"email": "jane@acme.com", "source": "research", "confidence": "medium",
               "person_name": "Jane Doe", "tier": "primary_person"},
              {"email": "sam@acme.com", "source": "research", "confidence": "low",
               "person_name": "Sam Alt", "person_title": "COO", "tier": "alt_person"}]
    store.upsert_sent_item(_sent(ladder=ladder))
    provider = make_provider("stub", None)
    res = outcomes.set_outcome("acme#0", "bounced", provider=provider, source="manual")
    assert res["ok"] and res["retry"] and res["retry"]["email"] == "sam@acme.com"
    assert res["retry"]["person"] == "Sam Alt" and res["retry"]["tier"] == "alt_person"
    assert suppression.is_suppressed("jane@acme.com")[0]
    d = store.get_draft("acme__b1")
    assert d is not None and d.state == State.drafted
    assert (d.spec or {}).get("send_to") == "sam@acme.com"


def test_manual_mark_bounced_without_provider_marks_and_suppresses_only():
    store.upsert_sent_item(_sent(ladder=[{"email": "jane@acme.com", "source": "research",
                                          "confidence": "medium"}]))
    res = outcomes.set_outcome("acme#0", "bounced", provider=None, source="manual")
    assert res["ok"] and res["retry"] is None and res["exhausted"] is True
    assert store.get_sent_item("acme#0").reply_state == ReplyState.bounced_exhausted
    assert suppression.is_suppressed("jane@acme.com")[0]
    assert store.load_drafts() == []


def test_reset_awaiting_corrects_false_positive_reply():
    store.upsert_sent_item(_sent(rs=ReplyState.replied))
    res = outcomes.set_outcome("acme#0", "awaiting", source="manual")
    assert res["ok"]
    si = store.get_sent_item("acme#0")
    assert si.reply_state == ReplyState.awaiting and si.detected_at is None


def test_reset_awaiting_lifts_only_bounce_suppression():
    # a manually-added do-not-contact must survive a reset; a bounce-added one is lifted
    suppression.add("jane@acme.com", reason="manual", source="manual")
    store.upsert_sent_item(_sent(rs=ReplyState.awaiting,
                                 ladder=[{"email": "jane@acme.com", "source": "research", "confidence": "low"}]))
    outcomes.set_outcome("acme#0", "bounced", provider=None)     # marks bounced; jane already suppressed
    outcomes.set_outcome("acme#0", "awaiting")                   # reset
    # still suppressed because the entry's reason is 'manual', not 'bounced'
    assert suppression.is_suppressed("jane@acme.com")[0]

    # now a purely bounce-added suppression IS lifted on reset
    store.upsert_sent_item(_sent(sid="beta#0", slug="beta", sent_to="x@beta.com", rs=ReplyState.awaiting,
                                 ladder=[{"email": "x@beta.com", "source": "research", "confidence": "low"}]))
    outcomes.set_outcome("beta#0", "bounced", provider=None)
    assert suppression.is_suppressed("x@beta.com")[0]
    outcomes.set_outcome("beta#0", "awaiting")
    assert not suppression.is_suppressed("x@beta.com")[0]


def test_manual_no_response_and_reopen():
    store.upsert_sent_item(_sent())
    outcomes.set_outcome("acme#0", "no_response")
    assert store.get_sent_item("acme#0").pipeline_flag == "no_response"
    outcomes.set_outcome("acme#0", "reopen")
    assert store.get_sent_item("acme#0").pipeline_flag == "reopened"


# ---- person-aware ladder ---------------------------------------------------

def test_ladder_includes_alt_person_and_known_precede_patterns():
    lad = apollo.rank_address_candidates(CACHE)
    alt_idx = next(i for i, c in enumerate(lad) if c["tier"] == "alt_person")
    first_pattern = next(i for i, c in enumerate(lad) if c["source"] == "pattern")
    # a named different person's KNOWN address outranks pattern-guesses of the primary
    assert alt_idx < first_pattern
    assert lad[alt_idx]["person_name"] == "Sam Alt"
    assert lad[0]["tier"] == "primary_person" and lad[0]["email"] == "jane@acme.com"
    emails = [c["email"] for c in lad]
    assert len(emails) == len(set(emails))            # dedup preserved


def test_ladder_no_alts_is_todays_behaviour():
    cache = {"contact": {"name": "Jane Doe", "email": "jane@acme.nl", "email_confidence": "medium"},
             "company": {"website": "acme.nl"}}
    lad = apollo.rank_address_candidates(
        cache, apollo_match={"email": "jane.doe@acme.nl", "email_status": "verified"})
    emails = [c["email"] for c in lad]
    assert emails[0] == "jane.doe@acme.nl" and lad[0]["source"] == "apollo"
    assert emails[1] == "jane@acme.nl" and lad[1]["source"] == "research"
    assert all(c["tier"] == "primary_person" for c in lad)   # no alts → all primary
    assert any(c["source"] == "pattern" for c in lad)


def test_sanitize_drops_alt_equal_to_primary_and_caps_two():
    cache = {"contact": {"name": "Jane Doe"},
             "contacts_alt": [{"name": "Jane Doe"}, {"name": "A"}, {"name": "B"}, {"name": "C"}]}
    out = research._sanitize_cache(cache)
    names = [a["name"] for a in out["contacts_alt"]]
    assert "Jane Doe" not in names and len(names) == 2 and names == ["A", "B"]


# ---- re-addressing on escalation -------------------------------------------

def test_retarget_readdresses_to_alt_person():
    store.save_cache("acme", CACHE)
    si = _sent()
    provider = make_provider("stub", None)
    cs = pipeline.draft_retarget(provider, si, "sam@acme.com", bounce_n=1,
                                 new_person={"name": "Sam Alt", "title": "COO", "confidence": "low"})
    assert cs.state == State.drafted
    # the working-copy cache + spec now name the ALT person, and the address is the alt's
    assert cs.cache["contact"]["name"] == "Sam Alt"
    assert cs.spec["contact_name"] == "Sam Alt" and cs.spec["contact_first"] == "Sam"
    assert cs.spec["send_to"] == "sam@acme.com"
    assert "Sam Alt" in cs.status_pill
    # the persisted parent cache is untouched (working-copy override never saved)
    assert store.load_cache("acme")["contact"]["name"] == "Jane Doe"


def test_bounce_escalates_to_different_person(monkeypatch):
    store.save_cache("acme", CACHE)
    st = S.load_settings(); st.max_bounce_retries = 3
    monkeypatch.setattr(S, "load_settings", lambda: st)
    ladder = apollo.rank_address_candidates(CACHE)          # jane known, sam known, then patterns
    store.upsert_sent_item(_sent(ladder=[c for c in ladder]))
    provider = make_provider("stub", None)
    retry = outcomes.retarget_after_bounce(provider, store.get_sent_item("acme#0"), "jane@acme.com")
    assert retry and retry["person"] == "Sam Alt" and retry["tier"] == "alt_person"
    d = store.get_draft("acme__b1")
    assert d.cache["contact"]["name"] == "Sam Alt" and d.spec["send_to"] == "sam@acme.com"


def test_manual_retarget_specific_person_via_pipeline():
    store.save_cache("acme", CACHE)
    si = _sent()
    provider = make_provider("stub", None)
    cs = pipeline.draft_retarget(provider, si, "jordan@acme.com", bounce_n=1,
                                 new_person={"name": "Jordan Lee", "title": "Head of Ops", "confidence": "low"})
    assert cs.spec["send_to"] == "jordan@acme.com" and cs.cache["contact"]["name"] == "Jordan Lee"


def _sent_with_copy(body, to_name="Jane Doe", ladder=None):
    from app.models import AddressCandidate
    si = SentItem(id="acme#0", slug="acme", name="Acme", voice="role_small",
                  message_id="<m@x>", sent_to="jane@acme.com", to_name=to_name,
                  recipient_domain="acme.com", subject="Quick question",
                  approved_subject="Quick question", approved_body=body,
                  approved_at="2026-07-01T00:00:00+00:00",
                  address_candidates=[AddressCandidate(**c) for c in (ladder or [])])
    return si


def test_retry_reuses_approved_copy_verbatim_same_person():
    store.save_cache("acme", CACHE)
    body = "Hi Jane,\n\nLoved what you shipped. Coffee?\n\nAlex"
    store.upsert_sent_item(_sent_with_copy(body))
    cs = pipeline.draft_retarget(None, store.get_sent_item("acme#0"), "jane.doe@acme.com", bounce_n=1)
    assert cs.final_email == body                      # byte-for-byte, no recompose
    assert cs.subject == "Quick question"
    assert cs.spec["send_to"] == "jane.doe@acme.com"


def test_retry_readdresses_greeting_for_different_person():
    store.save_cache("acme", CACHE)
    body = "Hi Jane,\n\nLoved what you shipped. Coffee?\n\nAlex"
    store.upsert_sent_item(_sent_with_copy(body))
    cs = pipeline.draft_retarget(None, store.get_sent_item("acme#0"), "sam@acme.com", bounce_n=1,
                                 new_person={"name": "Sam Alt", "title": "COO", "confidence": "low"})
    assert cs.final_email.startswith("Hi Sam,")        # greeting re-addressed
    assert "Loved what you shipped. Coffee?" in cs.final_email   # the rest is preserved
    assert cs.cache["contact"]["name"] == "Sam Alt"
    assert cs.spec["send_to"] == "sam@acme.com"


def test_retry_reuse_stages_without_a_provider():
    store.save_cache("acme", CACHE)
    ladder = [{"email": "jane@acme.com", "source": "research", "confidence": "medium"},
              {"email": "jane.doe@acme.com", "source": "pattern", "confidence": "low"}]
    store.upsert_sent_item(_sent_with_copy("Hi Jane,\n\nX\n\nAlex", ladder=ladder))
    retry = outcomes.retarget_after_bounce(None, store.get_sent_item("acme#0"), "jane@acme.com")
    assert retry is not None                            # relaxed gate: no provider needed to reuse
    assert store.get_draft("acme__b1").final_email.startswith("Hi Jane,")
