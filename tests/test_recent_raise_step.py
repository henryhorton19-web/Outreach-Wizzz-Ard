"""The recent_raise LLM step extracts structured raise facts from raw research text.

If research contains 'raised EUR 15m in Series A funding led by Partech in March 2026',
the step extracts amount='EUR 15m', round_name='Series A', date='March 2026', and
press_signal='raised'. The derived token {recent_raise} then produces a natural
sentence like 'congratulations on the EUR 15m Series A round'.
"""
import json

from app.compose import _recent_raise_sentence, derive_tokens


def test_recent_raise_sentence_combinations():
    assert _recent_raise_sentence({"press_signal": "raised", "raise_amount": "EUR 15m",
                                     "round_name": "Series A"}) == \
        "congratulations on the EUR 15m Series A round"

    assert _recent_raise_sentence({"press_signal": "funding", "round_name": "Series B"}) == \
        "congratulations on the Series B round"

    assert _recent_raise_sentence({"press_signal": "capital", "raise_amount": "EUR 5m"}) == \
        "congratulations on the EUR 5m raise"

    assert _recent_raise_sentence({"press_signal": "raised"}) == \
        "congratulations on the recent funding"

    assert _recent_raise_sentence({"press_signal": ""}) == ""
    assert _recent_raise_sentence({}) == ""


def test_derive_tokens_includes_recent_raise():
    spec = {
        "company": "Acme",
        "facts": {
            "press_signal": "raised",
            "raise_amount": "EUR 10m",
            "round_name": "Series A",
        }
    }
    tokens = derive_tokens(spec)
    assert tokens.get("recent_raise") == "congratulations on the EUR 10m Series A round"


def test_llm_step_extracts_raise_facts():
    """Test the LLM step function when called with research containing raise info."""
    try:
        from engine.research import extract_recent_raise_facts
    except ImportError:
        from app.engine_bridge import extract_recent_raise_facts

    class MockProvider:
        is_stub = False
        provider = "gemini"

        def generate(self, **kwargs):
            class Res:
                text = json.dumps({
                    "press_signal": "raised",
                    "raise_amount": "EUR 15m",
                    "round_name": "Series A",
                    "raise_date": "March 2026"
                })
            return Res()

    research_text = "Acme raised EUR 15m in Series A funding led by Partech in March 2026."
    facts = extract_recent_raise_facts(research_text, provider=MockProvider())
    assert facts.get("press_signal") == "raised"
    assert facts.get("raise_amount") == "EUR 15m"
    assert facts.get("round_name") == "Series A"
