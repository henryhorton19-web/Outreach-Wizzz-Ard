"""End-to-end regression covering EVERY phase of continuous voice learning through the real
FastAPI server + the real approve flow (offline, stub provider):

  A  signal captured on a genuine approve (effort + sent_id, outcome-weighted, bounces excluded)
  B  suggest -> accept (HTTP), auto-apply via the approve hook, versioning + rollback (HTTP)
  C  auto+promote spawns a challenger (champion untouched), the bandit can route to it,
     arbitrate promotes a winner and retires a loser (HTTP), offline optimise spawns a challenger

This drives the same code paths a user hits in the app; nothing is mocked but the model/network.
"""
import importlib
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_PROVIDER", "stub")
    monkeypatch.setenv("PARIS_NO_KEYRING", "1")
    import app.settings as S; importlib.reload(S)
    import app.store as store; importlib.reload(store)
    import app.edit_ledger as el; importlib.reload(el)
    import app.voice_learning as VL; importlib.reload(VL)
    import app.voice_optimize as VO; importlib.reload(VO)
    import app.pipeline as pipeline; importlib.reload(pipeline)
    import app.server as server; importlib.reload(server)
    S.ensure_seeded()
    c = TestClient(server.app)
    c._H = {"x-paris-token": S.SESSION_TOKEN}
    return c, S, store, el, VL, pipeline, server


def _set(c, **kw):
    assert c.post("/api/settings", headers=c._H, json=kw).status_code == 200


def _approve_edited(c, store, name, i):
    H = c._H
    c.post("/api/ingest", json={"text": name}, headers=H)
    slug = store.load_queue()[0]["slug"]
    c.post(f"/api/queue/{slug}/draft", headers=H)
    c.post(f"/api/draft/{slug}", headers=H)
    cs = store.get_draft(slug)
    machine, body = cs.machine_email or "", cs.machine_body or ""
    new_body = f"Tightened body {i}. One tight proof, straight to the point, no throat clearing here."
    final = machine.replace(body, new_body) if body and body in machine else machine + "\n\n" + new_body
    c.put(f"/api/companies/{slug}/email", headers=H, json={"email": final})
    c.post(f"/api/companies/{slug}/approve", headers=H)
    return slug


