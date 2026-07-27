"""App-layer tests: pipeline orchestration, disqualifier stop, verbatim edit, research salvage,
ingest parsing. Uses the stub provider (no network)."""
import os
import pytest

from app import pipeline as P
from app import validate as V
from app import research as R
from app import ingest as I
from app.models import CompanyState, State
from app.providers.base import make_provider

STUB = make_provider("stub", None)


def _dq_cache(**over):
    base = {"company": {"name": "X", "role_exists": True, "company_size": "small",
                        "work_mode": "remote_english", "working_language": "English"},
            "proof_points": [{"fact": "does things"}],
            "contact": {"status": "found", "name": "A", "contact_verified": True}}
    base["company"].update(over)
    return base


def test_voice_selection_matrix():
    assert P.select_voice(_dq_cache(role_exists=False)) == "no_role_small"
    assert P.select_voice(_dq_cache(role_exists=True, company_size="small")) == "role_small"
    assert P.select_voice(_dq_cache(role_exists=True, company_size="large")) == "role_large"
    # override wins
    assert P.select_voice(_dq_cache(role_exists=True), override="no_role_small") == "no_role_small"


def test_disqualifier_workmode():
    dq, reason = V.is_disqualified(_dq_cache(work_mode="disqualify", disqualify_reason="Berlin on-site"))
    assert dq and "Berlin" in reason


def test_disqualifier_language():
    dq, reason = V.is_disqualified(_dq_cache(working_language="French"))
    assert dq and "French" in reason


def test_disqualifier_allows_english():
    dq, _ = V.is_disqualified(_dq_cache(working_language="English"))
    assert not dq


def test_draft_one_stub_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    cs = CompanyState(slug="acme", name="Acme", website="https://acme.example", state=State.input)
    cs = P.draft_one(STUB, cs)
    assert cs.state == State.drafted
    assert cs.voice in ("no_role_small", "role_small", "role_large")
    assert cs.machine_email and cs.machine_body
    # Sciences Po named exactly once
    assert cs.machine_email.lower().count("sciences po") == 1


def test_apply_edit_is_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    cs = CompanyState(slug="acme2", name="Acme", website="https://acme.example", state=State.input)
    cs = P.draft_one(STUB, cs)
    edited = "Totally rewritten body.\n\nWith a dash — kept verbatim."
    cs = P.apply_edit(cs, subject="My subject", email=edited)
    assert cs.final_email == edited        # not re-normalized (dash preserved)
    assert cs.subject == "My subject"
    assert cs.was_edited()


def test_reset_edit_restores_machine(tmp_path, monkeypatch):
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    cs = CompanyState(slug="acme3", name="Acme", website="https://acme.example", state=State.input)
    cs = P.draft_one(STUB, cs)
    machine = cs.machine_email
    P.apply_edit(cs, subject="x", email="changed")
    P.reset_edit(cs)
    assert cs.final_email == machine
    assert not cs.was_edited()


def test_salvage_partial_cache_is_schema_valid():
    cache = R.salvage_partial_cache("Acme", "https://a", ["https://a"], raw_text="", reason="rate limit")
    R._validate(cache)  # must not raise
    assert cache["company"]["name"] == "Acme"
    assert any("partial" in f.lower() or "incomplete" in f.lower() for f in cache["research_failures"])


def test_ingest_parses_names_and_refs():
    rows = I.parse_names("Alan\nPennylane, fintech\nQonto\tpayments")
    names = [r["name"] for r in rows]
    assert "Alan" in names and "Pennylane" in names and "Qonto" in names
    penny = next(r for r in rows if r["name"] == "Pennylane")
    assert penny.get("ref") == "fintech"


def test_ingest_dedupes():
    rows = I.parse_names("Acme\nAcme\nAcme")
    slugs = [r["slug"] for r in rows]
    assert len(set(slugs)) == len(slugs)  # unique slugs even for repeats
