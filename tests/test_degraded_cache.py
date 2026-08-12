"""A salvage cache carries placeholders where facts should be.

research.py builds a minimal cache whose proof_points and thesis are literal
strings such as "research incomplete before facts were confirmed". The composer
receives those as its facts, so the body comes out empty, and a retry that reuses
the same cache produces the same emptiness.

The placeholders are fixed strings the code itself writes, so they are reliably
recognisable: measured five markers to zero against a real good cache.
"""
from app.cache_health import is_degraded, degraded_reasons


def _minimal():
    return {
        "company": {"name": "Acme", "what_they_do": ""},
        "proof_points": [{"fact": "Acme: research incomplete before facts were confirmed."}],
        "thesis": {"market_shift": "Market context was not established before research stopped.",
                   "company_positioning": "Acme (company details were not verified before research stopped)."},
        "situation_read": "",
        "research_failures": ["Salvage fell back to a minimal cache; verify everything."],
    }


def _good():
    return {
        "company": {"name": "Swan", "what_they_do": "banking as a service"},
        "proof_points": [{"fact": "Processes over 1B per month", "source": "https://x"},
                         {"fact": "Licensed by the ACPR", "source": "https://y"}],
        "thesis": {"market_shift": "Embedded finance is consolidating",
                   "company_positioning": "Swan is a BaaS platform"},
        "situation_read": "expanding across European corridors",
        "research_failures": [],
    }


def test_a_minimal_salvage_cache_is_degraded():
    assert is_degraded(_minimal())


def test_a_good_cache_is_not_degraded():
    assert not is_degraded(_good())


def test_a_salvaged_cache_with_real_facts_is_not_degraded():
    """Salvage running is not itself a problem. A cache that hit a ValidationError
    but still carries real sourced facts is usable, and re-researching it would
    spend the search budget for nothing."""
    c = _good()
    c["research_failures"] = ["ValidationError on first parse; facts salvaged"]
    assert not is_degraded(c)


def test_reasons_name_what_is_wrong():
    reasons = degraded_reasons(_minimal())
    assert reasons, "no reason given for a degraded cache"
    assert any("placeholder" in r.lower() or "incomplete" in r.lower() for r in reasons)


def test_an_empty_cache_is_degraded():
    assert is_degraded({})
    assert is_degraded(None)
