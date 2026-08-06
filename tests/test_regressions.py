"""Regression tests for the allow_dashes defect (§3.1).

Each test here fails before Task 8 and must keep passing afterwards.
Do not weaken an assertion to make a test pass.
"""
import engine.draft_engine as de
from app.models import CustomVoice


def test_normalize_keeps_dashes_when_allowed():
    """allow_dashes=True must survive assembly. normalize() previously stripped
    every dash unconditionally, so the knob only removed a prompt instruction."""
    txt = "A line \u2014 with an em dash."
    assert de.normalize(txt, keep_dashes=True) == txt
    assert "\u2014" not in de.normalize(txt, keep_dashes=False)


def test_critique_does_not_flag_dashes_when_voice_allows_them():
    """critique() previously hard-flagged 'em dash' regardless of the voice."""
    body = "A line \u2014 with an em dash."
    allow = {"company": "X", "voice": "v", "allow_dashes": True}
    deny = {"company": "X", "voice": "v", "allow_dashes": False}
    assert "em dash" not in de.critique(body, "", allow).hard
    assert "em dash" in de.critique(body, "", deny).hard


def test_allow_dashes_field_exists_and_defaults_false():
    assert CustomVoice(id="t", display_name="T").allow_dashes is False
    assert CustomVoice(id="t", display_name="T", allow_dashes=True).allow_dashes is True
