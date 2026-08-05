"""Pytest wrapper for tools/check_selectors.py."""
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.check_selectors import check_selectors


def test_unguarded_selectors():
    fails, warns = check_selectors()
    if warns:
        print(f"\n[INFO] {len(warns)} guarded references to missing IDs:")
        for elem_id, line_num, snippet in warns:
            print(f"  Line {line_num}: #{elem_id} -> {snippet}")
    assert not fails, f"Found {len(fails)} unguarded references to missing element IDs:\n" + "\n".join(
        f"  Line {line_num}: #{elem_id} -> {snippet}" for elem_id, line_num, snippet in fails
    )
