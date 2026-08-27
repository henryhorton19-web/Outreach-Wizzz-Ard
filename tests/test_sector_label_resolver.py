"""resolve_sector_label falls back to mandate[0], which is always "B2B Software" for
this fund's mandate, whenever a company's what_they_do text matches nothing in
_SECTOR_TERMS. Confirmed directly: of 15 real active targets, 10 fell back to
"B2B Software" including a renewable-energy company (bohr_energie) and a database
company (surrealdb), which is a false factual claim about their sector, not a
labeling nuance.

This file only tests resolution. It does NOT test whether the resolved value ever
reaches a rendered email; that is a separate, deliberately distinct concern covered
in tests/test_block_token_substitution.py (Stage 3), because resolving correctly and
substituting correctly are two different failure modes that were confirmed
independent in this build (Symptom 1 vs Symptom 2).
"""
from app.compose import resolve_sector_label


MANDATE = ["B2B Software", "FinTech", "Digital Health", "Climate Tech"]


def test_a_clear_match_resolves_correctly():
    assert "cybersecurity" in resolve_sector_label("cybersecurity startup protecting APIs", MANDATE).lower()


def test_an_unmatched_description_does_not_silently_become_the_mandate_default():
    """The exact failure from the report: a renewable energy company must not be
    labelled B2B Software just because no keyword matched."""
    result = resolve_sector_label("renewable and flexible energy assets", MANDATE)
    assert result != "B2B Software", \
        "an unmatched company fell back to mandate[0], the same defect the report found across 10 of 15 targets"


def test_an_unmatched_description_produces_an_honest_fallback_not_a_wrong_specific_one():
    """The fix must not simply pick a DIFFERENT wrong specific sector. If nothing
    matches, the fallback should read as unresolved rather than as a confident but
    unfounded claim."""
    result = resolve_sector_label("a completely novel business model with no clear category", MANDATE)
    assert result.lower() not in {m.lower() for m in MANDATE}, \
        "fell back to a mandate entry despite no match, same class of defect with a different value"


def test_a_database_company_is_not_labelled_b2b_software():
    """The literal reproduction from the diagnostic report."""
    assert resolve_sector_label("multi-model database for developers", MANDATE) != "B2B Software"


def test_a_manufacturing_company_is_not_labelled_b2b_software():
    assert resolve_sector_label("manufacturing intelligence, factory floor monitoring", MANDATE) != "B2B Software"
