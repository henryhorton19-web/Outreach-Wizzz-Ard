"""Offline unit tests for sourcing research subsystem."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from app import settings as S
from app import store
from app.sourcing.gates import evaluate_location_language_gate
from app.sourcing.harvest.techeu_funding_feed import TechEuFundingFeed
from app.sourcing.harvest.franceinvest_directory import FranceInvestDirectoryHarvester
from app.sourcing.seen import is_seen, record_seen
from app.sourcing.verify import verify_candidate
from app.sourcing.research_job import start_sourcing_job, undo_sourcing_job


def test_harvest_techeu_fixture():
    feed = TechEuFundingFeed()
    items = feed.harvest(recency_days=120)
    assert len(items) >= 2
    first = items[0]
    assert "slug" in first
    assert "name" in first
    assert first["meta"]["hq_city"] in ("Paris", "France")
    assert "funding_heat" in first["meta"]


def test_harvest_franceinvest_fixture():
    harvester = FranceInvestDirectoryHarvester()
    items = harvester.harvest(recency_days=120)
    assert len(items) >= 1
    for item in items:
        # Invariant C10: no paywalled detail URLs
        url = item["meta"]["source_url"]
        assert not ("/annuaire/" in url and url.count("/") > 4)


def test_no_paywalled_fetch():
    harvester = FranceInvestDirectoryHarvester()
    fake_paywalled_item = [{
        "name": "Secret Fund",
        "city": "Paris",
        "source_url": "https://www.franceinvest.org/annuaire/detail/secret-fund-12345/private",
    }]
    items = harvester.harvest(fixture_data=fake_paywalled_item)
    assert len(items) == 0, "Paywalled detail URLs must be filtered out per C10"


def test_gates_pass_everything_with_no_allow_list():
    assert evaluate_location_language_gate("France", "Paris") == "pass"
    assert evaluate_location_language_gate("", "", is_remote_english=True) == "pass"
    assert evaluate_location_language_gate("Germany", "Berlin", is_remote_english=False) == "pass"
    assert evaluate_location_language_gate("Germany", "Berlin", is_remote_english=False, allowed_locations={"france", "paris"}) == "disqualify"


def test_location_language_gate_honors_explicit_allowlist():
    raw_disqualified = {
        "name": "Berlin Hardware Tech",
        "slug": "berlin_hardware",
        "city": "Berlin",
        "country": "Germany",
        "is_remote_english": False,
        "allowed_locations": {"france", "paris"},
        "ref": "https://berlinhardware.de",
        "meta": {"hq_city": "Berlin", "hq_country": "Germany"}
    }
    res = verify_candidate(raw_disqualified)
    assert res["gates"]["location_language_gate"] == "disqualify"
    assert res["verdict"] in ("needs_review", "reject")
    assert res["tier"] == "Needs Review"


def test_seen_slug_skips_before_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(S, "SOURCING_PROMPTS_DIR", tmp_path / "sourcing_prompts")
    (tmp_path / "sourcing_prompts").mkdir(parents=True, exist_ok=True)

    slug = "test_seen_co"
    record_seen(slug, "Test Seen Co", verdict="accept")

    assert is_seen(slug, expiry_days=60) is True


def test_second_run_reports_novelty_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(S, "SOURCING_PROMPTS_DIR", tmp_path / "sourcing_prompts")
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    (tmp_path / "sourcing_prompts").mkdir(parents=True, exist_ok=True)

    st = S.load_settings()
    st.sourcing_enabled = True

    fixture = [
        {"slug": "co_a", "name": "Co A", "meta": {"hq_city": "Paris", "hq_country": "France"}},
        {"slug": "co_b", "name": "Co B", "meta": {"hq_city": "Paris", "hq_country": "France"}},
    ]

    job1 = start_sourcing_job(settings=st, fixture_harvest=fixture)
    assert job1["counts"]["new"] == 2
    assert job1["counts"]["already_seen"] == 0

    job2 = start_sourcing_job(settings=st, fixture_harvest=fixture)
    assert job2["counts"]["already_seen"] == 2
    assert job2["counts"]["new"] == 0


def test_undo_removes_only_untouched_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(S, "SOURCING_PROMPTS_DIR", tmp_path / "sourcing_prompts")
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    (tmp_path / "sourcing_prompts").mkdir(parents=True, exist_ok=True)

    st = S.load_settings()
    st.sourcing_enabled = True

    fixture = [
        {"slug": "undo_co_1", "name": "Undo Co 1", "meta": {"hq_city": "Paris", "hq_country": "France"}},
    ]

    job = start_sourcing_job(settings=st, fixture_harvest=fixture)
    assert len(store.load_queue()) == 1

    undo_res = undo_sourcing_job(job["job_id"])
    assert undo_res["removed"] == 1
    assert len(store.load_queue()) == 0