def test_all_phases_end_to_end(app_ctx):
    c, S, store, el, VL, pipeline, server = app_ctx
    H = c._H
    from app.models import SentItem, ReplyState

    # ---- Phase A: signal on a real approve ----
    _set(c, voice_learning_mode="off", voice_learning_min_edits=3,
         voice_learning_cooldown_hours=0, voice_stats_min_n=2)
    _approve_edited(c, store, "Acme", 0)
    vid = store.load_sent_items()[0].voice
    trips = el.triples_for_learning(vid)
    assert len(trips) == 1 and trips[0]["sent_id"] and trips[0]["effort"] > 0
    assert store.list_voice_versions(vid) == []           # off changed nothing

    # ---- Phase A/B: suggest stores a proposal via the hook; accept applies it (HTTP) ----
    _set(c, voice_learning_mode="suggest", voice_learning_min_edits=3, voice_learning_cooldown_hours=0)
    base_dir = store.get_custom_voice(vid).style.directness
    for i, nm in enumerate(["Beta", "Gamma", "Delta"], start=1):
        _approve_edited(c, store, nm, i)
    ls = c.get(f"/api/voices/{vid}/learning", headers=H).json()
    assert ls["proposals"], "suggest mode should have stored a proposal"
    assert store.get_custom_voice(vid).style.directness == base_dir   # not applied yet
    pid = ls["proposals"][0]["id"]
    ap = c.post(f"/api/voices/{vid}/proposals/{quote(pid, safe='')}/apply", headers=H)
    assert ap.status_code == 200 and ap.json()["ok"]
    assert store.get_custom_voice(vid).style.directness != base_dir   # applied
    assert len(store.list_voice_versions(vid)) >= 1                   # snapshotted

    # ---- Phase B: rollback (HTTP) restores the prior voice ----
    hist = c.get(f"/api/voices/{vid}/history", headers=H).json()["versions"]
    rb = c.post(f"/api/voices/{vid}/rollback", headers=H, json={"ts": hist[-1]["ts"]})
    assert rb.status_code == 200 and rb.json()["ok"]
    assert store.get_custom_voice(vid).style.directness == base_dir

    # ---- Phase B: auto-apply via the approve hook, versioned ----
    _set(c, voice_learning_mode="auto", voice_learning_min_edits=3,
         voice_learning_cooldown_hours=0, voice_learning_promote=False)
    d0, v0 = store.get_custom_voice(vid).style.directness, len(store.list_voice_versions(vid))
    for i, nm in enumerate(["Epsilon", "Zeta", "Eta"], start=10):
        _approve_edited(c, store, nm, i)
    assert store.get_custom_voice(vid).style.directness != d0        # auto changed it
    assert len(store.list_voice_versions(vid)) > v0                  # after a snapshot

    # ---- Phase C: auto+promote spawns a challenger; champion untouched ----
    _set(c, voice_learning_mode="auto", voice_learning_promote=True,
         voice_learning_min_edits=3, voice_learning_cooldown_hours=0)
    cv = store.get_custom_voice(vid); cv.style.directness = 1; store.save_custom_voice(cv)
    champ_dir = 1
    for i, nm in enumerate(["Theta", "Iota", "Kappa"], start=20):
        _approve_edited(c, store, nm, i)
    challengers = [v for v in store.list_custom_voices(kind=None, include_challengers=True)
                   if getattr(v, "challenger_of", "") == vid]
    assert challengers, "auto+promote should spawn a challenger"
    assert store.get_custom_voice(vid).style.directness == champ_dir  # champion untouched
    listed = {v["id"] for v in c.get("/api/voices?kind=all", headers=H).json()["voices"]}
    assert not (listed & {ch.id for ch in challengers})              # challengers hidden from editor
    ch = challengers[0]

    # ---- Phase C: the bandit can route live sends to the challenger ----
    _set(c, voice_learning_routing="auto", voice_explore_epsilon=1.0, voice_stats_min_n=2)
    sit = ch.situations[0]
    cache = {"company": {"name": "X", "role_exists": (sit != "no_role_small"),
                         "company_size": ("large" if sit == "role_large" else "small")},
             "contact": {"name": "Y", "email": "y@x.com"}}
    picks = {pipeline.resolve_voice(cache) for _ in range(50)}
    assert ch.id in picks, "challenger must be routable"

    # ---- Phase C: arbitrate promotes a clearly-winning challenger (HTTP) ----
    store.clear_sent_items()
    for i in range(10):
        store.upsert_sent_item(SentItem(id=f"{vid}#a{i}", slug="a", name="Z", voice=vid,
                                        reply_state=ReplyState.awaiting))
    for i in range(8):
        store.upsert_sent_item(SentItem(id=f"{ch.id}#a{i}", slug="a", name="Z", voice=ch.id,
                                        reply_state=ReplyState.replied))
    dec = next(d for d in c.post("/api/voices/arbitrate", headers=H).json()["decisions"]
               if d["challenger"] == ch.id)
    assert dec["decision"] == "promoted" and dec["ok"]
    assert store.get_custom_voice(ch.id) is None                     # challenger folded in + removed

    # ---- Phase C: offline optimise spawns a challenger from the corpus (HTTP) ----
    opt = c.post(f"/api/voices/{vid}/optimize", headers=H)
    assert opt.status_code == 200 and opt.json()["ok"]
    opt_ch = next(v for v in store.list_custom_voices(kind=None, include_challengers=True)
                  if getattr(v, "challenger_of", "") == vid)

    # ---- Phase C: arbitrate retires a clearly-losing challenger (HTTP) ----
    store.clear_sent_items()
    for i in range(12):
        store.upsert_sent_item(SentItem(id=f"{vid}#r{i}", slug="r", name="Z", voice=vid,
                                        reply_state=ReplyState.replied))
    for i in range(8):
        store.upsert_sent_item(SentItem(id=f"{opt_ch.id}#r{i}", slug="r", name="Z", voice=opt_ch.id,
                                        reply_state=ReplyState.awaiting))
    dec2 = next(d for d in c.post("/api/voices/arbitrate", headers=H).json()["decisions"]
                if d["challenger"] == opt_ch.id)
    assert dec2["decision"] == "retired"
    assert store.get_custom_voice(opt_ch.id) is None
