"""Voice selection is manual-only. No situation match, no learning-bandit tiebreak,
no sector routing -- resolve_voice() is override -> default_voice -> first-available.

Every test here fails against auto_route()/select_voice()/_learned_pick() as they
exist before this stage, because those functions currently DO select automatically.
"""
import app.pipeline as pipeline
from app import store, settings as S


def _cache(role_exists=False, size="small"):
    return {"company": {"role_exists": role_exists, "company_size": size}}


def test_auto_route_is_gone():
    assert not hasattr(pipeline, "auto_route"), \
        "auto_route still exists -- voice selection can still happen automatically"


def test_select_voice_is_gone():
    assert not hasattr(pipeline, "select_voice"), \
        "select_voice is dead code duplicating auto_route's logic; not in the source " \
        "report but found during verification -- it must go in the same pass"


def test_learned_pick_is_gone():
    assert not hasattr(pipeline, "_learned_pick"), \
        "the reply-rate bandit still exists -- it can still override an explicit choice"


def test_resolve_voice_ignores_situation_entirely(monkeypatch, tmp_path):
    """Two caches describing wildly different situations must resolve to the SAME
    voice when no override is given and no default_voice is set, because selection
    no longer looks at the cache at all -- only at override and default_voice."""
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    st = S.load_settings()
    st.default_voice = ""
    S.save_settings(st)
    a = pipeline.resolve_voice(_cache(role_exists=False, size="small"))
    b = pipeline.resolve_voice(_cache(role_exists=True, size="large"))
    assert a == b, f"resolve_voice varied by situation ({a!r} vs {b!r}) -- routing still happens"


def test_resolve_voice_honours_explicit_override_over_everything():
    voices = store.list_custom_voices()
    if not voices:
        S.ensure_seeded()
        voices = store.list_custom_voices()
    target = voices[0].id
    got = pipeline.resolve_voice(_cache(role_exists=True, size="large"), override=target)
    assert got == target


def test_voice_learning_routing_setting_is_gone():
    assert not hasattr(S.Settings, "voice_learning_routing"), \
        "voice_learning_routing still exists -- it governs WHICH voice runs, which no " \
        "longer happens automatically"
    assert not hasattr(S.Settings, "voice_explore_epsilon")


def test_validate_voice_accepts_a_descriptive_situation_label():
    """Once auto-routing is deleted, situations is a browse/filter label, not a
    routing key -- _validate_voice must stop treating anything outside
    VALID_VOICES as an error. A sector-style label like 'fintech' must be
    ACCEPTED, not rejected, the same way it is accepted as a value inside the
    JSON today."""
    from app.models import CustomVoice, Block
    from app.server import _validate_voice
    v = CustomVoice(id="probe", display_name="Probe", situations=["fintech", "digital_health"],
                    blocks=[Block(id="body", mode="fixed", text="x", fact_scope=[])])
    _validate_voice(v)   # must not raise
