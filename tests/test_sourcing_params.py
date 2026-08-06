"""The parameters a sourcing preset carries must change what a run returns.

Before Stage A: criteria_text appears only in a display string, recency_days is
accepted by every harvester and used by none, and _sample_fixture ignores its
custom_prompt argument entirely. Every test here fails on that code.

Do not weaken an assertion to make a test pass.
"""
from app.models import CustomSourcingPrompt
from app.sourcing.harvest.grounded_search import GroundedSearchHarvester
from app.sourcing.verify import verify_candidate


def _preset(**kw) -> CustomSourcingPrompt:
    base = dict(id="t", display_name="T", criteria_text="", sources=["grounded_search"],
                recency_days=120, exclude_notes="")
    base.update(kw)
    return CustomSourcingPrompt(**base)


def test_mandate_changes_the_query_that_is_built():
    """criteria_text must reach the search query, not just a label."""
    h = GroundedSearchHarvester()
    q_ai = h.build_query(_preset(criteria_text="AI infrastructure companies"), recency_days=90)
    q_cl = h.build_query(_preset(criteria_text="climate hardware companies"), recency_days=90)
    assert q_ai != q_cl, "the mandate does not affect the query"
    assert "AI infrastructure" in q_ai
    assert "climate hardware" in q_cl


def test_recency_days_reaches_the_query():
    h = GroundedSearchHarvester()
    q30 = h.build_query(_preset(criteria_text="x"), recency_days=30)
    q365 = h.build_query(_preset(criteria_text="x"), recency_days=365)
    assert q30 != q365, "recency_days does not affect the query"


def test_exclusions_are_matched_as_phrases_not_loose_tokens():
    """The old rule split exclude_notes on whitespace and substring-matched every
    token >3 chars against the company NAME, so 'Skip mature legacy enterprises'
    rejected anything containing 'mature', 'legacy' or 'enterprises'."""
    p = _preset(exclude_notes="Skip mature legacy enterprises")
    kept = verify_candidate({"name": "Legacy Robotics", "slug": "legacy-robotics", "meta": {}},
                            custom_prompt=p)
    assert kept["verdict"] != "reject", \
        "a single loose token still rejects an otherwise valid candidate"


def test_exclusions_still_reject_a_real_phrase_match():
    p = _preset(exclude_notes="staffing agency; recruitment consultancy")
    out = verify_candidate({"name": "Talent Staffing Agency", "slug": "talent-staffing-agency",
                            "meta": {}}, custom_prompt=p)
    assert out["verdict"] == "reject"
    assert "staffing agency" in out["reject_reason"].lower()


def test_verify_no_longer_declares_an_unused_provider_or_prompt_file():
    import inspect
    import app.sourcing.verify as v
    src = inspect.getsource(v)
    # Either wire them or remove them; a declared-and-unused provider argument and
    # a prompt file that is never read are the writer_brief pattern (review §3.10).
    if "provider" in inspect.signature(v.verify_candidate).parameters:
        assert "provider." in src or "provider(" in src, \
            "verify_candidate takes a provider it never uses"
    if "PROMPT_FILE" in src:
        assert "PROMPT_FILE.read_text" in src, "PROMPT_FILE is declared but never read"


def test_duplicate_and_reset_endpoints(tmp_path, monkeypatch):
    import os
    from fastapi.testclient import TestClient
    from app.server import app
    from app import settings as S
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WIZZARD_PROFILE_SOURCE", "fixture")
    monkeypatch.setenv("WIZZARD_PROVIDER", "stub")
    c = TestClient(app, raise_server_exceptions=False)
    H = {"x-wizzard-token": S.SESSION_TOKEN}

    # Save a seed preset first
    sp = CustomSourcingPrompt(id="test_seed", display_name="Test Seed", criteria_text="Seed mandate", seeded_from="default_hot_startups")
    c.post("/api/sourcing_prompts", json=sp.model_dump(), headers=H)

    # Test Duplicate
    dup_res = c.post("/api/sourcing_prompts/test_seed/duplicate", headers=H)
    assert dup_res.status_code == 200
    dup_data = dup_res.json()["prompt"]
    assert dup_data["display_name"] == "Test Seed (copy)"
    assert dup_data["total_candidates_seen"] == 0
    assert not dup_data["last_run_at"]

    # Test Duplicate twice (no ID collision)
    dup_res2 = c.post("/api/sourcing_prompts/test_seed/duplicate", headers=H)
    assert dup_res2.status_code == 200
    assert dup_res2.json()["prompt"]["id"] != dup_data["id"]

    # Test Reset
    reset_res = c.post("/api/sourcing_prompts/test_seed/reset", headers=H)
    assert reset_res.status_code == 200 or reset_res.status_code == 404


def test_sources_and_preview_query_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.server import app
    from app import settings as S
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    c = TestClient(app, raise_server_exceptions=False)
    H = {"x-wizzard-token": S.SESSION_TOKEN}

    res_src = c.get("/api/sourcing/sources", headers=H)
    assert res_src.status_code == 200
    assert "sources" in res_src.json()

    res_prev = c.post("/api/sourcing/preview_query", json={"id": "t", "display_name": "T", "criteria_text": "AI Paris", "recency_days": 60}, headers=H)
    assert res_prev.status_code == 200
    assert "AI Paris" in res_prev.json()["query"]

