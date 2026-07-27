"""Dev launcher: run the server and open it in your browser (no pywebview required).

    python run_local.py                 # gemini/anthropic per your saved settings
    PARIS_PROVIDER=stub python run_local.py   # offline demo, no API calls

This is the easiest way to try the app without building the desktop bundle. For the packaged
double-clickable app, use main.py (pywebview) or `pyinstaller build.spec`.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
import faulthandler

if sys.version_info >= (3, 14):
    raise SystemExit(
        "Paris Outreach needs Python 3.11-3.13; detected %d.%d "
        "(3.14 breaks a dependency). Recreate .venv with python3.12 or 3.13." % sys.version_info[:2])

_fault = open(os.path.join(os.path.dirname(__file__), "faults.log"), "a")
faulthandler.enable(file=_fault)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine"))


def main() -> int:
    import uvicorn
    from app import settings as S

    host = "127.0.0.1"
    port = int(os.environ.get("PARIS_PORT", S.load_settings().port))
    url = f"http://{host}:{port}/"

    # Cross-device git sync (no-op unless the data dir is a git repo with a remote).
    import atexit
    from app import sync
    sync.on_start()
    atexit.register(sync.on_exit)

    def _open():
        import socket
        for _ in range(50):
            time.sleep(0.2)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                if s.connect_ex((host, port)) == 0:
                    try:
                        webbrowser.open(url)
                    except Exception:
                        pass
                    return

    print("=" * 60)
    print("  Paris Outreach is starting.")
    print(f"  Open:  {url}")
    if os.environ.get("PARIS_PROVIDER") == "stub":
        print("  (offline demo mode — no API calls)")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)

    threading.Thread(target=_open, daemon=True).start()
    try:
        uvicorn.run("app.server:app", host=host, port=port, log_level="warning")
    finally:
        sync.on_exit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
