"""Offline tests for continuous voice learning (Layer 4) + Phase C A/B promotion.

All offline (stub provider, isolated tmp store): the edit signal + weighting, the reflection→patch
path (deterministic heuristic under the stub), clamping + honesty-floor lint, versioned apply +
rollback, the mode gate (off/suggest/auto), challenger spawning + bandit arbitration, the offline
batch optimiser, and the HTTP endpoints over the real server. Mirrors test_manual_outcomes.py /
test_manual_e2e.py discipline.
"""
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app import store, edit_ledger, voice_learning as VL, voice_optimize, voice_stats
from app import settings as S
from app.models import CustomVoice, Block, Style, SentItem, ReplyState


# ---- fixtures --------------------------------------------------------------

@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    """Isolate every file/dir the learning loop touches into tmp_path."""
    vd = tmp_path / "voices"; vd.mkdir()
    vh = tmp_path / "voice_history"; vh.mkdir()
    ld = tmp_path / "edit_ledger"; ld.mkdir()
    monkeypatch.setattr(S, "VOICES_DIR", vd)
    monkeypatch.setattr(S, "VOICE_HISTORY_DIR", vh)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(edit_ledger, "LEDGER_DIR", ld)
    monkeypatch.setattr(VL, "PROPOSALS_FILE", tmp_path / "voice_proposals.json")
    monkeypatch.setattr(store, "SENT_ITEMS_FILE", tmp_path / "sent_items.json")
    yield


def _voice(vid="role_small", situations=("role_small",), directness=2, notes="Keep it human.",
           examples=None):
    return CustomVoice(
        id=vid, display_name="Role · small", situations=list(situations),
        blocks=[Block(id="body", label="Body", mode="ai", length="body",
                      fact_scope=["candidate_evidence"], guidance="Tie one proof to the role."),
                Block(id="close", label="Close", mode="fixed", text="Open to a short call?")],
        style=Style(directness=directness, notes=notes, examples=list(examples or [])),
        length_min=70, length_max=120)


def _set(**kw):
    s = S.load_settings()
    for k, v in kw.items():
        setattr(s, k, v)
    S.save_settings(s)


# ---- signal: effort + capture + gather -------------------------------------

def test_edit_effort_monotonic():
    assert edit_ledger.edit_effort("hello world", "hello world") == 0.0
    small = edit_ledger.edit_effort("hello world", "hello world!")
    big = edit_ledger.edit_effort("hello world", "totally different text entirely")
    assert 0.0 < small < big <= 1.0


def test_record_edit_stores_effort_and_sent_id():
    machine = "Hi Jane,\n\nThis is the machine body paragraph.\n\nOpen to a short call?"
    final = "Hi Jane,\n\nTightened, punchier body.\n\nOpen to a short call?"
    ok = edit_ledger.record_edit("role_small", machine,
                                 "This is the machine body paragraph.", final, sent_id="acme#0")
    assert ok
    tr = edit_ledger.triples_for_learning("role_small")
    assert len(tr) == 1 and tr[0]["sent_id"] == "acme#0" and tr[0]["effort"] > 0


def test_gather_excludes_bounces_and_weights_replies():
    for i, rs in enumerate([ReplyState.replied, ReplyState.awaiting, ReplyState.bounced]):
        store.upsert_sent_item(SentItem(id=f"acme#{i}", slug="acme", name="Acme",
                                        voice="role_small", reply_state=rs))
        edit_ledger.record_edit("role_small",
                                f"Hi,\n\nmachine body number {i} goes here now.\n\nBye?",
                                f"machine body number {i} goes here now.",
                                f"Hi,\n\nedited body {i} shorter.\n\nBye?", sent_id=f"acme#{i}")
    triples = VL.gather("role_small")
    outcomes = {t["outcome"] for t in triples}
    assert "bounced" not in outcomes and "bounced_exhausted" not in outcomes
    assert len(triples) == 2
    replied = next(t for t in triples if t["outcome"] == "replied")
    assert replied["weight"] == 2.0


# ---- reflect (offline heuristic) + clamp + lint ----------------------------

def _shortening_triples(n=4):
    out = []
    for i in range(n):
        before = "Hi there,\n\n" + ("a fairly long machine paragraph that says a lot. " * 4) + f"#{i}"
        after = f"Tightened body {i}, much shorter."
        out.append({"before": before, "after": after, "effort": 0.6,
                    "outcome": "replied" if i == 0 else "awaiting",
                    "weight": 2.0 if i == 0 else 1.0})
    return out


