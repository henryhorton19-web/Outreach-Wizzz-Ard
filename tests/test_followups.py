"""Tests for the automated follow-up feature.

Covers: event-driven enrolment on approval, the 3-7-7 cadence timing, the step cap,
the oldest-first Work-Queue ordering, elapsed/due labels, lazy follow-up draft generation
via the stub provider, approval of a follow-up through the EXISTING approve path (status flip
+ re-enrolment of the next step), dismissal, and idempotency.
"""
import pytest
from datetime import datetime, timezone, timedelta

from app import settings as S


@pytest.fixture
def clean_data_dir(tmp_path, monkeypatch):
    from app import store
    monkeypatch.setenv("PARIS_PROVIDER", "stub")
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    S.DATA_DIR = tmp_path
    S.CACHE_DIR = tmp_path / "caches"
    S.BATCH_DIR = tmp_path / "batches"
    S.AUDIT_DIR = tmp_path / "audit"
    S.VOICES_DIR = tmp_path / "voices"
    S.ATTACH_DIR = tmp_path / "attachments"
    S.SETTINGS_FILE = tmp_path / "settings.json"
    for d in (S.DATA_DIR, S.CACHE_DIR, S.BATCH_DIR, S.AUDIT_DIR, S.VOICES_DIR, S.ATTACH_DIR):
        d.mkdir(parents=True, exist_ok=True)
    # store binds its file/dir constants at import from the original DATA_DIR; rebind for isolation
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    monkeypatch.setattr(store, "ARCHIVE_FILE", tmp_path / "archive.json")
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(store, "FOLLOWUPS_FILE", tmp_path / "follow_ups.json")
    S.ensure_seeded()
    yield tmp_path


def _iso(dt):
    return dt.isoformat()


def _approved_cs(slug="acme_ai", name="Acme AI", approved_days_ago=0, subject="Operations role",
                 body="Original email body.", voice="role_small", email="jane@acme.example"):
    """A CompanyState as it looks right after approval."""
    from app.models import CompanyState, State
    when = datetime.now(timezone.utc) - timedelta(days=approved_days_ago)
    return CompanyState(
        slug=slug, name=name, state=State.ready, voice=voice,
        subject=subject, machine_email=body, final_email=body,
        approved_at=_iso(when),
        spec={"send_to": email},
        cache={"contact": {"name": "Jane Doe", "email": email}},
    )


# ---- enrolment -------------------------------------------------------------

def test_approval_enrolls_followup(clean_data_dir):
    from app import followups, store
    st = S.load_settings(); st.follow_up_enabled = True; st.follow_up_max_steps = 1; S.save_settings(st)

    fu = followups.enroll_from_approval(_approved_cs())
    assert fu is not None
    assert fu.id == "acme_ai__f1"
    assert fu.step == 1
    assert fu.parent_slug == "acme_ai"
    assert fu.status.value == "pending"
    # persisted
    assert store.get_followup("acme_ai__f1") is not None


def test_due_at_uses_first_delay_from_approval(clean_data_dir):
    from app import followups
    st = S.load_settings()
    st.follow_up_enabled = True; st.follow_up_max_steps = 3; st.follow_up_delay_days = [3, 7, 7]
    S.save_settings(st)

    approved = datetime.now(timezone.utc)
    cs = _approved_cs()
    cs.approved_at = _iso(approved)
    fu = followups.enroll_from_approval(cs)
    due = datetime.fromisoformat(fu.due_at)
    # first follow-up is due ~3 days after approval (the 3-7-7 cadence)
    assert abs((due - approved).total_seconds() - 3 * 86400) < 5


def test_disabled_does_not_enroll(clean_data_dir):
    from app import followups
    st = S.load_settings(); st.follow_up_enabled = False; S.save_settings(st)
    assert followups.enroll_from_approval(_approved_cs()) is None


def test_zero_max_steps_does_not_enroll(clean_data_dir):
    from app import followups
    st = S.load_settings(); st.follow_up_enabled = True; st.follow_up_max_steps = 0; S.save_settings(st)
    assert followups.enroll_from_approval(_approved_cs()) is None


def test_enrolment_is_idempotent(clean_data_dir):
    from app import followups, store
    st = S.load_settings(); st.follow_up_max_steps = 1; S.save_settings(st)
    cs = _approved_cs()
    assert followups.enroll_from_approval(cs) is not None
    assert followups.enroll_from_approval(cs) is None            # no duplicate
    assert len(store.load_followups()) == 1


def test_cap_blocks_beyond_max_steps(clean_data_dir):
    from app import followups
    st = S.load_settings(); st.follow_up_max_steps = 1; S.save_settings(st)
    # approving follow-up #1 (slug acme__f1) would be step 2 -> blocked at cap 1
    fu1 = _approved_cs(slug="acme_ai__f1")
    assert followups.enroll_from_approval(fu1) is None


