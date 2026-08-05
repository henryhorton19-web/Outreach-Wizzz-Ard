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


def main():
    fails, warns = check_selectors()

    if warns:
        print(f"=== WARN ({len(warns)} guarded references to missing IDs) ===")
        for elem_id, line_num, snippet in warns:
            print(f"  [WARN] Line {line_num}: #{elem_id} -> {snippet}")

    if fails:
        print(f"=== FAIL ({len(fails)} unguarded references to missing IDs) ===")
        for elem_id, line_num, snippet in fails:
            print(f"  [FAIL] Line {line_num}: #{elem_id} -> {snippet}")
        sys.exit(1)
    else:
        print("=== PASS (0 unguarded references to missing IDs) ===")
        sys.exit(0)


if __name__ == "__main__":
    main()
