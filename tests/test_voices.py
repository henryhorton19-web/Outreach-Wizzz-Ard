"""Voice-system tests (block schema): voices as editable data, bootstrap-once seeding, situation
routing with a default fallback, the one voice-driven draft path (fixed + AI blocks under the stub),
the per-experience {key} tokens and the {relevant} selector, voice-parameterised evidence, and the
honesty-floor guard. All on the stub provider in an isolated data dir."""
from pathlib import Path
import pytest

from app import settings as S
from app import store
from app import pipeline as P
from app import validate as V
from app import compose as C
from app.models import CustomVoice, CompanyState, State
from app.providers.base import make_provider
from app.engine_bridge import de, engine_config as EC

STUB = make_provider("stub", None)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    (tmp_path / "voices").mkdir()
    (tmp_path / "caches").mkdir()
    monkeypatch.setattr(S, "VOICES_DIR", tmp_path / "voices")
    monkeypatch.setattr(S, "CACHE_DIR", tmp_path / "caches")
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    return tmp_path


def _voice(**kw):
    """A fixed-frame-equivalent voice in the block schema: greeting + AI body + Sciences-Po
    positioning + close. Override any scalar field, or pass blocks/style/evidence wholesale."""
    base = dict(
        id="v1", display_name="V1", subject="{company}", situations=[], mention_sci_po=True,
        length_min=70, length_max=120,
        blocks=[
            {"id": "greeting", "mode": "fixed", "text": "Hi {contact_first},"},
            {"id": "body", "mode": "ai", "length": "body",
             "fact_scope": ["target_proofs", "candidate_evidence", "candidate_spine", "situation_read"],
             "guidance": "Tie one fact to their need."},
            {"id": "positioning", "mode": "fixed", "owns_sci_po": True, "length": "short",
             "text": "I am on the Sciences Po exchange this year."},
            {"id": "close", "mode": "fixed", "length": "one_line", "text": "Open to a call?"},
        ],
        style={"formality": 2, "warmth": 2, "directness": 3, "notes": "direct"},
        evidence={"count": 1},
    )
    base.update(kw)
    return CustomVoice(**base)


def _cache(role_exists=True, size="small", proof=None, recent=True):
    c = {"company": {"name": "Acme", "what_they_do": "B2B software.", "role_exists": role_exists,
                     "role_title": "Analyst", "company_size": size, "work_mode": "remote_english",
                     "working_language": "English"},
         "proof_points": proof or [{"fact": "Acme serves mid-market teams.", "source": "https://a"}],
         "contact": {"status": "found", "name": "Alex Founder", "contact_verified": True,
                     "email": "alex@acme.example"},
         "situation_read": "scaling commercial"}
    if recent:
        c["recent_point"] = {"present": True, "kind": "raise", "detail": "Acme's seed round",
                             "source": "https://c"}
    else:
        c["recent_point"] = {"present": False}
    return c


# ---- CRUD + schema -----------------------------------------------------------

def test_voice_crud_roundtrip(isolated):
    v = _voice(id="v_cr", display_name="Round Trip", situations=["role_small"],
               style={"directness": 4, "register": "blunt"})
    store.save_custom_voice(v)
    got = store.get_custom_voice("v_cr")
    assert got and got.display_name == "Round Trip"
    assert got.situations == ["role_small"]
    assert [b.id for b in got.blocks] == ["greeting", "body", "positioning", "close"]
    body = next(b for b in got.blocks if b.id == "body")
    assert body.mode == "ai" and body.length == "body"
    assert got.style.directness == 4 and got.style.notes == "blunt"   # register alias roundtrips
    store.delete_custom_voice("v_cr")                                 # any voice deletes
    assert store.get_custom_voice("v_cr") is None


