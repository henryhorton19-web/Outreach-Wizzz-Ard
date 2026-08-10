"""earned_observation must reach the composed email.

It has a schema entry, a default, preservation in both salvage paths, and is
returned to the UI. It appears zero times in compose.py, draft_engine.py and
models.py, and is absent from FACT_SCOPES. The sharpest sentence research
produces is computed, stored, displayed, and then discarded before writing.
"""
from app.models import FACT_SCOPES


def test_observation_is_a_declared_fact_scope():
    assert "earned_observation" in FACT_SCOPES, \
        "the observation cannot be requested by any block because it is not a fact scope"


def test_prepare_surfaces_the_observation():
    import engine.draft_engine as de
    cache = {
        "company": {"name": "Example Host", "role_exists": False, "company_size": "small"},
        "contact": {"name": "Theo Martin", "email": "theo.martin@example-host.test"},
        "situation_read": "scaling usage-based PaaS globally",
        "proof_points": [{"fact": "powers 100,000+ applications", "source": "https://x"}],
        "earned_observation": {
            "present": True, "mood": "question",
            "read": "guaranteeing byte-for-byte fidelity while AI agents add unpredictability",
            "basis": "stated plan plus environment cloning claim"},
    }
    spec = de.prepare(cache)
    assert spec.get("observation"), "prepare() did not surface the observation onto the spec"
    assert "byte-for-byte" in spec["observation"]


def test_an_absent_observation_is_an_empty_string_not_a_crash():
    import engine.draft_engine as de
    cache = {"company": {"name": "X", "role_exists": False, "company_size": "small"},
             "contact": {"name": "A", "email": "a@x.com"},
             "situation_read": "", "proof_points": []}
    assert de.prepare(cache).get("observation", "") == ""
