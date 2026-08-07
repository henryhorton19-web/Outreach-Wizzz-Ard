"""The server must serve other requests while a long draft run is in progress.

Before this stage: draft_all is `async def` with a blocking ThreadPoolExecutor
body, so it holds the event loop for the entire batch. Every other endpoint --
/api/pipeline, /api/triage, /api/followups, /api/performance, PUT /api/settings --
queues behind it. Measured against a real uvicorn server, a 3.0s blocking handler
delayed a concurrent trivial request by 2.64s.
"""
import re
import pathlib


def _route_blocks(src: str) -> list[str]:
    """Route handlers declared `async def` that never `await` anything -- each one
    runs directly on the event loop and blocks it for its whole duration."""
    out = []
    for b in re.split(r"(?=@app\.(?:get|post|put|delete|patch)\()", src):
        if not b.startswith("@app."):
            continue
        if "async def" in b and "await" not in b:
            m = re.search(r"async def (\w+)", b)
            if m:
                out.append(m.group(1))
    return out


def test_no_route_handler_is_async_without_await():
    src = (pathlib.Path(__file__).parent.parent / "app" / "server.py").read_text(encoding="utf-8")
    offenders = _route_blocks(src)
    assert not offenders, (
        f"{len(offenders)} route handlers are `async def` with no `await` -- each blocks the "
        f"event loop for its full duration, freezing every other request. "
        f"First 10: {offenders[:10]}"
    )


def test_draft_all_does_not_block_the_loop():
    """draft_all specifically: the worst case, since it runs a whole batch."""
    src = (pathlib.Path(__file__).parent.parent / "app" / "server.py").read_text(encoding="utf-8")
    m = re.search(r"(async )?def draft_all\(.*?\n(?=\n@app\.|\n# ----)", src, re.DOTALL)
    assert m, "could not locate draft_all"
    block = m.group(0)
    if "async def draft_all" in block:
        assert "await" in block, \
            "draft_all is async but never awaits -- it holds the event loop for the entire batch"