def test_legacy_voice_json_migrates_on_load(isolated):
    # an old-schema voice written straight to the store still loads (migrated to blocks)
    legacy = {"id": "old", "display_name": "Old", "situations": ["role_small"],
              "greeting": "Hi {name},", "subject": "{company}", "opening_mode": "fixed",
              "opening": "Op.", "opening_use_recent": False, "boilerplate_mode": "llm",
              "boilerplate": "At Sciences Po.", "boilerplate_guidance": "who I am",
              "boilerplate_owns_sci_po": True, "close_mode": "fixed", "close": "Call?",
              "register": "direct", "body_guidance": "tie a fact"}
    import json
    (isolated / "voices" / "old.json").write_text(json.dumps(legacy), encoding="utf-8")
    got = store.get_custom_voice("old")
    assert got and [b.id for b in got.blocks] == ["greeting", "opening", "body", "positioning", "close"]
    assert got.style.notes == "direct"
    pos = next(b for b in got.blocks if b.id == "positioning")
    assert pos.mode == "ai" and pos.owns_sci_po


# ---- bootstrap-once seeding --------------------------------------------------

def test_bootstrap_seeds_once_and_self_heals(isolated):
    # Committed repo ships 6 outreach + 3 follow-up starters. A dev machine may also carry a
    # git-ignored app/seed_voices_local/ with private voices; count those in so the guard is
    # correct both for a fresh public clone and for a local dev checkout.
    pkg = Path(S.__file__).resolve().parent
    n_local = len(list((pkg / "seed_voices_local").glob("*.json"))) \
        if (pkg / "seed_voices_local").exists() else 0
    expected_outreach = 6 + n_local
    total = 9 + n_local

    assert store.list_custom_voices() == []
    S.ensure_seeded()
    assert len(list((isolated / "voices").glob("*.json"))) == total
    assert len(store.list_custom_voices(kind="outreach")) == expected_outreach
    assert len(store.list_custom_voices(kind="followup")) == 3
    S.ensure_seeded()
    assert len(list((isolated / "voices").glob("*.json"))) == total
    v = store.get_custom_voice("role_small"); v.display_name = "Mine"; store.save_custom_voice(v)
    S.ensure_seeded()
    assert store.get_custom_voice("role_small").display_name == "Mine"
    for p in (isolated / "voices").glob("*.json"):
        p.unlink()
    S.ensure_seeded()
    assert len(list((isolated / "voices").glob("*.json"))) == total


# ---- routing -----------------------------------------------------------------

def test_resolve_override_wins(isolated):
    S.ensure_seeded()
    store.save_custom_voice(_voice(id="v_ov", display_name="Override", situations=[]))
    assert P.resolve_voice(_cache(size="small"), "v_ov") == "v_ov"


def test_resolve_by_situation_tag(isolated):
    store.save_custom_voice(_voice(id="only_large", display_name="Only Large",
                                   situations=["role_large"]))
    assert P.resolve_voice(_cache(role_exists=True, size="large")) == "only_large"


def test_resolve_default_and_delete_safety(isolated):
    store.save_custom_voice(_voice(id="tag_large", display_name="Tag", situations=["role_large"]))
    store.save_custom_voice(_voice(id="fallback", display_name="Fallback", situations=[]))
    st = S.load_settings(); st.default_voice = "fallback"; S.save_settings(st)
    assert P.resolve_voice(_cache(size="large")) == "tag_large"
    store.delete_custom_voice("tag_large")
    assert P.resolve_voice(_cache(size="large")) == "fallback"


def test_resolve_reseeds_empty_store(isolated):
    assert store.list_custom_voices() == []
    vid = P.resolve_voice(_cache(size="small"))
    assert vid and store.get_custom_voice(vid) is not None


# ---- the one voice-driven draft path (stub) ---------------------------------

def test_draft_fixed_voice_sciences_po_once(isolated):
    store.save_custom_voice(_voice(id="fx", display_name="Fixed", situations=["role_small"]))
    store.save_cache("acme", _cache())
    cs = CompanyState(slug="acme", name="Acme", website="https://acme.example", state=State.input)
    P.draft_one(STUB, cs, voice_override="fx", reuse_cache=True)
    assert cs.state == State.drafted and cs.voice == "fx"
    assert cs.machine_email.lower().count("sciences po") == 1
    assert cs.machine_email.startswith("Hi Alex")
    assert cs.machine_email.rstrip().endswith("Open to a call?")


