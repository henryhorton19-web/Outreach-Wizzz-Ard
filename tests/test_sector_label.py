"""sector_label names the market a company operates in, for mid-sentence use.

Voices reference {sector_label} in fixed text and derive_tokens did not emit it, so it
rendered as the literal string in a sent email. what_they_do describes the COMPANY
("a B2B SaaS platform for local marketing"), and the sentence needs a MARKET, so the
value has to be derived rather than passed through.
"""
import os

os.environ.setdefault("WIZZARD_PROFILE_SOURCE", "fixture")

import app.compose as compose


def _label(what_they_do: str) -> str:
    return compose.derive_tokens({"what_they_do": what_they_do, "company": "Acme"}).get(
        "sector_label", "")


def test_the_token_exists():
    assert "sector_label" in compose.derive_tokens({"company": "Acme"})


def test_it_is_never_empty():
    """An empty value ships 'developments in  and given', which is worse than the
    literal token it replaces."""
    for spec in ({}, {"what_they_do": ""}, {"what_they_do": "   "},
                 {"what_they_do": "photonic integrated circuits"}):
        value = compose.derive_tokens(spec).get("sector_label", "")
        assert value.strip(), f"empty label for {spec!r}"


def test_it_is_short_enough_to_sit_mid_sentence():
    long_desc = ("a B2B SaaS platform for local marketing, online visibility, reputation "
                 "management and customer relationships across 150 countries")
    assert len(_label(long_desc).split()) <= 4


def test_the_longest_match_wins_not_the_first():
    """local marketing must beat SaaS, which is true of thousands of companies."""
    assert _label("B2B SaaS local marketing platform") == "local marketing"


def test_hyphens_are_normalised():
    """Real descriptions write local-marketing; the vocabulary writes local marketing."""
    assert _label("B2B SaaS local-marketing platform") == "local marketing"


def test_casing_is_preserved_from_the_vocabulary():
    """fintech and saas look careless in outbound; FinTech and SaaS do not."""
    assert _label("a FinTech company") == "FinTech"


def test_it_has_no_trailing_punctuation():
    """The sentence continues with 'and given', so punctuation breaks it."""
    for desc in ("local payments infrastructure.", "elderly care, at home", ""):
        assert not _label(desc).rstrip().endswith((".", ",", ";", ":"))


def test_real_descriptions_produce_sensible_markets():
    cases = {
        "B2B SaaS local-marketing platform for online visibility": "local marketing",
        "tech-enabled at-home elderly care platform in France": "elderly care",
        "local payments infrastructure for global e-commerce": "payments infrastructure",
        "location marketing software for multi-location brands": "location marketing",
        "embedded finance and banking-as-a-service": "embedded finance",
    }
    for desc, expected in cases.items():
        assert _label(desc) == expected, f"{desc!r} gave {_label(desc)!r}"