def test_offline_reflect_learns_shorten_and_direct():
    v = _voice(directness=2)
    patch = VL.reflect(None, v, _shortening_triples())     # None provider -> offline heuristic
    assert patch["style_deltas"].get("directness") == 1
    assert patch["categorical"].get("sentence_length") == "short"
    assert patch["promote_examples"]                       # promoted the replied example


def test_clamp_bounds_sliders_and_categoricals():
    v = _voice(directness=4)
    dirty = {"style_deltas": {"directness": 3, "warmth": -2}, "categorical": {"hedging": "nonsense"},
             "notes_add": ["a", "b", "c", "d"], "promote_examples": [], "block_guidance": {},
             "evidence": {}, "notes_remove": []}
    c = VL.clamp_patch(dirty, v)
    assert "directness" not in c["style_deltas"]           # 4 + (+1 clamp) would exceed 4 -> dropped
    assert c["style_deltas"]["warmth"] == -1               # -2 clamped to -1
    assert "hedging" not in c["categorical"]               # invalid value dropped
    assert len(c["notes_add"]) == 2                        # capped at 2


def test_example_lint_rejects_floor_violations():
    v = _voice()                                            # allow_dashes False
    assert VL.example_is_clean("A clean, dashless approved body that is plenty long enough.", v)
    assert not VL.example_is_clean("Body with an em dash \u2014 which the voice forbids here.", v)
    assert not VL.example_is_clean("A short body that ends like a letter.\n\nBest regards", v)


# ---- versioned apply + rollback --------------------------------------------

def test_apply_patch_versions_and_mutates():
    v = _voice(directness=2, notes="Keep it human.")
    store.save_custom_voice(v)
    patch = {"style_deltas": {"directness": 1}, "categorical": {"sentence_length": "short"},
             "notes_add": ["Lead with the specific hook."], "notes_remove": [],
             "promote_examples": ["A clean approved body that is definitely long enough to keep."],
             "block_guidance": {"body": "One proof, no throat-clearing."}, "evidence": {}}
    res = VL.apply_patch("role_small", patch)
    assert res["ok"] and res["snapshot_ts"]
    got = store.get_custom_voice("role_small")
    assert got.style.directness == 3
    assert got.style.sentence_length == "short"
    assert "specific hook" in got.style.notes
    assert got.style.examples and "long enough" in got.style.examples[0]
    assert next(b for b in got.blocks if b.id == "body").guidance == "One proof, no throat-clearing."
    # a snapshot exists, and rollback restores the pre-change voice
    versions = store.list_voice_versions("role_small")
    assert len(versions) == 1
    restored = store.restore_voice_version("role_small", versions[0]["ts"])
    assert restored.style.directness == 2 and restored.style.sentence_length == "flowing"


def test_apply_empty_patch_is_noop():
    store.save_custom_voice(_voice())
    res = VL.apply_patch("role_small", VL._empty_patch())
    assert not res["ok"]
    assert store.list_voice_versions("role_small") == []   # nothing snapshotted, nothing changed


def test_example_rotation_caps_count():
    v = _voice(examples=["e1", "e2", "e3", "e4", "e5"])
    store.save_custom_voice(v)
    _set(voice_learning_max_examples=5)
    patch = VL.clamp_patch(
        {"promote_examples": ["A fresh clean approved body long enough to promote as an example."]},
        v)
    VL.apply_patch("role_small", patch)
    got = store.get_custom_voice("role_small")
    assert len(got.style.examples) == 5                    # rotated: oldest evicted
    assert "fresh clean approved" in got.style.examples[-1]


# ---- mode gate: off / suggest / auto ---------------------------------------

def _seed_edits(vid="role_small", n=5, outcome=ReplyState.awaiting):
    """Seed n realistic body edits that SHORTEN the draft, keeping a stable greeting/close frame so
    edit_ledger can isolate the body. Edited bodies are clean + long enough to be promotable."""
    frame_pre, frame_post = "Hi Jane,\n\n", "\n\nOpen to a short call?"
    for i in range(n):
        body = ("This is a fairly long and meandering machine paragraph that says quite a lot "
                "without much focus at all. " * 3) + f"Draft variation {i}."
        edited = (f"Tightened, punchier body {i}. Straight to the point, one proof, "
                  "no throat clearing whatsoever.")
        store.upsert_sent_item(SentItem(id=f"{vid}#{i}", slug=vid, name="Acme", voice=vid,
                                        reply_state=outcome))
        edit_ledger.record_edit(vid, frame_pre + body + frame_post, body,
                                frame_pre + edited + frame_post, sent_id=f"{vid}#{i}")
        VL.note_edit(vid)


