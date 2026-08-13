"""The intent-first voice never capitalises the company name (Plan 28).

Two routes, two mechanisms: the subject is template substitution and uses a {company_lower} token;
the body is model output and gets a deterministic pass gated on variables["lowercase_company"].
Every other voice must be untouched by both.
"""
import pytest

from app import compose as compose_mod
from app.models import CustomVoice, Block


SPEC = {"company": "Shiplog", "contact_first": "Khushi"}


def _voice(lowercase=True):
    v = CustomVoice(id="henry_intent_v1", display_name="Intent-first",
                    subject="henry & {company_lower}",
                    blocks=[Block(id="greeting", mode="fixed", text="Hi {contact_first},"),
                            Block(id="opening", mode="ai", length="medium")])
    if lowercase:
        v.variables = {"lowercase_company": "true"}
    return v


# ---- subject ---------------------------------------------------------------

def test_company_lower_token_exists_and_is_lowercased():
    tokens = compose_mod.derive_tokens(SPEC, {})
    assert tokens["company_lower"] == "shiplog"
    assert tokens["company"] == "Shiplog", "the original token must not change"


def test_subject_renders_lowercase():
    assert compose_mod.render("henry & {company_lower}", compose_mod.derive_tokens(SPEC, {})) \
        == "henry & shiplog"


def test_unregistered_token_would_ship_literally():
    # guards the ordering hazard: render leaves unknown tokens intact rather than failing
    out = compose_mod.render("henry & {not_a_token}", compose_mod.derive_tokens(SPEC, {}))
    assert out == "henry & {not_a_token}"


# ---- body ------------------------------------------------------------------

def test_body_pass_lowercases_every_occurrence():
    parts = {"opening": "Shiplog is that same idea. I have followed SHIPLOG for a while."}
    out = compose_mod._lowercase_company(parts, SPEC)
    assert out["opening"] == "shiplog is that same idea. I have followed shiplog for a while."


def test_possessive_is_handled():
    parts = {"opening": "Shiplog's approach is the same idea."}
    assert compose_mod._lowercase_company(parts, SPEC)["opening"].startswith("shiplog's")


def test_multiword_name_is_handled():
    parts = {"opening": "House of Sillage is that same idea."}
    out = compose_mod._lowercase_company(parts, {"company": "House of Sillage"})
    assert out["opening"] == "house of sillage is that same idea."


def test_a_longer_word_containing_the_name_is_not_touched():
    parts = {"opening": "Welly is not the company. Well is."}
    out = compose_mod._lowercase_company(parts, {"company": "Well"})
    assert out["opening"] == "Welly is not the company. well is."


def test_other_proper_nouns_are_untouched():
    parts = {"opening": "Shiplog is that same idea. I studied at LSE and used Python."}
    out = compose_mod._lowercase_company(parts, SPEC)
    assert "LSE" in out["opening"] and "Python" in out["opening"]


def test_pass_is_a_noop_without_a_company():
    parts = {"opening": "Nothing to do here."}
    assert compose_mod._lowercase_company(parts, {})["opening"] == "Nothing to do here."


def test_pass_never_raises():
    for bad in (None, {"opening": None}, {None: "x"}):
        assert isinstance(compose_mod._lowercase_company(bad, None), dict)


# ---- gating ----------------------------------------------------------------

def test_gate_is_off_by_default():
    v = _voice(lowercase=False)
    assert (v.variables or {}).get("lowercase_company") != "true"
