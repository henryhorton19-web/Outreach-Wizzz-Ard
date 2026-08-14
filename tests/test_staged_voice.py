"""Select, verify, render, assemble (Plan 31, Stage 3).

The observed failure was that one generation held the sender's facts and the target's facts and
merged them: Fabrikam ID was described as working in outreach, Nativ as researching companies. Selection
is separated from rendering so the choice is a typed object that code can check, and the render
prompt sees exactly one fact from each side.
"""
import pytest

from app import staged_voice as SV


SENDER = [{"id": "sourcing_system", "text": "Built a sourcing and outreach system for a PE fund"},
          {"id": "econometrics", "text": "Studied econometrics at LSE"}]
TARGET = [{"id": "tf1", "text": "Turns messy product data into enriched marketplace listings"},
          {"id": "tf2", "text": "Publishes listings to multiple sales channels"}]


def _sel(**kw):
    base = {"credential_id": "sourcing_system", "target_fact_id": "tf1",
            "relation": "other_side", "link_gist": "I read messy company data; they clean it",
            "confidence": "high"}
    base.update(kw)
    return base


# ---- parsing ---------------------------------------------------------------

def test_parses_a_clean_json_object():
    assert SV.parse_selection('{"credential_id":"a","target_fact_id":"b","relation":"other_side",'
                              '"link_gist":"g","confidence":"high"}')["relation"] == "other_side"


def test_parses_through_markdown_fences():
    raw = '```json\n{"credential_id":"a","target_fact_id":"b","relation":"bare",' \
          '"link_gist":"g","confidence":"low"}\n```'
    assert SV.parse_selection(raw)["credential_id"] == "a"


def test_unparseable_selection_is_empty():
    assert SV.parse_selection("I think the best link would be...") == {}


# ---- verification ----------------------------------------------------------

def test_a_valid_selection_verifies():
    assert SV.verify_selection(_sel(), SENDER, TARGET, recent_relations=[]) == []


def test_an_invented_credential_is_rejected():
    errs = SV.verify_selection(_sel(credential_id="ml_engineer"), SENDER, TARGET, recent_relations=[])
    assert any("credential" in e for e in errs)


def test_an_invented_target_fact_is_rejected():
    errs = SV.verify_selection(_sel(target_fact_id="tf9"), SENDER, TARGET, recent_relations=[])
    assert any("target" in e for e in errs)


def test_an_unknown_relation_is_rejected():
    errs = SV.verify_selection(_sel(relation="vibes"), SENDER, TARGET, recent_relations=[])
    assert any("relation" in e for e in errs)


def test_low_confidence_is_rejected():
    errs = SV.verify_selection(_sel(confidence="low"), SENDER, TARGET, recent_relations=[])
    assert any("confidence" in e for e in errs)


def test_an_overused_relation_is_rejected():
    errs = SV.verify_selection(_sel(), SENDER, TARGET,
                               recent_relations=["other_side", "other_side", "other_side"])
    assert any("relation" in e for e in errs)


def test_verification_never_raises():
    assert isinstance(SV.verify_selection(None, None, None, recent_relations=None), list)


# ---- render prompt isolation ----------------------------------------------

def test_render_prompt_contains_only_the_chosen_facts():
    p = SV.render_prompt(_sel(), SENDER, TARGET)
    assert "Built a sourcing and outreach system" in p
    assert "Turns messy product data" in p
    assert "Studied econometrics" not in p, "the unchosen credential must not be in the render prompt"
    assert "multiple sales channels" not in p, "the unchosen target fact must not be in the prompt"


def test_render_prompt_names_the_relation_shape():
    assert "other side" in SV.render_prompt(_sel(), SENDER, TARGET).lower()


# ---- assembly --------------------------------------------------------------

def test_assemble_emits_the_pin_verbatim():
    pin = "I am reaching out because I want to move from evaluating companies to building inside one."
    out = SV.assemble_opening(pin, "I built a thing, and it broke. They fixed it.")
    assert out.startswith(pin)
    assert out == pin + " I built a thing, and it broke. They fixed it."


def test_assemble_survives_a_model_that_repeated_the_pin():
    pin = "I am reaching out because I want to move from evaluating companies to building inside one."
    out = SV.assemble_opening(pin, pin + " I built a thing.")
    assert out.count("I am reaching out because") == 1
