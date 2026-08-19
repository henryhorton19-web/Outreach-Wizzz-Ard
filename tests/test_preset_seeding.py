"""Shipped sourcing presets seed per file, not all-or-nothing.

app/settings.py used `if not existing_sp:`, so one user-created preset stopped every
shipped preset from ever seeding. Same never-overwrite class as the profile and voices.
"""
import json
import sys

import pytest


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    from app import settings as S
    S.DATA_DIR = tmp_path
    S.SOURCING_PROMPTS_DIR = tmp_path / "sourcing_prompts"
    S.CUSTOM_VOICES_DIR = tmp_path / "custom_voices"
    S.USER_PROFILES_DIR = tmp_path / "profiles"
    S.BUILD_MARKER_FILE = tmp_path / ".last_seeded_build"
    return S


def test_shipped_presets_seed_into_an_empty_directory(tmp_path, monkeypatch):
    S = _fresh(tmp_path, monkeypatch)
    S.ensure_seeded()
    assert sorted((tmp_path / "sourcing_prompts").glob("*.json")), "no presets seeded"


def test_one_user_preset_does_not_block_the_shipped_ones(tmp_path, monkeypatch):
    """The regression."""
    S = _fresh(tmp_path, monkeypatch)
    d = tmp_path / "sourcing_prompts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "my_own.json").write_text(json.dumps({
        "id": "my_own", "display_name": "Mine", "criteria_text": "whatever",
        "sources": ["grounded_search"], "recency_days": 180}), encoding="utf-8")

    S.ensure_seeded()

    seeded = sorted(p.stem for p in d.glob("*.json"))
    assert "my_own" in seeded, "the user's own preset was removed"
    assert len(seeded) > 1, f"shipped presets were skipped: {seeded}"


def test_seeding_never_overwrites_a_users_edit(tmp_path, monkeypatch):
    S = _fresh(tmp_path, monkeypatch)
    d = tmp_path / "sourcing_prompts"
    d.mkdir(parents=True, exist_ok=True)
    S.ensure_seeded()
    shipped = sorted(d.glob("*.json"))
    if not shipped:
        pytest.skip("no shipped presets to test against")
    target = shipped[0]
    edited = json.loads(target.read_text(encoding="utf-8"))
    edited["display_name"] = "Edited by the user"
    target.write_text(json.dumps(edited), encoding="utf-8")

    S.ensure_seeded()

    assert json.loads(target.read_text(encoding="utf-8"))["display_name"] == "Edited by the user"
