"""Verification suite for Stage C voice migration transform and import endpoint."""
import pytest
from app.voice_import import migrate_voice_dict
from app.models import CustomVoice
from app.server import _validate_voice


def test_migrate_voice_dict_renames_fields_and_sets_audience():
    legacy = {
        "id": "legacy_v",
        "display_name": "Legacy Voice",
        "sector_weights": {"software": 1.0},
        "custom_angles": ["Angle A"],
        "firm_positioning_note": "Identity note text",
        "blocks": [{"id": "b1", "mode": "fixed", "text": "hello", "fact_scope": []}],
    }
    migrated = migrate_voice_dict(legacy)
    assert migrated["category_weights"] == {"software": 1.0}
    assert migrated["custom_facts"] == ["Angle A"]
    assert migrated["identity_note"] == "Identity note text"
    assert migrated["audience"] == "organisation"

    # Should validate cleanly as a CustomVoice
    voice = CustomVoice.model_validate(migrated)
    _validate_voice(voice)
    assert voice.audience == "organisation"
