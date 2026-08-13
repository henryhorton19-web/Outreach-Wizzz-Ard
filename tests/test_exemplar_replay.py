"""Offline tests for replay evaluation harness (Plan 26, Stage 7).

Replay simulates drafting across a sequence of targets to verify that template induction reduces
average user edit effort over baseline.
"""
import pytest

from app import exemplar_replay, exemplars, store, settings as S
from app.models import CustomVoice, Block


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    ex = tmp_path / "exemplars"; ex.mkdir()
    vd = tmp_path / "voices"; vd.mkdir()
    monkeypatch.setattr(exemplars, "CORPUS_DIR", ex)
    monkeypatch.setattr(S, "VOICES_DIR", vd)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    yield


def test_replay_returns_error_when_no_exemplars_exist():
    res = exemplar_replay.run_replay("nonexistent")
    assert res["ok"] is False
    assert "no exemplars" in res["error"].lower()


def test_replay_reports_insufficient_exemplars_when_below_min():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="Hi Ana,\n\nI saw the uptime page.",
                     features={"company": "Acme"})
    res = exemplar_replay.run_replay("sl_test")
    assert res["ok"] is True
    assert res["n_blocks"] == 0
    assert res["status"] == "insufficient_exemplars"


def test_replay_computes_effort_reduction_on_converged_corpus():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    e1 = "Hi Ana,\n\nI saw the uptime page at Acme.\n\nHappy to help."
    e2 = "Hi Bob,\n\nI saw the uptime page at Beta.\n\nHappy to help."
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e1, features={"company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email=e2, features={"company": "Beta"})
    res = exemplar_replay.run_replay("sl_test")
    assert res["ok"] is True
    assert res["n_exemplars"] == 2
    assert res["n_blocks"] >= 1
    assert "delta" in res


def test_replay_endpoint_returns_json_summary():
    v = CustomVoice(id="sl_test", display_name="SL Test", learning="exemplar")
    store.save_custom_voice(v)
    exemplars.record(voice="sl_test", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="Hi Ana,\n\nI saw the uptime page.",
                     features={"company": "Acme"})
    exemplars.record(voice="sl_test", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="Hi Bob,\n\nI saw the uptime page.",
                     features={"company": "Beta"})
    res = exemplar_replay.run_replay("sl_test")
    assert isinstance(res.get("baseline_effort"), float)
    assert isinstance(res.get("template_effort"), float)
