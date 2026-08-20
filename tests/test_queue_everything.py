"""Everything sourced reaches the queue. Manual review is the check.

Three automated judgements gated the queue: the verdict, the tier, and the score threshold
behind tier. The third score branch fired on role_basis_confidence == "low" or
honest_pitch_risk == "high", both job-application concepts with no bearing on whether a
company is worth approaching.

Both fields were hardcoded to safe values here, leaving that branch dormant, but the
verification prompt instructed a model to gate on them, so it would fire the moment
verification became model-driven.

The screening signal is still computed and now travels with the row instead of gating it.
"""
from app.settings import Settings


def _harvest(n=6, prefix="q_co"):
    """Fixture rows, so the job runs inline and assertions see a finished job."""
    import uuid
    uid = uuid.uuid4().hex[:6]
    return [{"slug": f"{prefix}_{uid}_{i}", "name": f"Company {i}",
             "ref": f"https://c{i}.example",
             "meta": {"source_id": "grounded_search", "hq_country": "Netherlands"},
             "raw": {"name": f"Company {i}"}} for i in range(n)]


def _run(**kw):
    import app.sourcing.research_job as rj
    params = dict(settings=Settings(), target_n=0, max_candidates=20,
                  recency_days=180, sources=["grounded_search"],
                  fixture_harvest=_harvest())
    params.update(kw)
    return rj.start_sourcing_job(**params)


def test_no_candidate_is_dropped_on_screening_grounds():
    """The regression: held and rejected were counters for companies not queued."""
    job = _run()
    counts = job.get("counts", {})
    assert counts.get("checked", 0) > 0, "nothing was checked, so this proves nothing"
    dropped = (counts.get("held", 0) or 0) + (counts.get("rejected", 0) or 0)
    assert dropped == 0, f"{dropped} candidates were not queued"


def test_every_checked_candidate_is_accepted():
    job = _run()
    counts = job.get("counts", {})
    assert counts.get("accepted", 0) == counts.get("checked", 0), \
        f"accepted {counts.get('accepted')} != checked {counts.get('checked')}"


def test_the_screening_verdict_is_still_recorded():
    """The signal informs the reviewer instead of gating the company."""
    job = _run()
    cands = job.get("candidates") or []
    assert cands, "no candidates recorded"
    assert any("verdict" in c for c in cands), "verdict was not recorded at all"


def test_target_n_counts_companies_not_screening_passes():
    """'Find me 3 companies' stops at 3 sourced, not 3 that passed a screen."""
    job = _run(target_n=3, fixture_harvest=_harvest(10))
    assert job["counts"]["accepted"] <= 4, \
        f"target of 3 produced {job['counts']['accepted']} accepted"
    assert job.get("stopped_because") == "target_met", job.get("stopped_because")


def test_dedup_still_applies():
    """Dropping the quality gate must not drop deduplication."""
    from app.server import _ingest_to_queue
    rows = [{"slug": "dupe_x", "name": "Dupe X", "website": ""}]
    first = _ingest_to_queue(list(rows), list_id="default")
    second = _ingest_to_queue(list(rows), list_id="default")

    def _n(res):
        a = res.get("added")
        return a if isinstance(a, int) else len(a or [])

    assert _n(first) == 1 and _n(second) == 0, \
        f"dedup broken: {_n(first)} then {_n(second)}"
