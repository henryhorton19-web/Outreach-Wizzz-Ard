"""Assert check_style_purity.py reports zero violations."""
import subprocess
import sys
from pathlib import Path


def test_style_purity():
    script = Path(__file__).resolve().parent.parent / "tools" / "check_style_purity.py"
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    fails = [l for l in result.stdout.splitlines() if l.startswith("[FAIL]")]
    assert not fails, "Style purity violations:\n" + "\n".join(fails)
