"""Variation pressure for the intent-first voice (Plan 30).

Guidance can specify a shape and a shape is what you get: ten letters reused one credential six
times and one link sentence verbatim six times. Variation has to come from state -- what has already
been sent -- so these tests cover detection over the exemplar corpus and the directive built from it.
"""
import pytest

from app import intent_variation as IV


LETTER_A = ("Hi Khushi,\n\nI am reaching out because I want to move from evaluating companies to "
            "building inside one. I built a system for PE funds to find companies, research them and "
            "write the outreach, and sector filters were useless. Ada is that same idea applied to "
            "customers, which is why I wanted to write.")

LETTER_B = ("Hi Joan,\n\nI am reaching out because I want to move from evaluating companies to "
            "building inside one. I built a system for PE funds to find and research companies, and "
            "what the model knew decided the shortlist. That is your thesis from the buyer's side.")

LETTER_C = ("Hi Gary,\n\nI studied behavioural economics at LSE, and Well pays for engagement rather "
            "than assuming it. Well is that at proper scale.")


# ---- move detection --------------------------------------------------------

def test_detects_named_component_move():
    assert IV.detect_move(LETTER_A) == "named_component"


def test_detects_buyer_side_move():
    assert IV.detect_move(LETTER_B) == "buyer_side"


def test_detects_scale_move():
    assert IV.detect_move(LETTER_C) == "at_proper_scale"


def test_unrecognised_shape_is_bare():
    assert IV.detect_move("Hi X,\n\nI built things. You build things too.") == "bare"


def test_every_move_has_a_prompt_line():
    for key in IV.LINK_MOVES:
        assert IV.LINK_MOVES[key].strip(), key


# ---- credential detection --------------------------------------------------

def test_detects_the_sourcing_credential():
    assert IV.detect_credential(LETTER_A) == "sourcing_system"


def test_detects_the_behavioural_economics_credential():
    assert IV.detect_credential(LETTER_C) == "behavioural_economics"


def test_unknown_credential_is_empty():
    assert IV.detect_credential("Hi X,\n\nNothing recognisable here.") == ""


# ---- the directive ---------------------------------------------------------

def test_directive_names_overused_credential_and_moves():
    d = IV.variation_directive([LETTER_A, LETTER_A, LETTER_B])
    assert "sourcing_system" in d
    assert "named_component" in d
    assert "buyer_side" in d


def test_directive_suggests_an_unused_move():
    d = IV.variation_directive([LETTER_A, LETTER_B, LETTER_C])
    unused = set(IV.LINK_MOVES) - {"named_component", "buyer_side", "at_proper_scale"}
    assert any(m in d for m in unused)


def test_directive_is_empty_with_no_history():
    assert IV.variation_directive([]) == ""


def test_directive_never_raises():
    for bad in (None, [None], [123]):
        assert isinstance(IV.variation_directive(bad), str)


# ---- relevance gate --------------------------------------------------------

def test_irrelevant_finding_is_flagged():
    notes = IV.relevance_notes(
        "most of the research was useless; I ended up rewriting the entire process",
        {"what_they_do": "measures how large language models mention and recommend a brand",
         "situation_read": "scaling GEO after a pre-seed"})
    assert notes


def test_relevant_finding_passes():
    notes = IV.relevance_notes(
        "what the model already knew about a company decided whether it made the shortlist",
        {"what_they_do": "measures how large language models mention and recommend a brand",
         "situation_read": "scaling GEO after a pre-seed"})
    assert notes == []


def test_relevance_gate_is_silent_without_company_context():
    assert IV.relevance_notes("anything at all", {}) == []


def test_relevance_never_raises():
    assert IV.relevance_notes(None, None) == []
