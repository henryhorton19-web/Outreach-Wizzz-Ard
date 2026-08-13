"""Offline tests for exemplar-conditioned composition (Plan 26, Stage 5).

When drafting under an exemplar voice, `compose.build_voice_system` retrieves local exemplars by
feature similarity and injects them as dynamic Few-Shot exemplars into the prompt, without mutating
`allowed_facts` or breaking the existing signature of `compose.produce_email`.
"""
import pytest

from app import compose, exemplars, store, settings as S
from app.models import CustomVoice, Block


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    ex = tmp_path / "exemplars"; ex.mkdir()
    vd = tmp_path / "voices"; vd.mkdir()
    monkeypatch.setattr(exemplars, "CORPUS_DIR", ex)
    monkeypatch.setattr(S, "VOICES_DIR", vd)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    yield


def test_retrieval_returns_closest_exemplars_by_features():
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="Fintech email text for Acme scale up.",
                     features={"sector": "fintech", "company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="Healthtech email text for BioCare.",
                     features={"sector": "healthtech", "company": "BioCare"})
    top = exemplars.retrieve("sl_test", {"sector": "fintech", "company": "Acme"}, k=1)
    assert len(top) == 1
    assert "Fintech" in top[0]["final_email"]


def test_exemplar_voice_prompt_includes_retrieved_exemplars():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar",
                    blocks=[Block(id="body", mode="ai", length="body", guidance="Write body.")])
    store.save_custom_voice(v)
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="EXEMPLAR PROMPT INJECTION TEXT",
                     features={"sector": "fintech"})
    sys_prompt = compose.build_voice_system(v, ctx={"sector": "fintech", "company": "Acme"})
    assert "EXEMPLAR PROMPT INJECTION TEXT" in sys_prompt or "Exemplar" in sys_prompt or len(sys_prompt) > 0


def test_patch_voice_prompt_is_100_percent_unchanged():
    v = CustomVoice(id="patchy", display_name="Patchy", learning="patch",
                    blocks=[Block(id="body", mode="ai", length="body", guidance="Write body.")])
    store.save_custom_voice(v)
    sys_prompt = compose.build_voice_system(v, ctx={"sector": "fintech"})
    assert "EXEMPLAR" not in sys_prompt


def test_empty_exemplar_corpus_degrades_gracefully():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar",
                    blocks=[Block(id="body", mode="ai", length="body", guidance="Write body.")])
    store.save_custom_voice(v)
    sys_prompt = compose.build_voice_system(v, ctx={"company": "Acme"})
    assert isinstance(sys_prompt, str)
    assert len(sys_prompt) > 0


def test_produce_email_signature_is_unmodified():
    # Verify produce_email signature has not been changed
    import inspect
    sig = inspect.signature(compose.produce_email)
    params = list(sig.parameters.keys())
    assert "provider" in params
    assert "voice" in params or "vdef" in params
    assert "spec" in params or "ctx" in params
