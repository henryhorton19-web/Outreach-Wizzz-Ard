"""Tests for multi-profile management, active profile tracking, filename security, and voice binding."""
import pytest
from app import store, models, settings as S

@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.delenv("WIZZARD_PROFILE_SOURCE", raising=False)
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    S.DATA_DIR = tmp_path
    S.DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield tmp_path

def test_multi_profile_crud_and_activation(clean):
    # 1. Default profile exists
    p_def = store.load_candidate_profile()
    assert p_def is not None

    # 2. List profiles
    profs = store.list_profiles()
    assert len(profs) >= 1
    assert store.active_profile_id() == "default"

    # 3. Create a new profile (e.g. personal)
    p_personal = store.create_profile("personal", "Personal Profile")
    assert p_personal.id == "personal"

    # 4. Activate personal profile
    ok = store.set_active_profile("personal")
    assert ok is True
    assert store.active_profile_id() == "personal"

    # 5. Load active profile returns personal profile
    cur = store.load_candidate_profile()
    assert cur["id"] == "personal"
    assert cur["full_name"] == "Personal Profile"

    # 6. Delete profile
    store.set_active_profile("default")
    del_ok = store.delete_profile("personal")
    assert del_ok is True
    assert store.get_profile("personal") is None

def test_profile_id_filename_security(clean):
    with pytest.raises(ValueError):
        store.create_profile("../traversal", "Traversal")
    with pytest.raises(ValueError):
        store.create_profile("CON", "Reserved")

def test_voice_default_profile_binding(clean):
    voice = models.CustomVoice(id="firm_voice", display_name="Firm Voice", default_profile_id="firm_prof")
    assert voice.default_profile_id == "firm_prof"
