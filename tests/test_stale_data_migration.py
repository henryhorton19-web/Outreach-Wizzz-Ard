"""A data directory created by an earlier install must not silently keep stale files.

Seeding is never-overwrite, which is correct for a user's own edits and wrong for a
file inherited from a different tool. A profile carrying job-search text and a
sourcing preset carrying a job-search mandate both survived into this build, so the
app kept using them.

The general fix is a build marker: record which build last seeded, and migrate the
known-stale files aside once when the marker is missing or older.
"""
import json

import pytest


@pytest.fixture(autouse=True)
def _cleanup_data_dir_after_test(monkeypatch):
    yield
    monkeypatch.delenv("WIZZARD_DATA_DIR", raising=False)
    import app.settings as S
    import importlib
    importlib.reload(S)


def _fresh_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    import app.settings as S
    import importlib
    importlib.reload(S)
    return S


def test_a_build_marker_is_written_on_first_seed(tmp_path, monkeypatch):
    S = _fresh_data_dir(tmp_path, monkeypatch)
    S.ensure_seeded()
    marker = tmp_path / "build_version.json"
    assert marker.exists(), "no build marker written, so staleness cannot be detected"
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data.get("build"), "marker has no build identifier"


def test_a_stale_job_search_preset_is_moved_aside(tmp_path, monkeypatch):
    S = _fresh_data_dir(tmp_path, monkeypatch)
    prompts = tmp_path / "sourcing_prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "ai_infra_seed.json").write_text(json.dumps({
        "id": "ai_infra_seed",
        "display_name": "Hot AI & Infra Seed Rounds",
        "criteria_text": ("Early-stage AI, machine learning, software infrastructure, and developer "
                          "tool startups in Paris or remote-English, backed by European VCs."),
        "sources": ["techeu_funding_feed", "grounded_search"],
        "recency_days": 180,
    }), encoding="utf-8")

    S.ensure_seeded()

    assert (prompts / "ai_infra_seed.json.stale.bak").exists(), \
        "the stale preset was not backed up"
    assert not (prompts / "ai_infra_seed.json").exists(), \
        "the stale preset is still active"
    shipped = sorted(p.stem for p in prompts.glob("*.json"))
    assert shipped, "no preset was seeded to replace the stale one"


def test_a_partners_own_preset_is_left_alone(tmp_path, monkeypatch):
    """Only job-search markers trigger migration. A real mandate must survive."""
    S = _fresh_data_dir(tmp_path, monkeypatch)
    prompts = tmp_path / "sourcing_prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "my_thesis.json").write_text(json.dumps({
        "id": "my_thesis",
        "display_name": "Nordic FinTech",
        "criteria_text": "Nordic FinTech companies with recurring revenue above EUR 5m.",
        "sources": ["grounded_search"],
        "recency_days": 180,
    }), encoding="utf-8")

    S.ensure_seeded()

    assert (prompts / "my_thesis.json").exists(), "a partner's own preset was migrated away"
    assert not (prompts / "my_thesis.json.stale.bak").exists()


def test_seeding_twice_does_not_migrate_twice(tmp_path, monkeypatch):
    """The marker must make migration idempotent."""
    S = _fresh_data_dir(tmp_path, monkeypatch)
    prompts = tmp_path / "sourcing_prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "ai_infra_seed.json").write_text(json.dumps({
        "id": "ai_infra_seed", "display_name": "Hot AI",
        "criteria_text": "startups in Paris or remote-English",
        "sources": ["grounded_search"], "recency_days": 180,
    }), encoding="utf-8")

    S.ensure_seeded()
    first = sorted(p.name for p in prompts.glob("*"))
    S.ensure_seeded()
    second = sorted(p.name for p in prompts.glob("*"))
    assert first == second, "the second seed changed the directory again"
