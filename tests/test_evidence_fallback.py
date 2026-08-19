"""Evidence selection must not crash when nothing scores.

engine/draft_engine.py fell back to next(iter(exps)) on an empty experiences dict,
raising StopIteration and failing every draft in a batch. It fires whenever a voice
specifies prefer: [] and pin: [] and no evidence scores above zero. The same line
hardcoded "solano", a specific experience key, inside engine logic.
"""
import pathlib

try:
    from app.engine_bridge import de, engine_config as C
except Exception:
    import engine.config as C
    import engine.draft_engine as de


def _cache():
    return {"company": {"name": "Acme", "what_they_do": "software",
                        "role_exists": False, "company_size": "small"},
            "contact": {"name": "A", "email": "a@x.com"},
            "situation_read": "", "proof_points": []}


def test_no_crash_when_the_profile_has_no_experiences(monkeypatch):
    """The regression: an empty dict raised StopIteration."""
    monkeypatch.setattr(C, "CANDIDATE_PROFILE",
                        dict(C.CANDIDATE_PROFILE, experiences={}), raising=False)
    picked = de.select_evidence(_cache(), prefer=[], pin=[], exclude=[],
                               weights={}, count=2)
    assert isinstance(picked, list) and picked


def test_the_fallback_carries_no_firm_identity(monkeypatch):
    """A fallback must not assert facts about anyone."""
    monkeypatch.setattr(C, "CANDIDATE_PROFILE",
                        dict(C.CANDIDATE_PROFILE, experiences={}), raising=False)
    blob = str(de.select_evidence(_cache(), prefer=[], pin=[], exclude=[],
                                 weights={}, count=2)).lower()
    for bad in ("hpe", "growth equity investor", "eur10m", "40m"):
        assert bad not in blob, f"fallback asserts identity: {bad!r}"


def test_no_experience_key_is_hardcoded_in_the_engine():
    """A profile without 'solano' must behave identically to one with it."""
    src = pathlib.Path("engine/draft_engine.py").read_text(encoding="utf-8")
    assert '"solano"' not in src, "a specific experience key is hardcoded in the engine"


def test_a_populated_profile_falls_back_to_a_real_experience(monkeypatch):
    monkeypatch.setattr(C, "CANDIDATE_PROFILE",
                        dict(C.CANDIDATE_PROFILE,
                             experiences={"alpha": {"name": "Alpha", "anchor": "did a thing",
                                                    "facts": ["f"], "bridges": []}}),
                        raising=False)
    picked = de.select_evidence(_cache(), prefer=[], pin=[], exclude=[],
                                weights={}, count=2)
    assert picked[0].get("_key") == "alpha"
