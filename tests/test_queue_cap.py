"""The queue cap is unlimited by default and never deletes what is already queued.

QUEUE_CAP was 500, enforced in two places. server.py refused new companies and reported
them, which was fine. upsert_queue_batch truncated the SAVED queue to the newest 500 and
wrote it to disk, silently removing older rows: a user at 498 adding 10 kept 500 and
lost 8, while over_cap reported only the 8 refused.
"""
import pytest

from app import settings as S, store


@pytest.fixture
def clean(tmp_path, monkeypatch):
    # Copied from tests/test_sourcing_targets.py, where it is local rather than in
    # conftest.py. It mutates S.DATA_DIR as a module global, so promoting it to a shared
    # fixture could affect unrelated tests.
    monkeypatch.delenv("WIZZARD_PROFILE_SOURCE", raising=False)
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    S.DATA_DIR = tmp_path
    S.DATA_DIR.mkdir(parents=True, exist_ok=True)
    yield tmp_path


def _rows(n, prefix="cap"):
    return [{"slug": f"{prefix}_{i}", "name": f"Company {i}",
             "website": f"https://c{i}.example"} for i in range(n)]


def test_the_default_cap_is_unlimited():
    assert store.QUEUE_CAP in (0, None) or store.QUEUE_CAP > 100000, \
        f"QUEUE_CAP is {store.QUEUE_CAP!r}, which still imposes a practical limit"


def test_adding_beyond_the_old_cap_keeps_everything(clean):
    store.upsert_queue_batch(_rows(600), list_id="default")
    assert len(store.load_queue(list_id="default")) == 600


def test_a_second_batch_does_not_evict_the_first(clean):
    """The data-loss case: older rows were removed, not refused."""
    store.upsert_queue_batch(_rows(400, "first"), list_id="default")
    store.upsert_queue_batch(_rows(300, "second"), list_id="default")
    slugs = {q["slug"] for q in store.load_queue(list_id="default")}
    missing = [f"first_{i}" for i in range(400) if f"first_{i}" not in slugs]
    assert not missing, f"{len(missing)} companies from the first batch were deleted"


def test_ingest_reports_nothing_over_cap_when_unlimited(clean):
    from app.server import _ingest_to_queue
    res = _ingest_to_queue(_rows(600, "ing"), list_id="default")
    assert not res.get("over_cap"), \
        f"{len(res.get('over_cap') or [])} refused despite an unlimited cap"


def test_a_configured_cap_refuses_rather_than_deleting(clean, monkeypatch):
    """The mechanism stays usable, and must refuse rather than delete."""
    store.upsert_queue_batch(_rows(10, "keep"), list_id="default")
    monkeypatch.setattr(store, "QUEUE_CAP", 12)
    from app.server import _ingest_to_queue
    res = _ingest_to_queue(_rows(10, "extra"), list_id="default")
    slugs = {q["slug"] for q in store.load_queue(list_id="default")}
    for i in range(10):
        assert f"keep_{i}" in slugs, "an already-queued company was deleted by the cap"
    assert res.get("over_cap"), "a configured cap refused nothing"
