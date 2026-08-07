"""Tests for sourcing targets, attempt budgets, and stop reasons."""
import pytest
from app import store, models, settings as S
from app.sourcing import research_job as sourcing_job_mod

@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.delenv("WIZZARD_PROFILE_SOURCE", raising=False)
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    S.DATA_DIR = tmp_path
    S.DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield tmp_path

def test_sourcing_job_stops_on_target_met(clean):
    st = S.load_settings()
    fixture_items = [
        {"slug": f"co_a_{i}", "name": f"Company A {i}", "website": f"https://coa{i}.com", "summary": "AI infra"}
        for i in range(15)
    ]
    job = sourcing_job_mod.start_sourcing_job(
        settings=st,
        target_n=3,
        max_candidates=20,
        recency_days=120,
        sources=["grounded_search"],
        fixture_harvest=fixture_items,
    )
    assert job["counts"]["accepted"] == 3
    assert job["stopped_because"] == "target_met"

def test_sourcing_job_stops_on_budget_exhausted(clean):
    st = S.load_settings()
    fixture_items = [
        {"slug": f"co_b_{i}", "name": f"Company B {i}", "website": f"https://cob{i}.com", "summary": "AI infra"}
        for i in range(2)
    ]
    job = sourcing_job_mod.start_sourcing_job(
        settings=st,
        target_n=10,
        max_candidates=20,
        recency_days=120,
        sources=["grounded_search"],
        fixture_harvest=fixture_items,
    )
    assert job["counts"]["accepted"] == 2
    assert job["stopped_because"] == "budget_exhausted"

def test_custom_sourcing_prompt_target_n_field(clean):
    sp = models.CustomSourcingPrompt(
        id="test_preset",
        display_name="Test Preset",
        target_n=5,
    )
    store.save_custom_sourcing_prompt(sp)
    loaded = store.get_custom_sourcing_prompt("test_preset")
    assert loaded is not None
    assert loaded.target_n == 5