def test_draft_ai_positioning_names_sci_po_offline(isolated):
    store.save_custom_voice(_voice(
        id="ai", display_name="AI", situations=["role_small"],
        blocks=[
            {"id": "greeting", "mode": "fixed", "text": "Hi {contact_first},"},
            {"id": "body", "mode": "ai", "length": "body",
             "fact_scope": ["candidate_evidence", "situation_read"], "guidance": "tie a fact"},
            {"id": "positioning", "mode": "ai", "owns_sci_po": True, "length": "short",
             "fact_scope": ["candidate_spine"],
             "guidance": "Say who I am and that I want a part-time seat during Sciences Po."},
        ]))
    store.save_cache("beta", _cache())
    cs = CompanyState(slug="beta", name="Beta", website="https://b.example", state=State.input)
    P.draft_one(STUB, cs, voice_override="ai", reuse_cache=True)
    assert cs.state == State.drafted
    assert cs.machine_email.lower().count("sciences po") == 1


def test_optional_recent_block_skipped_without_recent(isolated):
    store.save_custom_voice(_voice(
        id="op", display_name="Opt", situations=["role_small"],
        blocks=[
            {"id": "greeting", "mode": "fixed", "text": "Hi {contact_first},"},
            {"id": "opening", "mode": "ai", "length": "one_line", "optional": True,
             "fact_scope": ["recent"], "guidance": "acknowledge the recent event"},
            {"id": "positioning", "mode": "fixed", "owns_sci_po": True,
             "text": "On the Sciences Po exchange this year."},
        ]))
    store.save_cache("norec", _cache(recent=False))
    cs = CompanyState(slug="norec", name="NoRec", website="https://n.example", state=State.input)
    P.draft_one(STUB, cs, voice_override="op", reuse_cache=True)
    assert cs.state == State.drafted
    # opening had only a recent scope and no recent -> skipped; email jumps to positioning
    assert "sciences po" in cs.machine_email.lower()


# ---- per-experience {key} tokens and {relevant} -----------------------------

def test_experience_token_resolves_and_grounds(isolated):
    store.save_custom_voice(_voice(
        id="tok", display_name="Tok", situations=["role_small"],
        blocks=[
            {"id": "greeting", "mode": "fixed", "text": "Hi {contact_first},"},
            {"id": "context", "mode": "fixed", "length": "short", "text": "For context: {anchor_co}"},
            {"id": "positioning", "mode": "fixed", "owns_sci_po": True,
             "text": "On the Sciences Po exchange this year."},
        ]))
    store.save_cache("tok", _cache())
    cs = CompanyState(slug="tok", name="Tok", website="https://t.example", state=State.input)
    P.draft_one(STUB, cs, voice_override="tok", reuse_cache=True)
    anchor_co_anchor = EC.CANDIDATE_PROFILE["experiences"]["anchor_co"]["anchor"]
    assert anchor_co_anchor in cs.machine_email                         # {anchor_co} substituted verbatim
    af = [a["text"] for a in cs.spec["allowed_facts"]]
    assert anchor_co_anchor in af                                       # and grounded in allowed_facts


def test_relevant_token_resolves_offline_to_top_of_shortlist(isolated):
    store.save_custom_voice(_voice(
        id="rel", display_name="Rel", situations=["role_small"],
        blocks=[
            {"id": "greeting", "mode": "fixed", "text": "Hi {contact_first},"},
            {"id": "rel", "mode": "fixed", "length": "short", "text": "Most relevant: {relevant}"},
            {"id": "positioning", "mode": "fixed", "owns_sci_po": True,
             "text": "On the Sciences Po exchange this year."},
        ]))
    store.save_cache("rel", _cache(proof=[{"fact": "an LLM investment platform", "source": "x"}]))
    cs = CompanyState(slug="rel", name="Rel", website="https://r.example", state=State.input)
    P.draft_one(STUB, cs, voice_override="rel", reuse_cache=True)
    assert cs.state == State.drafted
    top = cs.spec["evidence_shortlist"][0]
    assert top and top in cs.machine_email                        # {relevant} -> top-of-shortlist anchor


