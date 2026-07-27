#!/usr/bin/env python3
"""Cross-platform launcher for Paris Outreach (Desktop App + Auto Git Sync).

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


def run_git(git_exe: str, args: list[str], cwd: Path, silent: bool = False) -> subprocess.CompletedProcess:
    """Run a git command without throwing unhandled exceptions."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(
            [git_exe] + args,
            cwd=str(cwd),
            env=env,
            capture_output=silent,
            text=True,
            check=False,
        )
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
    print(" 🚀 Launching Paris Outreach (Desktop App + Auto Git Sync)")
    print("=" * 60)

    if git_exe:
        # 1. Pre-launch sync: Public Code Repo
        print("\n[sync] Checking public code repository for updates...")
        if (project_dir / ".git").exists():
            res = run_git(git_exe, ["pull"], project_dir)
            if res.returncode != 0:
                print("[sync] Warning: Could not pull public code updates (offline or no remote configured). Continuing...")
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

    print("\n[app] Starting Paris Outreach Desktop Application...")
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
        if status_res.returncode == 0 and status_res.stdout.strip():
            print("[sync] Detected modified code files from this session. Auto-committing...")
            run_git(git_exe, ["add", "-u"], project_dir)  # Stage modified/deleted tracked files
            # Also stage any new files in app, engine, tests
            for subdir in ["app", "engine", "tests", "web"]:
                if (project_dir / subdir).exists():
                    run_git(git_exe, ["add", subdir], project_dir)
            
            commit_res = run_git(git_exe, ["commit", "-m", "Auto-commit: Paris Outreach session updates"], project_dir)
            if commit_res.returncode == 0:
                print("[sync] Pushing updates to GitHub...")
                push_res = run_git(git_exe, ["push"], project_dir)
                if push_res.returncode == 0:
                    print("[sync] ✔ Successfully pushed code changes to remote.")
                else:
                    print("[sync] Warning: Could not push code changes to remote. Committed locally.")
        else:
            print("[sync] ✔ No code changes detected. Public repo clean.")
    
    print("\n[sync] Session complete. Goodbye!")
    print("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