def test_multistep_reenrols_next(clean_data_dir):
    from app import followups, store
    st = S.load_settings(); st.follow_up_max_steps = 3; st.follow_up_delay_days = [3, 7, 7]
    S.save_settings(st)
    # approve the ORIGINAL -> enrol f1
    assert followups.enroll_from_approval(_approved_cs()).id == "acme_ai__f1"
    # approve f1 -> enrol f2
    fu2 = followups.enroll_from_approval(_approved_cs(slug="acme_ai__f1"))
    assert fu2 is not None and fu2.id == "acme_ai__f2" and fu2.step == 2
    # approve f2 -> enrol f3
    fu3 = followups.enroll_from_approval(_approved_cs(slug="acme_ai__f2"))
    assert fu3 is not None and fu3.step == 3
    # approve f3 -> at cap, no f4
    assert followups.enroll_from_approval(_approved_cs(slug="acme_ai__f3")) is None


# ---- work-queue view -------------------------------------------------------

def test_active_sorted_oldest_first(clean_data_dir):
    from app import followups
    st = S.load_settings(); st.follow_up_max_steps = 1; S.save_settings(st)
    followups.enroll_from_approval(_approved_cs(slug="newco", name="New Co", approved_days_ago=1))
    followups.enroll_from_approval(_approved_cs(slug="oldco", name="Old Co", approved_days_ago=10))
    followups.enroll_from_approval(_approved_cs(slug="midco", name="Mid Co", approved_days_ago=5))
    ordered = [f.parent_slug for f in followups.active_sorted()]
    assert ordered == ["oldco", "midco", "newco"]      # oldest original-approval first


def test_elapsed_and_due_labels(clean_data_dir):
    from app import followups
    st = S.load_settings(); st.follow_up_max_steps = 1; st.follow_up_delay_days = [3]
    S.save_settings(st)
    fu = followups.enroll_from_approval(_approved_cs(approved_days_ago=10))
    pub = followups.public(fu)
    assert pub["elapsed_label"].endswith("d ago")
    assert "10d" in pub["elapsed_label"]
    # approved 10 days ago, delay 3 -> due 7 days ago -> overdue
    assert pub["is_due"] is True
    assert pub["due_label"] == "due now"


def test_not_yet_due_label(clean_data_dir):
    from app import followups
    st = S.load_settings(); st.follow_up_max_steps = 1; st.follow_up_delay_days = [7]
    S.save_settings(st)
    fu = followups.enroll_from_approval(_approved_cs(approved_days_ago=1))
    pub = followups.public(fu)
    assert pub["is_due"] is False
    assert pub["due_label"].startswith("due in")


# ---- lazy draft generation (stub provider) ---------------------------------

def test_draft_followup_generates_body_and_re_subject(clean_data_dir):
    from app import followups, pipeline, store
    from app.providers.base import make_provider
    st = S.load_settings(); st.follow_up_max_steps = 1; S.save_settings(st)
    fu = followups.enroll_from_approval(_approved_cs(subject="Operations role"))

    provider = make_provider("stub", None)
    cs = pipeline.draft_followup(provider, fu, reuse_cache=True)
    assert cs.state.value == "drafted"
    assert cs.slug == "acme_ai__f1"
    assert cs.subject == "Re: Operations role"          # same thread (voice subject empty -> Re:)
    assert cs.final_email and len(cs.final_email) > 0
    # a follow-up is short: the whole assembled email stays well under a full outreach email.
    # (the <80-word floor targets the model-written portion; fixed re-anchor/close add a little.)
    assert len(cs.final_email.split()) < 110
    # the draft used a FOLLOW-UP-kind voice, not an outreach voice
    v = store.get_custom_voice(cs.voice)
    assert v is not None and v.kind == "followup"
    # generated draft is persisted as a normal draft
    assert store.get_draft("acme_ai__f1") is not None


def test_draft_followup_reuses_parent_cache_no_research(clean_data_dir):
    from app import followups, pipeline, store
    from app.providers.base import make_provider
    st = S.load_settings(); st.follow_up_max_steps = 1; S.save_settings(st)
    # seed a parent research cache with a distinctive proof point
    store.save_cache("acme_ai", {"company": {"name": "Acme AI", "role_exists": True, "company_size": "small"},
                                 "proof_points": [{"fact": "They shipped X in Q2.", "source": "blog"}],
                                 "situation_read": "scaling fast",
                                 "contact": {"name": "Jane Doe", "email": "jane@acme.example"}})
    fu = followups.enroll_from_approval(_approved_cs())
    provider = make_provider("stub", None)
    cs = pipeline.draft_followup(provider, fu, reuse_cache=True)
    assert cs.state.value == "drafted"
    assert cs.cache.get("proof_points")          # parent cache was reused, not re-searched


# ---- approval of a follow-up through the EXISTING path ----------------------

