"""Offline tests for the blank-box authoring path (Plan 26, Stage 2).

Turn 0 of a self-learning voice generates nothing: the user writes the email. The draft must be
editable (machine_email must be "" and not None), approving it must store an AUTHORED exemplar, and
the machine-draft path must be untouched for every other voice.
"""
import pytest
from fastapi.testclient import TestClient

from app import store, exemplars, pipeline, settings as S
from app.models import CustomVoice, Block, State, TargetState


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    vd = tmp_path / "voices"; vd.mkdir()
    ex = tmp_path / "exemplars"; ex.mkdir()
    monkeypatch.setattr(S, "VOICES_DIR", vd)
    monkeypatch.setattr(S, "VOICE_HISTORY_DIR", tmp_path / "vh")
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(exemplars, "CORPUS_DIR", ex)
    yield


def _sl_voice(vid="sl_paris"):
    v = CustomVoice(id=vid, display_name="Self-learning", learning="exemplar",
                    blocks=[Block(id="body", mode="ai", length="body", guidance="Write it.")])
    store.save_custom_voice(v)
    return v


def test_blank_returns_an_empty_editable_draft():
    _sl_voice()
    cs = TargetState(slug="acme", name="Acme")
    out = pipeline.author_blank(cs, voice_id="sl_paris")
    assert out.machine_email == ""
    assert out.machine_email is not None
    assert out.machine_blocks == {}
    assert out.state == State.in_review
    assert out.voice == "sl_paris"


def test_blank_refuses_a_non_exemplar_voice():
    v = CustomVoice(id="patchy", display_name="Patchy", learning="patch")
    store.save_custom_voice(v)
    cs = TargetState(slug="acme", name="Acme")
    out = pipeline.author_blank(cs, voice_id="patchy")
    assert out.state == State.error
    assert "exemplar" in (out.error or "")


def test_authored_email_records_an_authored_exemplar():
    _sl_voice()
    ok = exemplars.record(voice="sl_paris", slug="acme", provenance="authored",
                          machine_email="", machine_blocks={},
                          final_email="Hi Ana,\n\nSaw the uptime page.\n\nWorth a chat?",
                          features={"company": "Acme"})
    assert ok
    recs = exemplars.load("sl_paris")
    assert recs[0]["provenance"] == "authored"
    assert recs[0]["weight"] == exemplars.WEIGHT["authored"]
