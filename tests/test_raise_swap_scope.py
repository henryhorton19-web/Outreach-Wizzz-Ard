"""The raise-swap replaces ONE block, and {first_name} is not a real token.

Reproduced on fa0eba8: with recent_point.kind == "raise", five fixed blocks all
rendered as "Congratulations on your recent ..." -- the greeting, opening,
recent_context, fund_paragraph and close. The email became one sentence repeated
around the body.

These tests run the FULL produce_email path deliberately. Testing resolve_fixed
in isolation cannot catch a bug about WHICH block, because in isolation you only
ever call it for one.
"""
import json
import pathlib

import pytest

from app.models import CustomVoice


def _voice(name="Colleague1"):
    root = pathlib.Path(__file__).parent.parent
    return CustomVoice.model_validate(
        json.loads((root / "app" / "seed_voices" / f"{name}.json").read_text(encoding="utf-8")))


def _cache(kind="raise", present=True):
    return {
        "company": {"name": "Acme", "role_exists": False, "company_size": "small"},
        "contact": {"name": "Jane Doe", "email": "jane@acme.io"},
        "recent_point": ({"present": True, "kind": kind, "detail": "a EUR 20m Series B"}
                         if present else {"present": False}),
        "situation_read": "scaling",
        "proof_points": [{"fact": "f", "source": "https://x"}],
    }


def _produce(voice, cache):
    import engine.draft_engine as de
    import app.compose as compose
    from app.providers import make_provider
    prov = make_provider("stub", "")
    spec = de.prepare(cache)
    tokens = compose.derive_tokens(spec, voice.variables)
    return compose.produce_email(prov, voice, spec, tokens, [])


@pytest.mark.parametrize("vname", ["Colleague1", "Colleague2", "Colleague3"])
def test_only_the_opening_block_is_swapped(vname):
    voice = _voice(vname)
    _, parts, _ = _produce(voice, _cache())
    swapped = [b.id for b in voice.blocks
               if b.mode == "fixed" and (parts.get(b.id) or "").startswith("Congratulations")]
    assert swapped == ["opening"], f"{vname}: expected only 'opening', got {swapped}"


def test_the_greeting_survives_a_raise():
    _, parts, _ = _produce(_voice(), _cache())
    assert (parts.get("greeting") or "").startswith("Hi "), \
        f"greeting was overwritten: {parts.get('greeting')!r}"


def test_no_seed_voice_uses_an_undefined_token():
    """{first_name} is not produced by derive_tokens -- render() leaves unknown
    tokens literal, so it ships to the recipient."""
    import app.compose as compose
    known = set(compose.derive_tokens({"company": "X", "contact_first": "Y"}).keys())
    root = pathlib.Path(__file__).parent.parent
    offenders = []
    for d in ("app/seed_voices", "app/seed_followup_voices"):
        for f in sorted((root / d).glob("*.json")):
            if "{first_name}" in f.read_text(encoding="utf-8") and "first_name" not in known:
                offenders.append(f.name)
    assert not offenders, f"voices use a token that does not exist: {offenders}"


def test_the_standing_opener_ships_with_no_recent_point():
    _, parts, _ = _produce(_voice(), _cache(present=False))
    assert not (parts.get("opening") or "").startswith("Congratulations")