def test_mode_off_is_noop():
    store.save_custom_voice(_voice())
    _set(voice_learning_mode="off")
    _seed_edits()
    assert VL.maybe_run("role_small", None) is None
    assert store.list_voice_versions("role_small") == []


def test_suggest_mode_stores_proposal_without_applying():
    store.save_custom_voice(_voice(directness=2))
    _set(voice_learning_mode="suggest", voice_learning_min_edits=5, voice_learning_cooldown_hours=0)
    _seed_edits()
    out = VL.maybe_run("role_small", None)
    assert out and out["mode"] == "suggest" and out["proposal"]
    # voice itself is unchanged; a proposal is pending; counter reset
    assert store.get_custom_voice("role_small").style.directness == 2
    assert VL.proposals_for("role_small")
    assert store.get_custom_voice("role_small").learning_meta.get("edits_since") == 0


def test_auto_mode_applies_versioned():
    store.save_custom_voice(_voice(directness=2))
    _set(voice_learning_mode="auto", voice_learning_min_edits=5, voice_learning_cooldown_hours=0,
         voice_learning_promote=False)
    _seed_edits()
    out = VL.maybe_run("role_small", None)
    assert out and out["mode"] == "auto" and out["applied"] is True
    assert store.get_custom_voice("role_small").style.directness == 3   # +1 applied
    assert len(store.list_voice_versions("role_small")) == 1            # snapshotted


def test_cooldown_blocks_second_cycle():
    store.save_custom_voice(_voice())
    _set(voice_learning_mode="auto", voice_learning_min_edits=3, voice_learning_cooldown_hours=12)
    _seed_edits(n=3)
    assert VL.maybe_run("role_small", None)["applied"] is True
    _seed_edits(n=3)                                        # more edits, but within cooldown
    assert VL.maybe_run("role_small", None) is None


# ---- proposals lifecycle ---------------------------------------------------

def test_proposal_apply_and_reject():
    store.save_custom_voice(_voice(directness=2))
    _seed_edits()
    prop = VL.build_proposal(None, "role_small")
    assert prop and prop["id"]
    # reject clears it, no change
    assert VL.reject_proposal(prop["id"]) is True
    assert not VL.proposals_for("role_small")
    # rebuild + apply mutates + clears
    prop2 = VL.build_proposal(None, "role_small")
    res = VL.apply_proposal(prop2["id"])
    assert res["ok"]
    assert store.get_custom_voice("role_small").style.directness == 3
    assert not VL.proposals_for("role_small")


# ---- Phase C: challenger spawn + arbitration -------------------------------

def test_spawn_challenger_hidden_but_routable():
    champ = _voice()
    store.save_custom_voice(champ)
    ch = VL.spawn_challenger(champ, {"style_deltas": {"directness": 1}})
    assert ch and ch["challenger"].startswith("role_small__c")
    # champion untouched
    assert store.get_custom_voice("role_small").style.directness == 2
    # challenger hidden from the default listing, visible when asked, carries the situation
    assert all(getattr(v, "challenger_of", "") == "" for v in store.list_custom_voices())
    allv = store.list_custom_voices(include_challengers=True)
    chv = next(v for v in allv if v.id == ch["challenger"])
    assert chv.challenger_of == "role_small" and "role_small" in chv.situations
    assert chv.style.directness == 3


def test_arbitrate_promotes_winning_challenger():
    _set(voice_stats_min_n=2, voice_learning_routing="auto")
    champ = _voice()
    store.save_custom_voice(champ)
    ch = VL.spawn_challenger(champ, {"style_deltas": {"directness": 1},
                                     "notes_add": ["Learned rule from the challenger."]})
    chid = ch["challenger"]
    # champion: many sends, few replies; challenger: few sends, all replies -> CIs separate
    for i in range(6):
        store.upsert_sent_item(SentItem(id=f"role_small#{i}", slug="c", name="X", voice="role_small",
                                        reply_state=ReplyState.awaiting))
    for i in range(4):
        store.upsert_sent_item(SentItem(id=f"{chid}#{i}", slug="c", name="X", voice=chid,
                                        reply_state=ReplyState.replied))
    decisions = VL.arbitrate()
    d = next(x for x in decisions if x["challenger"] == chid)
    assert d["decision"] == "promoted" and d["ok"]
    # challenger content is now in the champion; the challenger is gone
    assert store.get_custom_voice("role_small").style.directness == 3
    assert store.get_custom_voice(chid) is None


