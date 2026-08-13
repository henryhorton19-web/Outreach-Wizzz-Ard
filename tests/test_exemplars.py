"""Offline tests for the exemplar corpus (Plan 26, Stage 1).

The corpus is the ground truth of the self-learning voice: one JSONL file per voice holding every
approved email under that voice, with the machine draft it came from, the company features it was
written for, and the provenance (authored vs tolerated). No reply/bounce signal anywhere.
"""
import json

import pytest

from app import exemplars, settings as S
from app.models import CustomVoice, Block, Style


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    d = tmp_path / "exemplars"
    d.mkdir()
    monkeypatch.setattr(exemplars, "CORPUS_DIR", d)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    yield


def _features(company="Acme", sector="fintech"):
    return {"company": company, "what_they_do": "payments infrastructure",
            "situation_read": "scaling after a raise", "sector": sector,
            "observation": "they publish uptime numbers"}


def test_authored_record_has_no_machine_draft():
    ok = exemplars.record(
        voice="sl_test", slug="acme", provenance="authored",
        machine_email="", machine_blocks={},
        final_email="Hi Ana,\n\nI saw the uptime page.\n\nHappy to help.",
        features=_features())
    assert ok is True
    recs = exemplars.load("sl_test")
    assert len(recs) == 1
    assert recs[0]["provenance"] == "authored"
    assert recs[0]["effort"] == 1.0
    assert recs[0]["machine_email"] == ""


def test_unedited_approval_is_still_recorded_at_zero_effort():
    email = "Hi Ana,\n\nI saw the uptime page.\n\nHappy to help."
    ok = exemplars.record(
        voice="sl_test", slug="acme", provenance="tolerated",
        machine_email=email, machine_blocks={"greeting": "Hi Ana,", "body": "I saw the uptime page.",
                                             "close": "Happy to help."},
        final_email=email, features=_features())
    assert ok is True
    recs = exemplars.load("sl_test")
    assert len(recs) == 1
    assert recs[0]["effort"] == 0.0


def test_frame_edit_is_recorded_not_discarded():
    machine = "Hi Ana,\n\nI saw the uptime page.\n\nHappy to help."
    final = "Ana,\n\nI saw the uptime page.\n\nWorth a chat?"
    ok = exemplars.record(
        voice="sl_test", slug="acme", provenance="tolerated",
        machine_email=machine,
        machine_blocks={"greeting": "Hi Ana,", "body": "I saw the uptime page.",
                        "close": "Happy to help."},
        final_email=final, features=_features())
    assert ok is True
    recs = exemplars.load("sl_test")
    assert 0.0 < recs[0]["effort"] < 1.0


def test_corpus_is_scoped_per_voice():
    exemplars.record(voice="voice_a", slug="a", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="A" * 40, features=_features("A"))
    exemplars.record(voice="voice_b", slug="b", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="B" * 40, features=_features("B"))
    assert len(exemplars.load("voice_a")) == 1
    assert len(exemplars.load("voice_b")) == 1


def test_no_reply_or_bounce_field_is_stored():
    exemplars.record(voice="sl_test", slug="acme", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="X" * 40, features=_features())
    rec = exemplars.load("sl_test")[0]
    for banned in ("reply_state", "replied", "bounced", "sent_id", "outcome"):
        assert banned not in rec, f"{banned} must not be in an exemplar record"


def test_records_survive_a_corrupt_line():
    p = exemplars.path("sl_test")
    p.write_text('{"broken": \n{"also broken"\n', encoding="utf-8")
    exemplars.record(voice="sl_test", slug="acme", provenance="authored", machine_email="",
                     machine_blocks={}, final_email="Y" * 40, features=_features())
    recs = exemplars.load("sl_test")
    assert len(recs) == 1


def test_effort_series_is_chronological():
    for i, eff_pair in enumerate([("aaaa bbbb cccc", "aaaa bbbb dddd"),
                                  ("aaaa bbbb cccc", "aaaa bbbb cccc")]):
        exemplars.record(voice="sl_test", slug=f"c{i}", provenance="tolerated",
                         machine_email=eff_pair[0], machine_blocks={"body": eff_pair[0]},
                         final_email=eff_pair[1], features=_features())
    series = exemplars.effort_series("sl_test")
    assert len(series) == 2
    assert series[1] == 0.0
