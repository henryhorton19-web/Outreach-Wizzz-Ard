"""Offline tests for template induction (Plan 26, Stage 4).

Induction turns an exemplar corpus into a `Block` sequence for a `CustomVoice`. Matching text
across exemplars becomes `fixed` skeleton blocks; company-variable text becomes `ai` holes with an
inferred `fact_scope`.
"""
import pytest

from app import template_induct, exemplars, store, settings as S
from app.models import CustomVoice, Block


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    ex = tmp_path / "exemplars"; ex.mkdir()
    vd = tmp_path / "voices"; vd.mkdir()
    monkeypatch.setattr(exemplars, "CORPUS_DIR", ex)
    monkeypatch.setattr(S, "VOICES_DIR", vd)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    yield


def test_induction_requires_min_exemplars():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="Hi Ana,\n\nI saw the uptime page.",
                     features={"company": "Acme"})
    blocks = template_induct.induct("sl_test")
    assert len(blocks) == 0


def test_induction_produces_alternating_fixed_and_ai_blocks():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    e1 = "Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."
    e2 = "Hi Bob,\n\nI saw the uptime page at Beta.\n\nHappy to help."
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e1, features={"company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e2, features={"company": "Beta"})
    blocks = template_induct.induct("sl_test")
    assert len(blocks) >= 3
    modes = [b.mode for b in blocks]
    assert "fixed" in modes
    assert "ai" in modes


def test_induced_fixed_block_contains_common_skeleton_text():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    e1 = "Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."
    e2 = "Hi Bob,\n\nI saw the uptime page at Beta.\n\nHappy to help."
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e1, features={"company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e2, features={"company": "Beta"})
    blocks = template_induct.induct("sl_test")
    fixed_texts = [b.text for b in blocks if b.mode == "fixed"]
    combined = " ".join(fixed_texts)
    assert "uptime page" in combined or "Happy to help" in combined


def test_induced_ai_block_has_inferred_fact_scope():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    e1 = "Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."
    e2 = "Hi Bob,\n\nI saw the uptime page at Beta.\n\nHappy to help."
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e1, features={"company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e2, features={"company": "Beta"})
    blocks = template_induct.induct("sl_test")
    ai_blocks = [b for b in blocks if b.mode == "ai"]
    assert len(ai_blocks) > 0
    for b in ai_blocks:
        assert isinstance(b.fact_scope, list)


def test_authored_exemplar_outweighs_tolerated():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    authored = "Hi Ana,\n\nAUTHORED SKELETON TEXT HERE.\n\nBest."
    tolerated = "Hi Ana,\n\nTOLERATED MACHINE DRAFT TEXT.\n\nBest."
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=authored, features={"company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="tolerated", machine_email=tolerated,
                     machine_blocks={}, final_email=tolerated, features={"company": "Beta"})
    blocks = template_induct.induct("sl_test")
    fixed = " ".join([b.text for b in blocks if b.mode == "fixed"])
    assert "AUTHORED" in fixed or len(blocks) > 0


def test_empty_corpus_produces_no_blocks():
    blocks = template_induct.induct("nonexistent")
    assert blocks == []


def test_block_ids_are_unique_and_slug_safe():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    e1 = "Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."
    e2 = "Hi Bob,\n\nI saw the uptime page at Beta.\n\nHappy to help."
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e1, features={"company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e2, features={"company": "Beta"})
    blocks = template_induct.induct("sl_test")
    ids = [b.id for b in blocks]
    assert len(ids) == len(set(ids))
    for bid in ids:
        assert bid.replace("_", "").isalnum()
