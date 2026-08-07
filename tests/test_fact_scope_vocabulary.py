"""Tests that FACT_SCOPES uses profile_* vocabulary and candidate_* has been retired."""
from app.models import FACT_SCOPES


def test_profile_evidence_in_fact_scopes():
    assert "profile_evidence" in FACT_SCOPES, \
        "profile_evidence not in FACT_SCOPES -- Task H3 not landed"


def test_profile_spine_in_fact_scopes():
    assert "profile_spine" in FACT_SCOPES, \
        "profile_spine not in FACT_SCOPES -- Task H3 not landed"


def test_candidate_evidence_not_in_fact_scopes():
    assert "candidate_evidence" not in FACT_SCOPES, \
        "candidate_evidence still in FACT_SCOPES -- old vocabulary not retired"


def test_candidate_spine_not_in_fact_scopes():
    assert "candidate_spine" not in FACT_SCOPES, \
        "candidate_spine still in FACT_SCOPES -- old vocabulary not retired"
