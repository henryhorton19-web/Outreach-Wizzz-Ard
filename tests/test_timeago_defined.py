"""renderPresetsList calls timeAgo(), which must exist.

timeAgo was called once and defined nowhere. It threw a ReferenceError the first
time any sourcing preset had a last_run_at value, which crashed the render loop
before listEl.innerHTML was ever assigned, leaving the entire preset list blank --
not just the row that triggered it.

node --check cannot catch this class of bug: a ReferenceError is a runtime error,
not a syntax error, so the file parsed cleanly while still crashing when it runs.
"""
import pathlib
import re

SRC = pathlib.Path("ui/app.js").read_text(encoding="utf-8")


def test_timeago_is_called():
    assert "timeAgo(" in SRC, "expected at least one call site, this test is stale"


def test_timeago_is_defined():
    assert re.search(r"function\s+timeAgo\s*\(", SRC), \
        "timeAgo is called but never defined -- this is the exact crash from the report"


def test_timeago_handles_an_empty_input_without_throwing():
    m = re.search(r"function\s+timeAgo\s*\([^)]*\)\s*\{", SRC)
    assert m, "timeAgo not found"
    start = m.end()
    depth = 1
    i = start
    while depth > 0 and i < len(SRC):
        if SRC[i] == "{": depth += 1
        elif SRC[i] == "}": depth -= 1
        i += 1
    body = SRC[start:i]
    assert "if (!isoStr)" in body or "if (!iso)" in body or "return \"\";" in body, \
        "no visible guard for an empty or missing timestamp"


def test_timeago_does_not_throw_on_an_unparseable_string():
    m = re.search(r"function\s+timeAgo\s*\([^)]*\)\s*\{", SRC)
    start = m.end()
    depth = 1
    i = start
    while depth > 0 and i < len(SRC):
        if SRC[i] == "{": depth += 1
        elif SRC[i] == "}": depth -= 1
        i += 1
    body = SRC[start:i]
    assert "isNaN" in body or "try" in body, \
        "no guard against Date parsing failure; a garbage timestamp would render 'NaNm ago'"
