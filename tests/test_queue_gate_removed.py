"""Regression guards on the source, so the gate cannot come back quietly."""
import pathlib


def test_the_tier_gate_is_gone_from_the_ingest_path():
    src = pathlib.Path("app/sourcing/research_job.py").read_text(encoding="utf-8")
    assert 'if verdict == "accept" and screened.get("tier") == "Tier 1":' not in src, \
        "automated screening still decides whether a company is queued"


def test_the_job_search_branch_is_gone_from_scoring():
    src = pathlib.Path("app/sourcing/verify.py").read_text(encoding="utf-8")
    assert 'role_basis_confidence == "low" or honest_pitch_risk == "high"' not in src, \
        "a job-search proxy still affects the score"


def test_the_fields_are_still_recorded():
    """Only the gate goes. Other code may read them."""
    src = pathlib.Path("app/sourcing/verify.py").read_text(encoding="utf-8")
    assert "role_basis_confidence" in src and "honest_pitch_risk" in src


def test_the_prompt_no_longer_instructs_the_rejection():
    """Removing the code branch is not enough if the prompt still asks for the verdict."""
    src = pathlib.Path("app/prompts/sourcing_verify.md").read_text(encoding="utf-8")
    lines = [ln for ln in src.split("\n")
             if "needs_review" in ln and ("role_basis_confidence" in ln or "honest_pitch_risk" in ln)]
    assert not lines, f"the prompt still gates on a job-search proxy: {lines}"
