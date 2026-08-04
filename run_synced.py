#!/usr/bin/env python3
"""Cross-platform launcher for Outreach Wizz-ard (Desktop App + Auto Git Sync).

Performs:
1. Public Code Repo Sync: `git pull` before launching, and auto-commit/push on exit if code changed.
2. Private Data Repo Sync: Checks `DATA_DIR` and pulls before launching (app/sync.py handles runtime & exit sync).
3. Desktop App Launch: Executes main.py in desktop window mode.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def find_git() -> str | None:
    """Find the git executable across Windows, macOS, and Linux."""
    git = shutil.which("git")
    if git:
        return git
    if sys.platform == "win32":
        paths = [
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files\Git\bin\git.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Git\cmd\git.exe"),
        ]
        for p in paths:
            if os.path.exists(p):
                return p
    elif sys.platform == "darwin":
        paths = ["/usr/bin/git", "/usr/local/bin/git", "/opt/homebrew/bin/git"]
        for p in paths:
            if os.path.exists(p):
                return p
    return None


def run_git(git_exe: str, args: list[str], cwd: Path, silent: bool = False,
            timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a git command without throwing unhandled exceptions.

    `timeout` guards the network-facing calls (pull/push): GIT_TERMINAL_PROMPT=0 stops git
    blocking on a credential prompt, but a half-open TCP connection can still hang forever,
    which would strand the launcher before the app ever starts.
    """
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(
            [git_exe] + args,
            cwd=str(cwd),
            env=env,
            capture_output=silent,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        if not silent:
            print(f"[sync] Git timed out after {timeout:g}s ({' '.join(args)}).")
        return subprocess.CompletedProcess(args, 124, "", "timed out")
    except Exception as e:
        if not silent:
            print(f"[sync] Git error ({' '.join(args)}): {e}")
        return subprocess.CompletedProcess(args, 1, "", str(e))


def main() -> int:
    project_dir = Path(__file__).resolve().parent
    git_exe = find_git()

    # Try to import settings to locate private DATA_DIR
    data_dir = None
    try:
        if str(project_dir) not in sys.path:
            sys.path.insert(0, str(project_dir))
        from app import settings as S
        data_dir = S.DATA_DIR
    except Exception:
        pass

    print("=" * 60)
    print(" 🚀 Launching Outreach Wizz-ard (Desktop App + Auto Git Sync)")
    print("=" * 60)

    if git_exe:
        # 1. Pre-launch sync: Public Code Repo
        print("\n[sync] Checking public code repository for updates...")
        if (project_dir / ".git").exists():
            # --rebase --autostash mirrors app/sync.py: keeps history linear for a single-user
            # repo and survives launching with uncommitted edits. A bare `git pull` aborts on
            # local modifications and, since git 2.34, fails outright on divergent branches.
            res = run_git(git_exe, ["pull", "--rebase", "--autostash"], project_dir, timeout=60)
            if res.returncode != 0:
                git_dir = project_dir / ".git"
                if (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists():
                    # A genuine divergence stalled the rebase. Roll back so the tree stays
                    # usable, but say so loudly -- this is not the benign offline case.
                    run_git(git_exe, ["rebase", "--abort"], project_dir, silent=True)
                    print("!" * 60)
                    print("[sync] CODE PULL CONFLICTED with the remote and was rolled back.")
                    print("[sync] Your local work is intact, but this machine is now diverged.")
                    print("[sync] Reconcile before you keep working:")
                    print(f'[sync]   git -C "{project_dir}" pull --rebase')
                    print("!" * 60)
                else:
                    print("[sync] Warning: Could not pull public code updates (offline or no upstream). Continuing...")
        else:
            print("[sync] Notice: Project directory is not a git repository.")

        # 2. Pre-launch sync: Private Data Repo
        if data_dir and (data_dir / ".git").exists():
            print(f"\n[sync] Checking private data repository ({data_dir})...")
            res = run_git(git_exe, ["pull", "--rebase"], data_dir)
            if res.returncode != 0:
                print("[sync] Warning: Could not pull private data updates. Continuing...")
    else:
        print("\n[sync] Git executable not found in PATH. Skipping pre-launch sync.")

    print("\n[app] Starting Outreach Wizz-ard Desktop Application...")
    print("-" * 60)

    # 3. Launch Desktop App (main.py)
    app_script = project_dir / "main.py"
    exit_code = 0
    try:
        res = subprocess.run([sys.executable, str(app_script)] + sys.argv[1:], cwd=str(project_dir))
        exit_code = res.returncode
    except KeyboardInterrupt:
        print("\n[app] Session interrupted by user.")
    except Exception as e:
        print(f"\n[app] Error running application: {e}")
        exit_code = 1

    print("-" * 60)
    print("[app] Desktop application closed.")

    # 4. Post-exit sync & auto-commit
    if git_exe and (project_dir / ".git").exists():
        print("\n[sync] Checking for code changes in public repository...")
        status_res = run_git(git_exe, ["status", "--porcelain"], project_dir, silent=True)
        dirty = status_res.returncode == 0 and bool((status_res.stdout or "").strip())

        if dirty:
            print("[sync] Detected modified code files from this session. Auto-committing...")
            # `add -A` stages new, modified AND deleted paths anywhere in the tree, including
            # new files at the repo root. The previous `add -u` + subdir allowlist missed both.
            # .gitignore -- not the allowlist -- is what keeps engine/config_local.py and
            # app/seed_voices_local/ out of this repo, and it still does.
            run_git(git_exe, ["add", "-A"], project_dir)
            commit_res = run_git(git_exe, ["commit", "-m", "Auto-commit: Outreach Wizz-ard session updates"], project_dir)
            if commit_res.returncode != 0:
                print("[sync] Warning: nothing was committed (see git output above).")

        # Push is driven by "is the branch ahead of its upstream", NOT by "did this session
        # dirty the tree". A clean tree with unpushed commits is the normal aftermath of a
        # manual commit or a push that failed earlier, and it must still reach the remote.
        ahead_res = run_git(git_exe, ["rev-list", "--count", "@{u}..HEAD"], project_dir, silent=True)
        ahead_out = (ahead_res.stdout or "").strip()
        no_upstream = ahead_res.returncode != 0
        ahead = int(ahead_out) if (not no_upstream and ahead_out.isdigit()) else 0

        if ahead > 0:
            print(f"[sync] {ahead} local commit(s) not on remote. Pushing to GitHub...")
            push_res = run_git(git_exe, ["push"], project_dir, timeout=120)
            if push_res.returncode == 0:
                print("[sync] ✔ Successfully pushed code changes to remote.")
            else:
                # Silence here is how work goes missing between devices. Be impossible to miss.
                still = run_git(git_exe, ["rev-list", "--count", "@{u}..HEAD"], project_dir, silent=True)
                count = (still.stdout or "").strip() or str(ahead)
                print("!" * 60)
                print(f"[sync] PUSH FAILED -- {count} commit(s) exist ONLY on this machine.")
                print("[sync] No other device will see this work. Resolve before switching machines:")
                print(f'[sync]   git -C "{project_dir}" push')
                print("!" * 60)
        elif no_upstream:
            print("[sync] Notice: no upstream branch configured; nothing pushed.")
            print(f'[sync]   git -C "{project_dir}" push -u origin HEAD   # to set one')
        else:
            print("[sync] ✔ Working tree clean and remote already up to date.")
    
    print("\n[sync] Session complete. Goodbye!")
    print("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
