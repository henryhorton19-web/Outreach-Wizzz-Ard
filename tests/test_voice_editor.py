"""Regression tests for the Voices editor save path.

Two defects made the editor unable to save anything:

  1. `_validate_voice` read `b.owns_sci_po`, a field that had been removed from
     `Block`. The AttributeError was unhandled, so BOTH POST /api/voices and
     PUT /api/voices/{id} returned 500 for every voice that had blocks — which
     is every voice.
  2. With that fixed, three of the six SHIPPED seed voices were rejected 400:
     chief_of_staff, fractional_ops and gtm_generalist put their own id in
     `situations`, which only accepts a routing key
     (no_role_small | role_small | role_large).

The reason 171 tests missed both: every existing voice test calls
`store.save_custom_voice(...)` directly, so `_validate_voice` — the only gate
between the UI and disk — had zero route-level coverage. These tests use the
HTTP path deliberately.
"""
import json
import pathlib

import pytest

from app import settings as S


ROOT = pathlib.Path(__file__).parent.parent
SEED_DIRS = ("app/seed_voices", "app/seed_followup_voices")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


def _h():
    return {"x-wizzard-token": S.SESSION_TOKEN}


# ---------------------------------------------------------------------------
# the API's own output must be valid as its own input
# ---------------------------------------------------------------------------

def test_get_then_put_round_trip_succeeds_for_every_voice(client):
    """This is the exact operation the editor performs. It returned 500."""
    voices = client.get("/api/voices?kind=all", headers=_h()).json()["voices"]
    assert voices, "no voices to test"
    failures = []
    for v in voices:
        r = client.put(f"/api/voices/{v['id']}", json=v, headers=_h())
        if r.status_code != 200:
            failures.append((v["id"], r.status_code, r.text[:160]))
    assert not failures, f"round-trip failed: {failures}"


def test_editing_a_display_name_persists(client):
    voices = client.get("/api/voices", headers=_h()).json()["voices"]
    v = dict(voices[0])
    v["display_name"] = v["display_name"] + " (edited)"
    assert client.put(f"/api/voices/{v['id']}", json=v, headers=_h()).status_code == 200
    after = client.get("/api/voices", headers=_h()).json()["voices"]
    assert any(x["id"] == v["id"] and x["display_name"].endswith("(edited)") for x in after)


def test_creating_a_voice_through_the_route_succeeds(client):
    base = client.get("/api/voices", headers=_h()).json()["voices"][0]
    nv = dict(base, id="regression_probe", display_name="Regression Probe")
    assert client.post("/api/voices", json=nv, headers=_h()).status_code == 200
    client.delete("/api/voices/regression_probe", headers=_h())


# ---------------------------------------------------------------------------
# invalid input must be a clean 400, never a 500
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,mutation", [
    ("blank display_name", {"display_name": "   "}),
    ("no blocks",          {"blocks": []}),
    ("unknown situation",  {"situations": ["not_a_situation"]}),
    ("length inverted",    {"length_min": 200, "length_max": 10}),
])
def test_invalid_voice_is_400_not_500(client, label, mutation):
    base = client.get("/api/voices", headers=_h()).json()["voices"][0]
    payload = dict(base)
    payload.update({"id": "invalid_probe", "display_name": "Invalid Probe"})
    payload.update(mutation)          # mutation may override display_name
    r = client.post("/api/voices", json=payload, headers=_h())
    assert r.status_code == 400, f"{label}: expected 400, got {r.status_code} — {r.text[:200]}"
    assert "detail" in r.json()


def test_id_mismatch_is_400(client):
    base = client.get("/api/voices", headers=_h()).json()["voices"][0]
    r = client.put(f"/api/voices/{base['id']}", json=dict(base, id="something_else"), headers=_h())
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# the shipped seed data must satisfy the app's own validator
# ---------------------------------------------------------------------------

def _seed_files():
    out = []
    for d in SEED_DIRS:
        out.extend(sorted((ROOT / d).glob("*.json")))
    return out


@pytest.mark.parametrize("path", _seed_files(), ids=lambda p: p.stem)
def test_every_seed_voice_passes_its_own_validator(path):
    """Seeds are written straight to disk by ensure_seeded, bypassing the route,
    which is how three invalid ones shipped from the initial commit."""
    from app.models import CustomVoice
    from app.server import _validate_voice
    from fastapi import HTTPException

    v = CustomVoice.model_validate(json.loads(path.read_text(encoding="utf-8")))
    try:
        _validate_voice(v)
    except HTTPException as e:
        pytest.fail(f"{path.name} fails the app's own validator: {e.detail}")


@pytest.mark.parametrize("path", _seed_files(), ids=lambda p: p.stem)
def test_seed_situations_are_routing_keys_or_empty(path):
    """`situations` is a routing key, not a role label. Three seeds used their
    own id, so they could never auto-match any target."""
    d = json.loads(path.read_text(encoding="utf-8"))
    sits = d.get("situations") or []
    unknown = [s for s in sits if s not in S.VALID_VOICES]
    assert not unknown, (
        f"{path.name}: {unknown} not in {S.VALID_VOICES}. "
        "Use [] for a voice that is only ever chosen explicitly."
    )


def test_no_seed_voice_carries_the_removed_sci_po_flags():
    """`owns_sci_po` (per block) and `mention_sci_po` (per voice) were removed
    from the models. Pydantic silently drops unknown keys, so leaving them in
    the seeds encoded intent the code could not honour."""
    offenders = []
    for path in _seed_files():
        d = json.loads(path.read_text(encoding="utf-8"))
        if "mention_sci_po" in d:
            offenders.append(f"{path.name}: mention_sci_po")
        for b in d.get("blocks", []) or []:
            if isinstance(b, dict) and "owns_sci_po" in b:
                offenders.append(f"{path.name}: block '{b.get('id')}' owns_sci_po")
    assert not offenders, offenders


def test_block_model_has_no_owns_sci_po_attribute():
    """Locks the cause in place: if the field is ever reinstated, _validate_voice
    must be updated deliberately rather than by accident."""
    from app.models import Block
    assert "owns_sci_po" not in Block.model_fields
