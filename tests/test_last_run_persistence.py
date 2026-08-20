"""A finished run must not look like a running one after a restart.

_LAST_RUN is a module global, so restarting the app resets it to None. The /last endpoint
then returns null, the frontend has nothing to reconcile against, and the status text keeps
whatever it last rendered: a completed run displays "Sourcing run in progress..." forever.
"""
import sys


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("WIZZARD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    for mod in list(sys.modules):
        if mod.startswith(("app", "engine", "config")):
            del sys.modules[mod]
    import app.sourcing.research_job as rj
    return rj


def test_a_finished_run_survives_a_restart(tmp_path, monkeypatch):
    rj = _fresh(tmp_path, monkeypatch)
    rj._persist_last_run({"job_id": "x", "status": "done",
                          "counts": {"queued": 7}, "added_list_id": "default",
                          "errors": []})
    rj._LAST_RUN = None                       # the restart
    got = rj.get_last_run()
    assert got is not None, "a finished run was lost on restart"
    assert got["status"] == "done"
    assert got["counts"]["queued"] == 7


def test_the_in_memory_copy_wins_when_present(tmp_path, monkeypatch):
    rj = _fresh(tmp_path, monkeypatch)
    rj._persist_last_run({"job_id": "old", "status": "done", "counts": {}})
    rj._LAST_RUN = {"job_id": "new", "status": "running", "counts": {}}
    assert rj.get_last_run()["job_id"] == "new"


def test_no_persisted_run_returns_none(tmp_path, monkeypatch):
    rj = _fresh(tmp_path, monkeypatch)
    rj._LAST_RUN = None
    assert rj.get_last_run() is None


def test_persisting_never_raises(tmp_path, monkeypatch):
    """A reporting convenience must not fail a run."""
    rj = _fresh(tmp_path, monkeypatch)
    rj._persist_last_run({"job_id": "x", "counts": {"weird": object()}})
