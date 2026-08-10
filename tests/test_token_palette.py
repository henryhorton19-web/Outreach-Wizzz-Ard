"""The editor must advertise every token the engine emits.

derive_tokens produces 25 tokens; /api/meta hardcodes 14. Eleven real tokens,
including observation, link_strength, shared_subject and why, exist and work but
are invisible in the voice editor, so there is no way to discover or insert them.
"""
import os
import sys


def _emitted():
    sys.path[:0] = [p for p in (".", "engine") if p not in sys.path]
    os.environ.setdefault("WIZZARD_PROFILE_SOURCE", "fixture")
    import app.compose as compose
    return set(compose.derive_tokens({"company": "X", "contact_first": "Y"}).keys())


def _advertised():
    from app.server import get_meta
    return {t["token"] for t in get_meta()["tokens"] if t.get("kind") == "research"}


def test_every_emitted_token_is_advertised():
    missing = sorted(_emitted() - _advertised())
    assert not missing, f"tokens the engine emits but the editor never offers: {missing}"


def test_no_advertised_token_renders_blank():
    """A chip that inserts a token nothing produces is worse than no chip."""
    phantom = sorted(_advertised() - _emitted())
    assert not phantom, f"palette advertises tokens the engine does not emit: {phantom}"


def test_the_new_tokens_are_present():
    adv = _advertised()
    for t in ("observation", "link_strength", "shared_subject", "why", "recent_kind"):
        assert t in adv, f"{t} is not offered in the editor"