def test_followup_approval_flips_status_and_reenrols(clean_data_dir, monkeypatch):
    """Approving the generated follow-up draft via the real approve path should mark the FollowUp
    approved and (with cap>1) enrol the next step — proving reuse of the same approve architecture."""
    from app import followups, pipeline, store
    from app.providers.base import make_provider
    from app import server, apollo as apollo_mod

    st = S.load_settings(); st.follow_up_max_steps = 2; st.follow_up_delay_days = [3, 7]
    S.save_settings(st)

    # don't actually open a mail client
    monkeypatch.setattr(apollo_mod, "open_email_draft", lambda *a, **k: "/tmp/x.eml")

    # original approved -> f1 enrolled
    fu1 = followups.enroll_from_approval(_approved_cs())
    assert fu1.id == "acme_ai__f1"

    # generate the follow-up draft
    provider = make_provider("stub", None)
    cs = pipeline.draft_followup(provider, fu1, reuse_cache=True)
    fu1.draft_slug = cs.slug; fu1.status = fu1.status.__class__("drafted"); store.upsert_followup(fu1)

    # approve it through the SAME server helper the UI uses
    server._approve_rows([cs], None)

    # f1 is now approved, and f2 has been enrolled (cap 2)
    assert store.get_followup("acme_ai__f1").status.value == "approved"
    assert store.get_followup("acme_ai__f2") is not None
    assert store.get_followup("acme_ai__f2").step == 2


# ---- dismiss ---------------------------------------------------------------

def test_dismiss_removes_from_active(clean_data_dir):
    from app import followups, store
    from app.models import FollowUpStatus
    st = S.load_settings(); st.follow_up_max_steps = 1; S.save_settings(st)
    fu = followups.enroll_from_approval(_approved_cs())
    fu.status = FollowUpStatus.dismissed
    store.upsert_followup(fu)
    assert all(f.id != fu.id for f in followups.active_sorted())


# ---- follow-up voices are a separate, editable set --------------------------

def test_followup_voices_seed_as_separate_set(clean_data_dir):
    from app import store
    outreach = {v.id for v in store.list_custom_voices(kind="outreach")}
    followup = {v.id for v in store.list_custom_voices(kind="followup")}
    assert outreach and followup
    assert outreach.isdisjoint(followup)                 # disjoint sets
    assert all(v.startswith("fu_") for v in followup)    # shipped follow-up voices
    # every shipped follow-up voice is a valid CustomVoice with kind=followup
    for vid in followup:
        v = store.get_custom_voice(vid)
        assert v.kind == "followup" and v.blocks


def test_followup_voices_seed_even_when_outreach_exists(clean_data_dir):
    """Upgrade path: a user who already had outreach voices still gets follow-up voices seeded."""
    from app import store, settings as S
    # wipe follow-up voices, keep outreach
    for v in store.list_custom_voices(kind="followup"):
        store.delete_custom_voice(v.id)
    assert store.list_custom_voices(kind="followup") == []
    S.ensure_seeded()
    assert len(store.list_custom_voices(kind="followup")) >= 1   # self-healed
    assert len(store.list_custom_voices(kind="outreach")) >= 1   # untouched


def test_outreach_listing_excludes_followup_voices(clean_data_dir):
    """The outreach voice pickers must never show follow-up voices."""
    from app import store
    ids = {v.id for v in store.list_custom_voices()}   # default kind='outreach'
    assert not any(i.startswith("fu_") for i in ids)


def test_followup_draft_routes_to_matching_situation_voice(clean_data_dir):
    from app import followups, pipeline, store
    from app.providers.base import make_provider
    st = S.load_settings(); st.follow_up_max_steps = 1; S.save_settings(st)
    # large-company parent -> should route to fu_role_large
    store.save_cache("bigco", {"company": {"name": "Big Co", "role_exists": True, "company_size": "large"},
                               "proof_points": [{"fact": "Launched a new platform.", "source": "blog"}],
                               "contact": {"name": "Sam", "email": "sam@big.co"}})
    cs = _approved_cs(slug="bigco", name="Big Co", email="sam@big.co")
    cs.cache = {"company": {"role_exists": True, "company_size": "large"},
                "contact": {"name": "Sam", "email": "sam@big.co"}}
    fu = followups.enroll_from_approval(cs)
    draft = pipeline.draft_followup(make_provider("stub", None), fu)
    assert draft.voice == "fu_role_large"


# ---- settings endpoint persists the follow-up knobs (regression) ------------

def test_settings_endpoint_persists_followup_fields(clean_data_dir):
    """The /api/settings allowlist must include the follow-up knobs, or the cadence is
    silently unconfigurable from the UI (a bug the HTTP smoke test caught)."""
    import asyncio
    from app import server, settings as S
    asyncio.run(
        server.update_settings({"follow_up_enabled": False,
                                "follow_up_max_steps": 3,
                                "follow_up_delay_days": [2, 5, 9]}))
    st = S.load_settings()
    assert st.follow_up_enabled is False
    assert st.follow_up_max_steps == 3
    assert st.follow_up_delay_days == [2, 5, 9]
    # and they surface in the sanitized payload the status endpoint returns
    san = st.sanitized()
    assert san["follow_up_max_steps"] == 3 and san["follow_up_delay_days"] == [2, 5, 9]
