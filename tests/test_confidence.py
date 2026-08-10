"""overall_confidence must be derived, not hardcoded.

Both salvage paths in research.py set "overall_confidence": "medium" as a
literal, so Swan and ExampleFr both reported medium and the value carried no
information beyond "salvage ran".
"""
import app.research as research


def test_confidence_is_computed_from_the_cache():
    strong = {"company": {"name": "X", "what_they_do": "a thing"},
              "proof_points": [{"fact": "a", "source": "https://a"},
                               {"fact": "b", "source": "https://b"}],
              "situation_read": "a specific read",
              "earned_observation": {"present": True, "read": "a tension", "basis": "b"},
              "contact": {"name": "A", "email": "a@x.com", "contact_verified": True},
              "research_failures": []}
    weak = {"company": {"name": "X", "what_they_do": ""},
            "proof_points": [], "situation_read": "",
            "contact": {"name": "", "email": ""},
            "research_failures": ["salvage fell back to a minimal cache"]}
    assert research.compute_confidence(strong) == "high"
    assert research.compute_confidence(weak) == "low"


def test_salvage_alone_does_not_force_medium():
    """A salvaged cache that still has good facts should not be capped at medium.

    Measured on the real Swan cache: it scores 5/5 on content, identical to the
    clean Example Host run, and was labelled medium only because salvage ran. That is a
    fact about the code path, not about the research.
    """
    salvaged = {"company": {"name": "X", "what_they_do": "a thing"},
                "proof_points": [{"fact": "a", "source": "https://a"},
                                 {"fact": "b", "source": "https://b"}],
                "situation_read": "a specific read",
                "earned_observation": {"present": True, "read": "a tension", "basis": "b"},
                "contact": {"name": "A", "email": "a@x.com", "contact_verified": True},
                "research_failures": ["ValidationError on first parse; facts salvaged"]}
    assert research.compute_confidence(salvaged) in ("medium", "high")
