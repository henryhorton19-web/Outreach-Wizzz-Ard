"""Unit test for markup validity of ui/index.html."""
from pathlib import Path
from tools.check_markup import check


def test_ui_index_html_markup_is_valid():
    repo_root = Path(__file__).resolve().parent.parent
    index_html = repo_root / "ui" / "index.html"
    errors = check(str(index_html))
    assert errors == [], f"Markup validation errors found: {errors}"
