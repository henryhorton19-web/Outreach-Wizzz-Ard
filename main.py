"""Desktop launcher.

Starts the FastAPI server on 127.0.0.1 in a background thread, then opens a native window
(pywebview). If pywebview isn't available or can't open a window (e.g. a headless box), it falls
back to printing the local URL so the user can open it in a browser. The server and window share
one process, so the per-launch session token the server injects into the page always matches.
"""
from __future__ import annotations

import socket
import sys
import io
import threading
import time
import os
import faulthandler

if sys.version_info >= (3, 14):
    raise SystemExit(
        "Outreach Wizz-ard needs Python 3.11-3.13; detected %d.%d "
        "(3.14 breaks a dependency). Recreate .venv with python3.12 or 3.13." % sys.version_info[:2])

_fault = open(os.path.join(os.path.dirname(__file__), "faults.log"), "a")
faulthandler.enable(file=_fault)

if getattr(sys, "frozen", False):
    if sys.stdout is None: sys.stdout = io.StringIO()
    if sys.stderr is None: sys.stderr = io.StringIO()

import uvicorn

from app import settings as S


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) != 0


def _pick_port(host: str, preferred: int) -> int:
    if _port_free(host, preferred):
        return preferred
    for p in range(preferred + 1, preferred + 40):
        if _port_free(host, p):
            return p
    raise RuntimeError("no free port found near %d" % preferred)


class _Server(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.host, self.port = host, port
        self.error = None
        cfg = uvicorn.Config("app.server:app", host=host, port=port,
                             log_level="warning", access_log=False)
        self._server = uvicorn.Server(cfg)

    def run(self):
        try:
            self._server.run()
        except BaseException as e:
            self.error = e
            raise

    def wait_until_up(self, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not _port_free(self.host, self.port):   # something is listening
                return True
            time.sleep(0.1)
        return False


def main() -> int:
    import app.server  # Pre-warm import on main thread to prevent macOS asyncio deadlock
    st = S.load_settings()
    host = "127.0.0.1"                          # never bind externally
    port = _pick_port(host, st.port)
    if port != st.port:
        st.port = port
        S.save_settings(st)

    server = _Server(host, port)
    server.start()
    if not server.wait_until_up():
        if getattr(server, 'error', None):
            print(f"ERROR: local service failed to start: {server.error}", file=sys.stderr)
        else:
            print("ERROR: local service failed to start", file=sys.stderr)
        return 1

    # Cross-device git sync (no-op unless the data dir is a git repo with a remote).
    # atexit covers every exit path below: window close, headless fallback, Ctrl+C.
    import atexit
    from app import sync
    sync.on_start()
    atexit.register(sync.on_exit)

    url = f"http://{host}:{port}/"
    print(f"DEBUG: Server started successfully on {url}. Starting native window...")
    try:
        import webview                          # pywebview
        webview.create_window("Outreach Wizz-ard", url,
                               width=1240, height=880, min_size=(940, 640))
        import os
        icon_path = os.path.join(os.path.dirname(__file__), "ui", "favicon.ico")
        if os.path.exists(icon_path):
            webview.start(icon=icon_path)       # blocks until the window closes
        else:
            webview.start()
        print("DEBUG: Window closed gracefully.")
        return 0
    except Exception as e:
        # headless / pywebview unavailable -> browser fallback
        print("\n" + "=" * 60)
        print("  Outreach Wizz-ard is running.")
        print(f"  Open this in your browser:  {url}")
        print(f"  Open this in your browser:  {url}")
        print("  (native window unavailable: %s)" % e)
        print("  Press Ctrl+C to stop.")
        print("=" * 60 + "\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
