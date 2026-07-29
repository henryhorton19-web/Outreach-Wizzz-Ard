"""Regression cover for run_synced.py's git sync.

The launcher had no test coverage at all, which is how three sync bugs shipped together.
These tests build a throwaway bare remote + clone in tmp_path and drive the real
run_synced.py against it, so they assert on observable git state rather than on source text.

The bug they exist to prevent: push used to be nested inside `if <tree is dirty>`, so a
clean tree with unpushed commits was silently skipped and local commits piled up forever.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "run_synced.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _env(home: Path) -> dict[str, str]:
    """Isolate git from the developer's real identity, hooks and global config."""
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "HOME": str(home),
        "USERPROFILE": str(home),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }


def _git(repo: Path, *args: str, env: dict[str, str]) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stderr}"
    return p.stdout.strip()


@pytest.fixture()
def repo(tmp_path: Path):
    """A clone wired to a bare remote, with the real launcher and a stub app in place."""
    home, remote, work = tmp_path / "home", tmp_path / "remote.git", tmp_path / "work"
    home.mkdir()
    env = _env(home)

    subprocess.run(["git", "init", "--bare", "-q", str(remote)],
                   check=True, env=env, timeout=60)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)],
                   check=True, env=env, timeout=60)

    (work / "app").mkdir()
    (work / "app" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (work / "ui").mkdir()
    (work / "ui" / "app.js").write_text("// ui\n", encoding="utf-8")
    # Same private-path ignores as the real repo: .gitignore is the only thing keeping
    # these out, so the tests below prove `add -A` does not defeat it.
    (work / ".gitignore").write_text(
        "faults.log\n.test_data/\nengine/config_local.py\napp/seed_voices_local/\n",
        encoding="utf-8")
    shutil.copy(LAUNCHER, work / "run_synced.py")
    # Stand in for the desktop app: runs, changes nothing, exits 0.
    (work / "main.py").write_text("print('stub app')\n", encoding="utf-8")

    _git(work, "remote", "add", "origin", str(remote), env=env)
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-qm", "baseline", env=env)
    _git(work, "push", "-qu", "origin", "main", env=env)
    return work, env


def _launch(work: Path, env: dict[str, str]) -> str:
    p = subprocess.run([sys.executable, str(work / "run_synced.py")],
                       cwd=str(work), capture_output=True, text=True,
                       env=env, timeout=180)
    return p.stdout + p.stderr


def _ahead(work: Path, env: dict[str, str]) -> int:
    return int(_git(work, "rev-list", "--count", "@{u}..HEAD", env=env))


def test_pushes_unpushed_commits_when_tree_is_clean(repo):
    """The original bug. A commit made outside the launcher must still get pushed."""
    work, env = repo
    (work / "FIRST_TIME_SETUP.md").write_text("# setup\n", encoding="utf-8")
    _git(work, "add", "FIRST_TIME_SETUP.md", env=env)
    _git(work, "commit", "-qm", "manual commit", env=env)

    assert _ahead(work, env) == 1
    assert _git(work, "status", "--porcelain", env=env) == "", "precondition: clean tree"

    _launch(work, env)

    assert _ahead(work, env) == 0, "clean tree with unpushed commits was not pushed"


def test_new_root_level_file_is_committed(repo):
    """`add -u` plus a subdir allowlist missed new files at the repo root."""
    work, env = repo
    (work / "NOTES.md").write_text("# notes\n", encoding="utf-8")

    _launch(work, env)

    assert _git(work, "ls-files", "NOTES.md", env=env) == "NOTES.md"
    assert _ahead(work, env) == 0


def test_new_file_in_ui_is_committed(repo):
    """The old allowlist was app/engine/tests/web -- `web` does not exist, `ui` was omitted."""
    work, env = repo
    (work / "ui" / "panel.js").write_text("// panel\n", encoding="utf-8")

    _launch(work, env)

    assert _git(work, "ls-files", "ui/panel.js", env=env) == "ui/panel.js"


def test_gitignored_private_files_are_never_committed(repo):
    """`add -A` must not defeat the privacy guarantee -- .gitignore is what enforces it."""
    work, env = repo
    (work / "engine").mkdir(exist_ok=True)
    (work / "engine" / "config_local.py").write_text("NAME = 'real name'\n", encoding="utf-8")
    (work / "app" / "seed_voices_local").mkdir(parents=True, exist_ok=True)
    (work / "app" / "seed_voices_local" / "v.json").write_text("{}", encoding="utf-8")
    (work / "faults.log").write_text("trace\n", encoding="utf-8")
    (work / "TRIGGER.md").write_text("# forces a commit\n", encoding="utf-8")

    _launch(work, env)

    tracked = _git(work, "ls-files", env=env).splitlines()
    assert "engine/config_local.py" not in tracked
    assert "app/seed_voices_local/v.json" not in tracked
    assert "faults.log" not in tracked
    assert "TRIGGER.md" in tracked, "sanity: the commit did happen"


def test_clean_and_up_to_date_is_a_no_op(repo):
    """Guard against over-correcting the fix into creating empty commits.

    Unlike the tests above, this one passes on the pre-fix launcher too -- that is the
    point. It pins the behaviour that was already correct so the fix cannot regress it.
    Assertions are behavioural on purpose: matching console text would also match the
    "Already up to date." that `git pull` prints on its own.
    """
    work, env = repo
    before = _git(work, "rev-parse", "HEAD", env=env)
    n_before = int(_git(work, "rev-list", "--count", "HEAD", env=env))

    _launch(work, env)

    assert _git(work, "rev-parse", "HEAD", env=env) == before
    assert int(_git(work, "rev-list", "--count", "HEAD", env=env)) == n_before
    assert _ahead(work, env) == 0


def test_push_failure_is_reported_loudly(repo, tmp_path):
    """A failed push must not be a one-line aside -- that is how work goes missing."""
    work, env = repo
    (work / "WORK.md").write_text("# important\n", encoding="utf-8")
    (tmp_path / "remote.git").rename(tmp_path / "remote.git.gone")
    try:
        out = _launch(work, env)
    finally:
        (tmp_path / "remote.git.gone").rename(tmp_path / "remote.git")

    assert "PUSH FAILED" in out
    assert _ahead(work, env) >= 1, "the commit should still exist locally"
