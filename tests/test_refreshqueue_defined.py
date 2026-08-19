"""trackDraftJob calls refreshQueue(), which must exist.

refreshQueue was called in trackDraftJob but defined nowhere. When batch drafting
completed, calling refreshQueue threw a ReferenceError, silently stopping the
completion toast and leaving the queue UI stale.

node --check cannot catch ReferenceErrors at parse time.
"""
import pathlib
import re

SRC = pathlib.Path("ui/app.js").read_text(encoding="utf-8")


def _fn(name: str) -> str:
    """Body of top-level function up to next function or boundary."""
    m = re.search(r"\n(?:async\s+)?function\s+%s\s*\([^)]*\)\s*\{" % re.escape(name), SRC)
    assert m, f"{name} not found in ui/app.js"
    start = m.end()
    depth = 1
    i = start
    while depth > 0 and i < len(SRC):
        if SRC[i] == "{":
            depth += 1
        elif SRC[i] == "}":
            depth -= 1
        i += 1
    return SRC[start:i]


def test_refreshqueue_is_called():
    assert "refreshQueue()" in SRC or "await refreshQueue()" in SRC, \
        "expected at least one call site for refreshQueue in ui/app.js"


def test_refreshqueue_is_defined():
    assert re.search(r"(?:async\s+)?function\s+refreshQueue\s*\(", SRC), \
        "refreshQueue is called in trackDraftJob but never defined"


def test_refreshqueue_updates_state_queue():
    body = _fn("refreshQueue")
    assert "state.queue =" in body or "state.queue=" in body, \
        "refreshQueue must assign the retrieved items to state.queue"


def test_refreshqueue_calls_render_queue():
    body = _fn("refreshQueue")
    assert "renderQueue()" in body, \
        "refreshQueue must call renderQueue() to refresh the UI"


def test_refreshqueue_does_not_throw_on_its_own_failure():
    body = _fn("refreshQueue")
    assert "try" in body and "catch" in body, \
        "refreshQueue must catch network errors so background callers continue safely"


def test_the_completion_toast_still_fires_after_the_queue_refresh():
    body = _fn("trackDraftJob")
    idx_ref = body.find("refreshQueue")
    idx_toast = body.find("toast")
    assert idx_ref != -1, "trackDraftJob must call refreshQueue"
    assert idx_toast != -1, "trackDraftJob must fire a completion toast"
