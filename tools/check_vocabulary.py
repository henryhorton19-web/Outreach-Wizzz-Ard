"""Fail if org-audience code or copy uses a specific-organisation vocabulary term
instead of this project's generic ones (Part 1 of EXECUTION_PLAN_5).

Scanned: ui/, app/, engine/ (excluding seed data, which is allowed to use whatever
vocabulary the example organisation it represents actually uses -- that is what
makes it a believable example, not a defect).
"""
import re, sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
BANNED = {"fund voice", "portfolio company", "deal-sourcing", "fund record"}
SCAN_DIRS = ["ui", "app", "engine"]
EXCLUDE_PARTS = {"seed_", "test_", "fixtures", "__pycache__"}


def main() -> int:
    fails = []
    for d in SCAN_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.suffix not in (".py", ".js", ".html", ".md") or not p.is_file():
                continue
            if any(x in str(p) for x in EXCLUDE_PARTS):
                continue
            text = p.read_text(encoding="utf-8", errors="replace").lower()
            for term in BANNED:
                if term in text:
                    fails.append((str(p.relative_to(ROOT)), term))
    if fails:
        print("=== FAIL: organisation-specific vocabulary outside seed data ===")
        for f, t in fails:
            print(f"  [FAIL] {f}: contains {t!r}")
        return 1
    print("PASS: no organisation-specific vocabulary found outside seed/test data")
    return 0


if __name__ == "__main__":
    sys.exit(main())
