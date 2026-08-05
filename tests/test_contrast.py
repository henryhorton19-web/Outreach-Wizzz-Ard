"""Pytest wrapper for tools/check_contrast.py."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_contrast_ratios():
    res = subprocess.run([sys.executable, str(ROOT / "tools" / "check_contrast.py")], capture_output=True, text=True)
    assert "Relative Luminance Token Contrast Check" in res.stdout, f"Contrast check did not run properly:\n{res.stdout}"
