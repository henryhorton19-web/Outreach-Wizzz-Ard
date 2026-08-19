"""Exclusion reasons distinguish companies we emailed from companies we sourced.

A company that was emailed (reason="contacted") is permanently blocked from being added
again. A company that was sourced but left in the queue (reason="sourced") is tagged so it
is never re-researched, but batched ingest does not block it if added again.
"""
import pytest

from app import settings as S, store


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.delenv("WIZZARD_PROFILE_SOURCE", raising=False)
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    S.DATA_DIR = tmp_path
    S.DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield tmp_path


def test_adding_to_exclusion_records_the_reason(clean):
    store.add_to_exclusion_set("acme_co", reason="contacted")
    assert store.is_excluded("acme_co")
    assert store.exclusion_reasons().get("acme_co") == "contacted"


def test_contacted_outranks_sourced(clean):
    """If a company was previously sourced and is later emailed, reason upgrades."""
    store.add_to_exclusion_set("beta_inc", reason="sourced")
    assert store.exclusion_reasons().get("beta_inc") == "sourced"
    store.add_to_exclusion_set("beta_inc", reason="contacted")
    assert store.exclusion_reasons().get("beta_inc") == "contacted"
    # and a second sourced tag must not downgrade it back
    store.add_to_exclusion_set("beta_inc", reason="sourced")
    assert store.exclusion_reasons().get("beta_inc") == "contacted"


def test_sourced_exclusion_does_not_block_ingest(clean):
    """The bug fixed in Task 12: batch 2 of a run was blocked by batch 1's tags."""
    from app.server import _ingest_to_queue
    store.add_to_exclusion_set("gamma_ltd", reason="sourced")
    res = _ingest_to_queue([{"slug": "gamma_ltd", "name": "Gamma Ltd",
                             "website": "https://gamma.example"}])
    assert not res.get("excluded_blocked"), \
        "a company tagged reason='sourced' was blocked by ingest"


def test_contacted_exclusion_blocks_ingest(clean):
    """An approved email must still block re-adding the same company."""
    from app.server import _ingest_to_queue
    store.add_to_exclusion_set("delta_corp", reason="contacted")
    res = _ingest_to_queue([{"slug": "delta_corp", "name": "Delta Corp",
                             "website": "https://delta.example"}])
    assert "Delta Corp" in (res.get("excluded_blocked") or []), \
        "a company tagged reason='contacted' was not blocked by ingest"
