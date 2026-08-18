"""Always produce a draft; degrade to a flag, never to a refusal (Plan 32).

Rejected design: an earlier version of this stopped a target with no contact or thin research in
State.error with no draft at all. That is explicitly reversed. This module is the replacement: it
widens the contact search with a labelled fallback, retries research once with a different angle
before giving up, and attaches a confidence flag to whatever it produces instead of refusing.
"""
import pytest

from app import confidence


# ---- contact fallback -------------------------------------------------------

def test_a_found_named_contact_is_reported_as_found():
    cache = {"contact": {"name": "Ada Lovelace", "email": "ada@x.com",
                         "email_method": "found_on_page"}}
    assert confidence.contact_flag(cache) == "found"


def test_a_placeholder_contact_falls_back_to_a_role_address():
    cache = {"company": {"resolved_domain": "x.com"},
             "contact": {"name": "Unknown", "email": "unknown@x.com"}}
    out = confidence.apply_contact_fallback(cache)
    assert out["contact"]["name"] in ("", None) or "team" in out["contact"]["name"].lower()
    assert out["contact"]["email"].endswith("@x.com")
    assert out["contact"]["email"].split("@")[0] in ("founder", "hello", "hi", "team")
    assert out["contact"]["email_method"] == "role_fallback"
    assert confidence.contact_flag(out) == "generic"


def test_a_pattern_guess_on_a_real_name_is_reported_as_guessed():
    cache = {"contact": {"name": "Ada Lovelace", "email": "ada@x.com",
                         "email_method": "pattern_guess"}}
    assert confidence.contact_flag(cache) == "guessed"


def test_fallback_is_a_noop_with_no_domain():
    cache = {"contact": {"name": "Unknown", "email": "unknown@x.com"}}
    out = confidence.apply_contact_fallback(cache)
    assert out == cache


def test_fallback_never_raises():
    for bad in (None, {}, {"contact": 3}):
        out = confidence.apply_contact_fallback(bad)
        assert isinstance(out, dict)


# ---- research thinness -------------------------------------------------------

def test_thin_research_is_flagged():
    cache = {"company": {"what_they_do": ""}, "proof_points": []}
    assert confidence.research_flag(cache) == "thin"


def test_one_proof_point_is_enough_to_call_it_full():
    cache = {"company": {"what_they_do": "sells things"}, "proof_points": [{"fact": "x"}]}
    assert confidence.research_flag(cache) == "full"


def test_research_flag_never_raises():
    for bad in (None, {}, {"company": None}):
        assert confidence.research_flag(bad) in ("full", "thin")


# ---- the flag travels with the draft ----------------------------------------

def test_draft_confidence_summarises_both():
    cache = {"company": {"what_they_do": "sells things"},
             "contact": {"name": "Ada Lovelace", "email_method": "found_on_page"},
             "proof_points": [{"fact": "x"}]}
    flags = confidence.draft_confidence(cache)
    assert flags == {"contact": "found", "research": "full"}


def test_draft_confidence_never_raises():
    assert isinstance(confidence.draft_confidence(None), dict)
