"""The sourcing run is threaded, choosable, stoppable, and ingests as it goes.

The job was synchronous ("Run execution synchronous for clean predictability"), so the
request blocked for the whole run: nothing to poll, and a stop button with no request to
answer, even though cancel_job and the loop's cancelled check both already existed.
"""
import time

from app.settings import Settings


class _Provider:
    """Slow enough that a test can observe a run in progress."""
    is_stub = False
    provider = "gemini"

    def __init__(self, rows=6, delay=0.05):
        self.rows, self.delay, self.calls = rows, delay, 0

    def generate(self, **kwargs):
        self.calls += 1
        time.sleep(self.delay)
        items = ",".join(
            '{"name":"Co%d","city":"Berlin","country":"Germany","press_signal":"raised",'
            '"employees_band":"51-200","website":"https://c%d.example",'
            '"source_url":"https://news.example/%d"}' % (i, i, i)
            for i in range(self.rows))

        class Result:
            text = "[" + items + "]"
            searches_used = 2
            source_urls = ["https://news.example/0"]
        return Result()


def _start(**kw):
    import app.sourcing.research_job as rj
    params = dict(settings=Settings(), target_n=5, max_candidates=10,
                  recency_days=180, sources=["grounded_search"], provider=_Provider())
    params.update(kw)
    return rj, rj.start_sourcing_job(**params)


def _wait(rj, job_id, limit=100):
    current = {}
    for _ in range(limit):
        current = rj.get_active_job(job_id) or {}
        if current.get("status") in ("done", "cancelled", "error"):
            return current
        time.sleep(0.1)
    return current


def test_the_job_returns_before_the_run_finishes():
    """The whole point: the request must not block for the run."""
    rj, job = _start()
    assert job.get("job_id")
    assert job.get("status") in ("running", "queued"), \
        f"job returned already finished with status {job.get('status')!r}"


def test_the_job_is_retrievable_while_running():
    rj, job = _start()
    assert rj.get_active_job(job["job_id"]) is not None


def test_the_job_reaches_a_terminal_state():
    rj, job = _start()
    current = _wait(rj, job["job_id"])
    assert current.get("status") in ("done", "cancelled", "error"), \
        f"never finished, last status {current.get('status')!r}"


def test_cancelling_keeps_what_was_already_found():
    """Stop now must not discard work already done."""
    rj, job = _start(target_n=50, max_candidates=50)
    time.sleep(0.3)
    assert rj.cancel_job(job["job_id"]) is True
    current = _wait(rj, job["job_id"], limit=60)
    assert current.get("status") == "cancelled"
    assert "candidates" in current


def test_the_target_list_is_captured_at_start():
    rj, job = _start()
    assert job.get("target_list_id"), "the job did not record which list it writes to"


def test_the_caller_can_choose_the_target_list():
    rj, job = _start(list_id="default")
    assert job.get("target_list_id") == "default"


def test_an_unknown_list_falls_back_and_says_so():
    """A stale id from a closed tab must not send companies nowhere."""
    rj, job = _start(list_id="no_such_list_xyz")
    assert job.get("target_list_id") != "no_such_list_xyz"
    assert any("no longer exists" in e for e in job.get("errors", []))


def test_the_job_records_ingest_progress():
    rj, job = _start()
    current = _wait(rj, job["job_id"])
    assert "queued" in current.get("counts", {})
    assert isinstance(current.get("added_slugs"), list)