def test_arbitrate_retires_losing_challenger():
    _set(voice_stats_min_n=2, voice_learning_routing="auto")
    champ = _voice()
    store.save_custom_voice(champ)
    ch = VL.spawn_challenger(champ, {"style_deltas": {"directness": 1}})
    chid = ch["challenger"]
    for i in range(6):
        store.upsert_sent_item(SentItem(id=f"role_small#{i}", slug="c", name="X", voice="role_small",
                                        reply_state=ReplyState.replied))
    for i in range(4):
        store.upsert_sent_item(SentItem(id=f"{chid}#{i}", slug="c", name="X", voice=chid,
                                        reply_state=ReplyState.awaiting))
    decisions = VL.arbitrate()
    d = next(x for x in decisions if x["challenger"] == chid)
    assert d["decision"] == "retired"
    assert store.get_custom_voice(chid) is None
    assert store.get_custom_voice("role_small").style.directness == 2   # champion never mutated


# ---- Phase C: offline batch optimiser --------------------------------------

def test_optimize_spawns_challenger_from_corpus():
    store.save_custom_voice(_voice())
    _seed_edits(n=8)
    res = voice_optimize.optimize(None, "role_small", min_corpus=6)
    assert res["ok"] and res["challenger"]
    # a challenger now exists tracking the champion
    allv = store.list_custom_voices(include_challengers=True)
    assert any(getattr(v, "challenger_of", "") == "role_small" for v in allv)


def test_optimize_needs_corpus():
    store.save_custom_voice(_voice())
    _seed_edits(n=2)
    res = voice_optimize.optimize(None, "role_small", min_corpus=6)
    assert not res["ok"] and "need" in res["error"]


# ---- HTTP e2e over the real server -----------------------------------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_PROVIDER", "stub")
    monkeypatch.setenv("PARIS_NO_KEYRING", "1")
    import importlib
    import app.settings as S2; importlib.reload(S2)
    import app.store as store2; importlib.reload(store2)
    import app.edit_ledger as el2; importlib.reload(el2)
    import app.voice_learning as vl2; importlib.reload(vl2)
    import app.voice_optimize as vo2; importlib.reload(vo2)
    import app.pipeline as p2; importlib.reload(p2)
    import app.server as server; importlib.reload(server)
    S2.ensure_seeded()
    c = TestClient(server.app)
    c._H = {"x-paris-token": S2.SESSION_TOKEN}
    c._S, c._store, c._el, c._vl = S2, store2, el2, vl2
    return c


def test_http_learn_history_rollback(client):
    # seed edits for a seeded voice, then learn-now + history + rollback via HTTP
    from app.models import SentItem as _SI, ReplyState as _RS
    pre, post = "Hi Jane,\n\n", "\n\nOpen to a short call?"
    for i in range(5):
        client._store.upsert_sent_item(_SI(id=f"chief_of_staff#{i}", slug="s", name="X",
                                           voice="chief_of_staff", reply_state=_RS.awaiting))
        body = ("A fairly long and meandering machine paragraph that says a great deal "
                "without much focus here. " * 3) + f"Variation {i}."
        edited = (f"Tightened, punchier body {i}. Straight to the point, one proof, "
                  "no throat clearing at all.")
        client._el.record_edit("chief_of_staff", pre + body + post, body, pre + edited + post,
                               sent_id=f"chief_of_staff#{i}")

    r = client.post("/api/voices/chief_of_staff/learn", headers=client._H)
    assert r.status_code == 200 and r.json()["proposal"]
    prop = r.json()["proposal"]

    a = client.post(f"/api/voices/chief_of_staff/proposals/{quote(prop['id'], safe='')}/apply",
                    headers=client._H)
    assert a.status_code == 200 and a.json()["ok"]

    h = client.get("/api/voices/chief_of_staff/history", headers=client._H)
    assert h.status_code == 200 and h.json()["versions"]
    ts = h.json()["versions"][0]["ts"]

    rb = client.post("/api/voices/chief_of_staff/rollback", headers=client._H, json={"ts": ts})
    assert rb.status_code == 200 and rb.json()["ok"]

    ls = client.get("/api/voices/chief_of_staff/learning", headers=client._H)
    assert ls.status_code == 200 and ls.json()["ok"]


def test_http_challenger_hidden_from_voices_list(client):
    v = client._store.get_custom_voice("chief_of_staff")
    client._vl.spawn_challenger(v, {"style_deltas": {"directness": 1}})
    r = client.get("/api/voices?kind=outreach", headers=client._H)
    ids = [x["id"] for x in r.json()["voices"]]
    assert not any(i.startswith("role_small__c") for i in ids)   # challengers hidden from the editor
