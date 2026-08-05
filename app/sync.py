"""Optional git-backed sync of the data directory across a user's own devices.

Model (grounded in obsidian-git's proven pattern for a solo user):
  * pull --rebase --autostash on startup      -- the "pull before you write" golden rule
  * commit + push on exit, plus a periodic push while the app is open
  * single-writer: a local lock file guards against two instances on ONE machine.
    Real conflicts (from editing two devices without syncing) are surfaced in a
    SYNC_CONFLICT.md note and left for manual resolution -- never auto-merged, because
    naive JSON merges corrupt structured data.

Everything here fails OPEN. If git is missing, no repo/remote is configured, or the
network is down, the app runs exactly as before and sync simply no-ops. No secret ever
touches the repo: API keys live only in the OS keychain (see app/keys.py), and the data
dir's .gitignore excludes caches, the outbox, attachments (kept device-local by choice),
and the per-session stats file.

Sync turns on automatically once DATA_DIR is a git repo WITH a remote. Force it off with
the environment variable PARIS_SYNC=0.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import subprocess
import threading
import time
from pathlib import Path

from . import settings as S

DATA_DIR: Path = S.DATA_DIR
LOCK_FILE = DATA_DIR / ".wizzard.lock"
CONFLICT_FILE = DATA_DIR / "SYNC_CONFLICT.md"

# Never let git block on an interactive credential prompt -- fail fast instead.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}

_push_lock = threading.Lock()
_stop = threading.Event()
_state = {"enabled": False, "conflict": False, "have_lock": False, "last_push": 0.0}

_GITIGNORE = """\
# Outreach Wizz-ard synced data dir -- track durable state, skip regenerable / device-local files.
caches/
outbox/
attachments/
session_stats.json
faults.log
.wizzard.lock
SYNC_CONFLICT.md
__pycache__/
*.pyc
"""

_GITATTRIBUTES = """\
*.json merge=union
"""


# ---- low-level git ---------------------------------------------------------

def _git(*args: str, timeout: float = 30.0) -> tuple[int, str, str]:
    try:
        res = subprocess.run(
            ["git", "-C", os.fspath(DATA_DIR)] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_GIT_ENV,
            check=False,
        )
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except (subprocess.SubprocessError, OSError) as e:
        return 1, "", str(e)


def _is_repo() -> bool:
    code, _, _ = _git("rev-parse", "--git-dir")
    return code == 0


def _has_remote() -> bool:
    code, out, _ = _git("remote")
    return code == 0 and bool(out)


def enabled() -> bool:
    if os.environ.get("WIZZARD_SYNC") == "0" or os.environ.get("PARIS_SYNC") == "0":
        return False
    return _is_repo() and _has_remote()


def _ensure_scaffolding() -> None:
    gi = DATA_DIR / ".gitignore"
    if not gi.exists():
        S.atomic_write_text(gi, _GITIGNORE)
    ga = DATA_DIR / ".gitattributes"
    if not ga.exists():
        S.atomic_write_text(ga, _GITATTRIBUTES)


# ---- single-writer lock ----------------------------------------------------

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def acquire_lock() -> bool:
    """Take the local writer lock. Returns False only if a LIVE instance on THIS
    machine already holds it (prevents two local copies fighting over the repo)."""
    host = socket.gethostname()
    if LOCK_FILE.exists():
        try:
            info = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
            if info.get("host") == host and _pid_alive(int(info.get("pid", -1))):
                return False
        except Exception:
            pass  # stale / corrupt lock -> reclaim it
    try:
        S.atomic_write_text(LOCK_FILE, json.dumps(
            {"pid": os.getpid(), "host": host, "ts": time.time()}))
        _state["have_lock"] = True
    except Exception:
        pass  # lock is best-effort; never block the app on it
    return True


def release_lock() -> None:
    if _state.get("have_lock"):
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
        _state["have_lock"] = False


# ---- pull / push -----------------------------------------------------------

def _rebase_active() -> bool:
    code, gd, _ = _git("rev-parse", "--git-dir")
    if code != 0:
        return False
    base = Path(gd) if os.path.isabs(gd) else (DATA_DIR / gd)
    return (base / "rebase-merge").exists() or (base / "rebase-apply").exists()


def _clear_incoming_untracked() -> None:
    """Remove untracked files that the remote is about to deliver -- i.e. regenerable seed
    data (voices/*, .gitignore, .gitattributes) the app writes on startup before sync runs.
    Without this, a device's FIRST pull aborts with "untracked working tree files would be
    overwritten". Remote wins for these; tracked changes and device-unique untracked files
    are never touched, so no real edit is ever lost."""
    if _git("fetch", timeout=60)[0] != 0:
        return
    c1, others, _ = _git("ls-files", "--others", "--exclude-standard")
    if c1 != 0 or not others:
        return
    c2, tree, _ = _git("ls-tree", "-r", "--name-only", "@{u}")
    if c2 != 0:
        return
    incoming = set(tree.splitlines())
    for rel in others.splitlines():
        if rel in incoming:
            try:
                (DATA_DIR / rel).unlink()
            except OSError:
                pass


def pull() -> str:
    """Returns 'ok', 'conflict', or 'skipped' (benign/offline). Never raises."""
    if not _state["enabled"]:
        return "skipped"
    _clear_incoming_untracked()
    code, _out, err = _git("pull", "--rebase", "--autostash", timeout=60)
    if code == 0:
        return "ok"
    # A non-zero pull is only a real conflict if a rebase actually stalled on
    # conflicting hunks. Everything else (offline, no upstream yet, transient network)
    # is benign: leave the tree untouched, don't alarm, let the next sync retry.
    uc, unmerged, _ = _git("diff", "--name-only", "--diff-filter=U")
    if _rebase_active() or (uc == 0 and unmerged):
        # Diverging history: another device edited without syncing. Keep the working
        # tree clean + usable and hand the reconcile to the user -- nothing is lost.
        _git("rebase", "--abort")
        _state["conflict"] = True
        try:
            S.atomic_write_text(CONFLICT_FILE,
                "# Sync conflict\n\n"
                "Another device pushed changes that diverge from this machine's local\n"
                "history, so an automatic rebase couldn't apply cleanly. **Your local\n"
                "data is intact and the app is usable**, and the other device's commits\n"
                "are safe on the remote -- nothing was lost.\n\n"
                "To reconcile, open a terminal in this data directory and run:\n\n"
                "```\n"
                "git pull --rebase\n"
                "# resolve any conflicting file(s), then:\n"
                "git rebase --continue && git push\n"
                "```\n\n"
                "Delete this file when done. Automatic push is paused until it's gone.\n")
        except Exception:
            pass
        return "conflict"
    last = err.splitlines()[-1] if err else "remote unavailable"
    print(f"  [sync] pull skipped ({last}); will retry next sync.")
    return "skipped"


def _do_push(reason: str) -> None:
    if not _state["enabled"] or _state["conflict"]:
        return
    # A corrupt local store presents as EMPTY data (store._read_json_or_degrade).
    # Committing that state would replicate the loss to every other machine on
    # the next pull, so refuse while degraded.
    from . import store as _store
    if getattr(_store, "DEGRADED", None):
        print(f"  [sync] REFUSING to commit: {_store.DEGRADED}", file=sys.stderr)
        return
    with _push_lock:
        _git("add", "-A")
        _c, porcelain, _e = _git("status", "--porcelain")
        cc, ahead_out, _ = _git("rev-list", "--count", "@{u}..HEAD")
        ahead = cc == 0 and ahead_out.isdigit() and int(ahead_out) > 0
        if not porcelain and not ahead:
            return  # nothing to sync
        if porcelain:
            host = socket.gethostname()
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            _git("commit", "-m", f"wizzard: {host} {stamp} ({reason})")
        _git("push", timeout=60)  # offline -> fails quietly; the commit waits for next time
        _state["last_push"] = time.time()


def commit_and_push(reason: str = "exit") -> None:
    try:
        _do_push(reason)
    except Exception:
        pass


def _interval_loop(minutes: float) -> None:
    while not _stop.wait(minutes * 60):
        commit_and_push("interval")


def status() -> dict:
    """Read-only snapshot for a future status pill / endpoint."""
    return dict(_state)


# ---- lifecycle (called by the launchers) -----------------------------------

def on_start(interval_minutes: float = 10.0) -> None:
    """Pull latest + acquire the lock before the app serves. Never raises."""
    global _stop
    try:
        _state["enabled"] = enabled()
        if not _state["enabled"]:
            return
        if not acquire_lock():
            _state["enabled"] = False
            print("  [sync] another Outreach Wizzard instance is running on this machine; "
                  "sync disabled for this window.")
            return
        # Pull BEFORE writing .gitignore/.gitattributes: on a device's first launch the
        # remote already carries those files, and creating them untracked first would make
        # the pull abort ("untracked files would be overwritten"). If a prior conflict is
        # still unresolved, don't pull again — stay paused until the user clears it.
        if CONFLICT_FILE.exists():
            _state["conflict"] = True
            res = "conflict"
        else:
            res = pull()
        _ensure_scaffolding()
        note = {"ok": "pulled latest", "skipped": "remote unavailable, using local",
                "conflict": "CONFLICT -- auto-push paused, see SYNC_CONFLICT.md"}.get(res, res)
        print(f"  [sync] git sync active ({note}).")
        if interval_minutes and not _state["conflict"]:
            _stop = threading.Event()
            threading.Thread(target=_interval_loop, args=(interval_minutes,),
                             daemon=True).start()
    except Exception as e:  # pragma: no cover
        print(f"  [sync] disabled (init error: {e})")
        _state["enabled"] = False


def on_exit() -> None:
    """Commit + push, then release the lock, on shutdown. Never raises. Idempotent."""
    try:
        _stop.set()
        commit_and_push("exit")
    finally:
        release_lock()
