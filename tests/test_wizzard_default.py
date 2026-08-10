"""Tests for wizzard_default seed voice validation and attributes."""
import json
from pathlib import Path
import pytest
from app.models import CustomVoice
from app import store


def _load_wizzard_default():
    p = Path("app/seed_voices/wizzard_default.json")
    if not p.exists():
        pytest.fail("app/seed_voices/wizzard_default.json does not exist")
    data = json.loads(p.read_text(encoding="utf-8"))
    return p, data, CustomVoice.model_validate(data)


def test_wizzard_default_voice_exists_and_validates():
    p, data, voice = _load_wizzard_default()
    assert CustomVoice.model_validate(data)
    assert voice.id == "wizzard_default"


def test_wizzard_default_attributes():
    _, data, voice = _load_wizzard_default()
    assert voice.audience == "self"
    assert "raise" in (voice.recent_point_templates or {})
    assert voice.style.examples and len(voice.style.examples) > 0
    assert voice.length_max <= 120
    
    body_block = next((b for b in voice.blocks if b.id == "body"), None)
    assert body_block is not None
    assert "profile_evidence" in body_block.fact_scope
    
    assert "link_matcher_prompt" in (voice.variables or {})