def test_relevant_never_resolves_to_excluded(isolated):
    cache = _cache(proof=[{"fact": "raised a growth round", "source": "x"}])   # anchor_co would top
    ranked = de.rank_evidence(cache, exclude=["anchor_co"])
    assert "anchor_co" not in [e["_key"] for e in ranked]
    shortlist = [e for e in ranked if e.get("_score", 0) > 0 or e.get("_pinned")][:5] or ranked[:3]
    assert "anchor_co" not in [e["_key"] for e in shortlist]


def test_explicit_token_overrides_exclude_in_allowed_facts(isolated):
    store.save_custom_voice(_voice(
        id="ovr", display_name="Ovr", situations=["role_small"],
        evidence={"exclude": ["side_co"], "count": 1},
        blocks=[
            {"id": "greeting", "mode": "fixed", "text": "Hi {contact_first},"},
            {"id": "ino", "mode": "fixed", "length": "short", "text": "{side_co}"},
            {"id": "positioning", "mode": "fixed", "owns_sci_po": True,
             "text": "On the Sciences Po exchange this year."},
        ]))
    store.save_cache("ovr", _cache())
    cs = CompanyState(slug="ovr", name="Ovr", website="https://o.example", state=State.input)
    P.draft_one(STUB, cs, voice_override="ovr", reuse_cache=True)
    side_co_anchor = EC.CANDIDATE_PROFILE["experiences"]["side_co"]["anchor"]
    af = [a["text"] for a in cs.spec["allowed_facts"]]
    assert side_co_anchor in af          # placed by {side_co} despite exclude -> still grounded


# ---- voice-parameterised evidence -------------------------------------------

def test_evidence_exclude_pin_count(isolated):
    cache = _cache(proof=[{"fact": "an LLM agent platform for analysts", "source": "x"}])
    ex = de.select_evidence(cache, exclude=["anchor_co"], count=2)
    assert "anchor_co" not in [e["_key"] for e in ex]
    pinned = de.rank_evidence(cache, pin=["side_co"])
    assert pinned[0]["_key"] == "side_co"
    one = de.select_evidence(cache, count=1)
    assert len(one) == 1


# ---- the honesty floor -------------------------------------------------------

def test_floor_flags_unsourced_number_and_dash(isolated):
    spec = de.prepare(_cache()); spec["allowed_facts"] = []
    voice = _voice(id="fl", mention_sci_po=False,
                   blocks=[{"id": "body", "mode": "ai", "length": "body", "guidance": "x"}])
    body = "We already serve 5000 enterprise logos \u2014 a lot, and growing fast every quarter."
    notes = V.floor_notes(spec, body, {"body": body}, voice)
    assert any("5000" in n.text for n in notes)
    assert any("dash" in n.text.lower() for n in notes)


def test_floor_word_count_is_voice_aware(isolated):
    spec = de.prepare(_cache())
    voice = _voice(id="wc", mention_sci_po=False, length_min=70, length_max=120,
                   blocks=[{"id": "body", "mode": "ai", "length": "body", "guidance": "x"}])
    notes = V.floor_notes(spec, "Too short.", {"body": "Too short."}, voice)
    wc = [n for n in notes if "word" in n.text.lower()]
    assert len(wc) == 1 and "70-120" in wc[0].text


def test_floor_respects_allow_dashes_and_no_sci_po(isolated):
    spec = de.prepare(_cache())
    voice = _voice(id="dash", allow_dashes=True, mention_sci_po=False, length_min=1, length_max=200,
                   blocks=[{"id": "body", "mode": "ai", "length": "body", "guidance": "x"}])
    body = "A clean line \u2014 with a dash, and no Sciences Po."
    notes = V.floor_notes(spec, body, {"body": body}, voice)
    assert not any("dash" in n.text.lower() for n in notes)      # dashes allowed -> no flag
    # mention_sci_po False but Sciences Po present -> flagged
    assert any("sciences po" in n.text.lower() for n in notes)


def test_sciences_po_once_counts(isolated):
    zero = V.sciences_po_once("no mention here")
    assert zero and zero[0].severity == "soft"
    assert V.sciences_po_once("I am on the Sciences Po exchange.") == []
    twice = V.sciences_po_once("Sciences Po and again Sciences Po")
    assert twice and "2 times" in twice[0].text
