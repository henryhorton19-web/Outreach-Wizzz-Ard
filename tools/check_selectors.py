"""Check element ID references in ui/app.js against IDs defined in ui/index.html."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
HTML_PATH = ROOT / "ui" / "index.html"
JS_PATH = ROOT / "ui" / "app.js"

PATTERNS = [
    r'\$\$?\(\s*[\'"]#([A-Za-z0-9_-]+)[\'"]',
    r'getElementById\(\s*[\'"]([A-Za-z0-9_-]+)[\'"]',
]
DYNAMIC_OK = {"snippetPopover", "undoSourcingBtn"}


def get_html_ids() -> set[str]:
    html_text = HTML_PATH.read_text(encoding="utf-8")
    # match id="..." or id='...'
    ids = set(re.findall(r'\bid=[\'"]([A-Za-z0-9_-]+)[\'"]', html_text))
    return ids


def check_selectors() -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    html_ids = get_html_ids()
    js_lines = JS_PATH.read_text(encoding="utf-8").splitlines()

    fails = []
    warns = []

    for line_num, line in enumerate(js_lines, start=1):
        for pattern in PATTERNS:
            for match in re.finditer(pattern, line):
                elem_id = match.group(1)
                if elem_id in DYNAMIC_OK or elem_id in html_ids:
                    continue

                # Check if guarded on the same line
                # Guard pattern: if ($("#elem_id")) or if ( ... $("#elem_id") ... )
                # e.g., if ($("#x")) or if (document.getElementById("x"))
                # Also treat as guarded if line starts with or contains 'if (' and checks elem_id before modifying
                is_guarded = bool(re.search(r'\bif\s*\(.*?' + re.escape(elem_id) + r'.*?\)', line))

                item = (elem_id, line_num, line.strip())
                if is_guarded:
                    warns.append(item)
                else:
                    fails.append(item)

    return fails, warns



# ---------------------------------------------------------------------------
# list scoping
# ---------------------------------------------------------------------------
# The bare form api("/api/queue") must be caught too: four of the six offending
# call sites used it with no query string at all, so a narrower pattern requiring
# "/api/queue/" or "/api/queue?" would have missed exactly the cases that
# motivated this check.
LIST_SCOPED = (r"/api/queue\b", r"/api/ingest_file\b")


def check_list_scoping() -> list[tuple[int, str]]:
    """Every queue/ingest call from the UI must carry list_id.

    Routes now default to store.active_list_id() rather than the literal
    "default", so an omission is no longer silently wrong, but it is still
    ambiguous. Ten nodes on this path were mis-scoped at one time or another.
    """
    js_lines = JS_PATH.read_text(encoding="utf-8").splitlines()
    bad = []
    for line_num, line in enumerate(js_lines, start=1):
        if "api(" not in line:
            continue
        if not any(re.search(p, line) for p in LIST_SCOPED):
            continue
        if "list_id" in line:
            continue
        bad.append((line_num, line.strip()[:110]))
    return bad


def check_blocking_handlers() -> list[str]:
    """Route handlers declared `async def` that never await. Each one holds the
    event loop for its full duration, freezing every other request -- the cause of
    'Pipeline/Triage/Follow-ups/Performance render blank while emails draft'.
    """
    src = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    out = []
    for b in re.split(r"(?=@app\.(?:get|post|put|delete|patch)\()", src):
        if not b.startswith("@app."):
            continue
        if "async def" in b and "await" not in b:
            m = re.search(r"async def (\w+)", b)
            if m:
                out.append(m.group(1))
    return out


def main():
    fails, warns = check_selectors()
    scoping = check_list_scoping()
    blocking = check_blocking_handlers()

    if blocking:
        print(f"=== FAIL ({len(blocking)} blocking async route handlers in server.py) ===")
        for name in blocking:
            print(f"  [FAIL] blocking handler: {name}")

    if scoping:
        print(f"=== FAIL ({len(scoping)} queue/ingest calls missing list_id) ===")
        for line_num, snippet in scoping:
            print(f"  [FAIL] Line {line_num}: {snippet}")

    if warns:
        print(f"=== WARN ({len(warns)} guarded references to missing IDs) ===")
        for elem_id, line_num, snippet in warns:
            print(f"  [WARN] Line {line_num}: #{elem_id} -> {snippet}")

    if fails:
        print(f"=== FAIL ({len(fails)} unguarded references to missing IDs) ===")
        for elem_id, line_num, snippet in fails:
            print(f"  [FAIL] Line {line_num}: #{elem_id} -> {snippet}")
        sys.exit(1)
    elif scoping or blocking:
        sys.exit(1)
    else:
        print("=== PASS (0 unguarded references to missing IDs, all queue calls scoped, 0 blocking handlers) ===")
        sys.exit(0)


if __name__ == "__main__":
    main()
