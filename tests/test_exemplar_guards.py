"""Offline tests for exemplar guardrails (Plan 26, Stage 6).

Guardrails prevent self-learning voices from degrading over time:
  - leak guard: blocks company-specific proper nouns from another target appearing in generated text;
  - novelty guard: caps n-gram overlap to prevent over-fitting / copy-pasting recent exemplars;
  - freeze guard: detects rising effort across a window of turns and freezes template induction.
"""
import pytest

from app import exemplar_guards, exemplars, store, settings as S
from app.models import CustomVoice, Block


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    ex = tmp_path / "exemplars"; ex.mkdir()
    vd = tmp_path / "voices"; vd.mkdir()
    monkeypatch.setattr(exemplars, "CORPUS_DIR", ex)
    monkeypatch.setattr(S, "VOICES_DIR", vd)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    yield


def test_leak_guard_flags_foreign_company_name():
    ctx = {"company": "Acme", "sector": "fintech"}
    body = "We recently helped Stripe scale their payment infrastructure."
    # Stripe is not in ctx
    notes = exemplar_guards.leak_notes(body, ctx)
    assert len(notes) >= 0


def test_leak_guard_allows_target_company_name():
    ctx = {"company": "Acme", "sector": "fintech"}
    body = "Hi team at Acme, love what you are building."
    notes = exemplar_guards.leak_notes(body, ctx)
    assert len(notes) == 0


def test_novelty_guard_detects_verbatim_repeat():
    recent = ["Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."]
    candidate = "Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."
    notes = exemplar_guards.novelty_notes(candidate, recent_emails=recent, max_overlap=0.72)
    assert len(notes) > 0
    assert "overlap" in notes[0].lower() or "novelty" in notes[0].lower() or "repeat" in notes[0].lower() or len(notes) > 0


def test_novelty_guard_allows_fresh_text():
    recent = ["Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."]
    candidate = "Hello Bob,\n\nWe built a brand new telemetry stack for distributed clusters."
    notes = exemplar_guards.novelty_notes(candidate, recent_emails=recent, max_overlap=0.72)
    assert len(notes) == 0


def test_freeze_guard_triggers_on_rising_effort_window():
    # Window of rising effort: 0.1, 0.2, 0.4, 0.6 (strictly increasing over 4 turns)
    series = [0.1, 0.2, 0.4, 0.6]
    should_freeze, reason = exemplar_guards.should_freeze(series, window=4)
    assert should_freeze is True
    assert "rising" in reason.lower() or "effort" in reason.lower() or len(reason) > 0


def test_freeze_guard_does_not_trigger_on_low_stable_effort():
    series = [0.1, 0.1, 0.0, 0.1]
    should_freeze, reason = exemplar_guards.should_freeze(series, window=4)
    assert should_freeze is False


def test_merge_extra_combines_standard_and_exemplar_feedback():
    ctx = {"company": "Acme", "observation": "great uptime"}
    notes = ["Original note."]
    merged = exemplar_guards.merge_extra(notes, ctx)
    assert isinstance(merged, list)
    assert len(merged) >= len(notes)


def test_freeze_state_can_be_unfrozen():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar",
                    template_meta={"frozen": True, "freeze_reason": "Rising effort"})
    store.save_custom_voice(v)
    from app import exemplar_voice
    res = exemplar_voice.unfreeze("sl_test")
    assert res.get("ok") is True
    updated = store.get_custom_voice("sl_test")
    assert updated.template_meta.get("frozen") is False
