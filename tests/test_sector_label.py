"""sector_label names the market a company operates in, for mid-sentence use.

The theo voice ships "monitoring developments in {sector_label}" as fixed text and
derive_tokens never emitted the token, so a founder received the literal string.

what_they_do describes the COMPANY ("a B2B SaaS platform for local marketing"); the
sentence needs a MARKET ("developments in local marketing"), so the value is derived.
"""
import os

os.environ.setdefault("WIZZARD_PROFILE_SOURCE", "fixture")

import app.compose as compose

MANDATE = ["B2B Software", "FinTech", "Digital Health", "Climate Tech"]


def _label(what_they_do: str, mandate=None) -> str:
    from app.compose import resolve_sector_label
    return resolve_sector_label(what_they_do, mandate)


def test_the_token_exists():
    assert "sector_label" in compose.derive_tokens({"company": "Acme"})


def test_it_is_never_empty():
    """An empty value ships 'developments in  and given', worse than the literal token."""
    for spec in ({}, {"what_they_do": ""}, {"what_they_do": "   "},
                 {"what_they_do": "photonic integrated circuits"}):
        assert compose.derive_tokens(spec).get("sector_label", "").strip(), spec


def test_it_is_short_enough_to_sit_mid_sentence():
    long_desc = ("a B2B SaaS platform for local marketing, online visibility, reputation "
                 "management and customer relationships across 150 countries")
    assert len(_label(long_desc).split()) <= 4


def test_the_longest_match_wins_not_the_first():
    """local marketing must beat SaaS, which is true of thousands of companies."""
    assert _label("B2B SaaS local marketing platform") == "local marketing"


def test_hyphens_are_normalised():
    assert _label("B2B SaaS local-marketing platform") == "local marketing"


def test_casing_is_preserved_from_the_vocabulary():
    """fintech and saas look careless in a partner's outbound email."""
    assert _label("a FinTech company") == "FinTech"


def test_it_has_no_trailing_punctuation():
    """The sentence continues with 'and given', so punctuation breaks it."""
    for desc in ("local payments infrastructure.", "elderly care, at home", ""):
        assert not _label(desc).rstrip().endswith((".", ",", ";", ":"))


def test_it_falls_back_to_an_honest_unresolved_fallback():
    """An unmatched description returns an honest fallback ('your market') rather than asserting mandate[0]."""
    assert _label("photonic integrated circuits", MANDATE) == "your market"
    assert _label("", MANDATE) == "your market"


def test_real_portfolio_descriptions_produce_sensible_markets():
    cases = {
        "B2B SaaS local-marketing platform for online visibility": "local marketing",
        "tech-enabled at-home elderly care platform in France": "elderly care",
        "local payments infrastructure for global e-commerce": "payments infrastructure",
        "location marketing software for multi-location brands": "location marketing",
        "embedded finance and banking-as-a-service": "embedded finance",
    }
    for desc, expected in cases.items():
        assert _label(desc) == expected, f"{desc!r} gave {_label(desc)!r}"


def test_the_theo_voice_no_longer_ships_an_unfillable_token():
    """This is the bug that motivated the plan."""
    import json
    import pathlib
    from app.settings import undefined_voice_tokens, _known_voice_tokens
    voice = json.loads(
        (pathlib.Path(__file__).parent.parent / "app/seed_voices/theo.json")
        .read_text(encoding="utf-8"))
    assert undefined_voice_tokens(voice, _known_voice_tokens()) == set()
