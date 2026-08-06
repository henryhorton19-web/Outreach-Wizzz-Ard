#!/usr/bin/env python3
"""Verify which of the remediation plan's 45 tasks actually landed.

(The plan is Part 2 of paris-outreach-COMPLETE.md, or EXECUTION_PLAN.md standalone.)

Run from the repository root. Needs no dependencies beyond the stdlib and does not
import the application, so it works on a partially-migrated tree.

    python3 tools/verify_plan1.py            # human-readable
    python3 tools/verify_plan1.py --json     # machine-readable, for pasting

Exit code 0 = every check passed, 1 = at least one did not.

Each check reports the OBSERVED value, not just pass/fail: "expected >=6, got 0" is
actionable in a way that "FAIL" is not.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS: list[dict] = []


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def check(task: str, label: str, ok: bool, observed, expected: str) -> None:
    RESULTS.append({"task": task, "label": label, "ok": bool(ok),
                    "observed": observed, "expected": expected})


def count(rel: str, needle: str) -> int:
    return read(rel).count(needle)


def rx(rel: str, pattern: str) -> int:
    return len(re.findall(pattern, read(rel), re.MULTILINE))


# ---------------------------------------------------------------- git context
def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def git_context() -> dict:
    return {
        "head": git("rev-parse", "--short", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "origin_main": git("rev-parse", "--short", "origin/main"),
        "commits_ahead_of_dbe3abb": git("rev-list", "--count", "dbe3abb..HEAD"),
        "recent": git("log", "--oneline", "-15"),
    }


# ------------------------------------------------------------- stage 0 patches
def check_patches() -> None:
    # 0001 — list scoping
    check("p0001", "tests/test_list_scoping.py present",
          (ROOT / "tests/test_list_scoping.py").exists(),
          (ROOT / "tests/test_list_scoping.py").exists(), "True")
    check("p0001", "_LIST_ID_RE validation in store",
          count("app/store.py", "_LIST_ID_RE") >= 1,
          count("app/store.py", "_LIST_ID_RE"), ">=1")
    check("p0001", "store.DEGRADED flag",
          count("app/store.py", "DEGRADED") >= 2,
          count("app/store.py", "DEGRADED"), ">=2")
    check("p0001", "sync refuses to commit while degraded",
          count("app/sync.py", "REFUSING to commit") == 1,
          count("app/sync.py", "REFUSING to commit"), "1")
    check("p0001", "no unscoped queue/ingest calls in app.js",
          _unscoped_queue_calls() == 0, _unscoped_queue_calls(), "0")
    check("p0001", "sourcing records added_list_id",
          count("app/sourcing/research_job.py", 'added_list_id') >= 1,
          count("app/sourcing/research_job.py", "added_list_id"), ">=1")
    # 0002 — voices
    check("p0002", "tests/test_voice_editor.py present",
          (ROOT / "tests/test_voice_editor.py").exists(),
          (ROOT / "tests/test_voice_editor.py").exists(), "True")
    check("p0002", "owns_sci_po gone from the validator",
          count("app/server.py", "b.owns_sci_po") == 0,
          count("app/server.py", "b.owns_sci_po"), "0")
    check("p0002", "generic exception handler",
          count("app/server.py", "_unhandled_handler") >= 1,
          count("app/server.py", "_unhandled_handler"), ">=1")
    bad_sits = _seed_bad_situations()
    check("p0002", "no seed voice has an invalid situation",
          not bad_sits, bad_sits or "none", "none")


def _unscoped_queue_calls() -> int:
    n = 0
    for line in read("ui/app.js").splitlines():
        if "api(" not in line:
            continue
        if not re.search(r"/api/(queue|ingest_file)\b", line):
            continue
        if "list_id" in line:
            continue
        n += 1
    return n


def _seed_bad_situations() -> list[str]:
    valid = {"no_role_small", "role_small", "role_large"}
    bad = []
    for d in ("app/seed_voices", "app/seed_followup_voices"):
        for p in sorted((ROOT / d).glob("*.json")) if (ROOT / d).exists() else []:
            try:
                sits = json.loads(p.read_text(encoding="utf-8")).get("situations") or []
            except Exception:
                bad.append(f"{p.name}: unreadable")
                continue
            unknown = [s for s in sits if s not in valid]
            if unknown:
                bad.append(f"{p.name}: {unknown}")
    return bad


# --------------------------------------------------------------- plan 1 tasks
def check_tasks() -> None:
    check("t1", "store mutation lock applied",
          count("app/store.py", "_MUTEX") >= 6, count("app/store.py", "_MUTEX"), ">=6")
    check("t1", "upsert_draft trims at DRAFTS_CAP not a literal",
          'updated_at", DRAFTS_CAP' in read("app/store.py"),
          'updated_at", DRAFTS_CAP' in read("app/store.py"), "True")

    css = read("ui/styles.css")
    check("t2", "contrast tokens replaced",
          "#D2D9E6" not in css and "#B45309" not in css,
          {"line-strong_old": "#D2D9E6" in css, "caution_old": "#B45309" in css},
          "both False")

    check("t3", "outbox default inside DATA_DIR",
          "proj_parent.parent.exists()" not in read("app/settings.py"),
          "proj_parent.parent.exists()" not in read("app/settings.py"), "True")

    gi = read(".gitignore")
    check("t4", "outbox/ and *.eml ignored",
          "outbox/" in gi and "*.eml" in gi,
          {"outbox/": "outbox/" in gi, "*.eml": "*.eml" in gi}, "both True")

    check("t5", "stale outbox doc strings gone",
          "09 Personal Projects" not in read("app/settings.py"),
          "09 Personal Projects" not in read("app/settings.py"), "True")

    html = read("ui/index.html")
    check("t6", "pipeline renders into a visible container",
          "pipelineBoard" not in html and 'id="pipelineCols"' in html
          and 'id="pipelineCols" class="board-cols hidden"' not in html,
          {"pipelineBoard": "pipelineBoard" in html,
           "pipelineCols": 'id="pipelineCols"' in html}, "False / True")

    check("t7", "tests/test_regressions.py present",
          (ROOT / "tests/test_regressions.py").exists(),
          (ROOT / "tests/test_regressions.py").exists(), "True")
    check("t8", "allow_dashes plumbed through normalize/critique",
          count("engine/draft_engine.py", "keep_dashes") >= 2
          and 'spec.get("allow_dashes"' in read("engine/draft_engine.py"),
          {"keep_dashes": count("engine/draft_engine.py", "keep_dashes"),
           "critique_reads_spec": 'spec.get("allow_dashes"' in read("engine/draft_engine.py")},
          ">=2 / True")
    check("t8", "assemble passes keep_dashes",
          "keep_dashes=" in read("app/assemble.py"),
          "keep_dashes=" in read("app/assemble.py"), "True")
    check("t9", "word-count test discriminates (110-160 not 70-120)",
          "110-160" in read("tests/test_voices.py"),
          "110-160" in read("tests/test_voices.py"), "True")
    check("t10", "hardcoded 70-120 gone from validate.py",
          count("app/validate.py", "70-120") == 0,
          count("app/validate.py", "70-120"), "0")

    check("t11", "tools/check_style_purity.py present",
          (ROOT / "tools/check_style_purity.py").exists(),
          (ROOT / "tools/check_style_purity.py").exists(), "True")
    js = read("ui/app.js")
    hexes = re.findall(r"#[0-9a-fA-F]{6}\b", js)
    check("t12", "no hex literals in app.js", not hexes, len(hexes), "0")

    for f in ("test_index.py", "test_prompt.py", "run_tests_quick.py"):
        check("t13", f"{f} deleted", not (ROOT / f).exists(),
              (ROOT / f).exists(), "False")

    check("t14", "writer_brief removed",
          count("engine/draft_engine.py", "writer_brief") == 0,
          count("engine/draft_engine.py", "writer_brief"), "0")
    check("t15", "profile modal removed",
          count("ui/index.html", "profileModal") == 0,
          count("ui/index.html", "profileModal"), "0")

    check("t16", "testserver host gated on an env flag",
          "WIZZARD_ALLOW_TESTSERVER" in read("app/server.py"),
          "WIZZARD_ALLOW_TESTSERVER" in read("app/server.py"), "True")
    check("t16", "link scheme allowlist",
          "safeHref" in js, "safeHref" in js, "True")
    check("t16", "AGENTS.md launcher names corrected",
          count("AGENTS.md", "run-paris") == 0,
          count("AGENTS.md", "run-paris"), "0")

    check("t17", "check_selectors reports orphan ids",
          "check_orphan_ids" in read("tools/check_selectors.py"),
          "check_orphan_ids" in read("tools/check_selectors.py"), "True")
    check("t18", "CI workflow present",
          (ROOT / ".github/workflows/ci.yml").exists(),
          (ROOT / ".github/workflows/ci.yml").exists(), "True")

    check("t19", "topbar tabs are labelled for a11y",
          'aria-label="Workspace"' in html and 'aria-label="Pipeline"' in html,
          {"workspace": 'aria-label="Workspace"' in html,
           "pipeline": 'aria-label="Pipeline"' in html}, "both True")
    check("t19", "46vw tab cap removed",
          "max-width: 46vw" not in css, "max-width: 46vw" in css, "False")
    check("t20", "status strip present",
          'id="statusStrip"' in html and "statusStrip" in js,
          {"html": 'id="statusStrip"' in html, "js": "statusStrip" in js},
          "both True")
    check("t20", "list options no longer duplicate the name+count",
          "l.count || 0})</option>" not in js,
          "l.count || 0})</option>" in js, "False")
    check("t21", "triage shortcuts wired",
          "handleTriageShortcut" in js, "handleTriageShortcut" in js, "True")

    check("t23", "type scale tokens declared",
          rx("ui/styles.css", r"^\s*--t-base\s*:") == 1,
          rx("ui/styles.css", r"^\s*--t-base\s*:"), "1")
    check("t23", "spacing scale tokens declared",
          rx("ui/styles.css", r"^\s*--s-3\s*:") == 1,
          rx("ui/styles.css", r"^\s*--s-3\s*:"), "1")
    check("t23", "--ok token declared",
          rx("ui/styles.css", r"^\s*--ok\s*:") == 1,
          rx("ui/styles.css", r"^\s*--ok\s*:"), "1")
    check("t23", "alias block removed",
          "--accent: var(--capital)" not in css,
          "--accent: var(--capital)" in css, "False")
    check("t24", "native dialogs",
          html.count("<dialog") >= 8, html.count("<dialog"), ">=8")

    check("t25", "no async handler without await", _async_no_await() == 0,
          _async_no_await(), "0")
    check("t26", "blind excepts reduced below half", _blind_excepts() < 78,
          _blind_excepts(), "<78 (was 154)")
    check("t27", "json_contract module present",
          (ROOT / "app/json_contract.py").exists(),
          (ROOT / "app/json_contract.py").exists(), "True")
    check("t27", "_FENCE_RE defined in exactly one file",
          _fence_files() <= 1, _fence_files(), "<=1")
    check("t28", "malformed provider fixture present",
          any(ROOT.glob("tests/**/*malformed*")),
          [str(p.relative_to(ROOT)) for p in ROOT.glob("tests/**/*malformed*")] or "none",
          ">=1")
    check("t29", "outbox backfill out of lifespan",
          "sync_historical_outbox()" not in read("app/server.py"),
          "sync_historical_outbox()" in read("app/server.py"), "False")
    check("t30", "conftest no longer needs environ.setdefault",
          count("tests/conftest.py", "os.environ.setdefault") == 0,
          count("tests/conftest.py", "os.environ.setdefault"), "0")

    check("t43", "phase labels stripped",
          _phase_labels() == 0, _phase_labels(), "0")
    # Scoped to the SentItem class body: `role_exists` already exists on
    # CompanyState, so a whole-file grep is a false positive.
    sent_body = _class_body("app/models.py", "SentItem")
    check("t45", "SentItem carries situation signals",
          "role_exists" in sent_body and "company_size" in sent_body,
          {"role_exists": "role_exists" in sent_body,
           "company_size": "company_size" in sent_body}, "both True")


def _class_body(rel: str, cls: str) -> str:
    """The source of one class, so a check cannot match a field on a sibling class."""
    m = re.search(rf"class {cls}\(BaseModel\):(.*?)(?=\nclass )", read(rel), re.DOTALL)
    return m.group(1) if m else ""


def _async_no_await() -> int:
    src = read("app/server.py")
    bad = 0
    for b in re.split(r"(?=@app\.(?:get|post|put|delete|patch)\()", src):
        if not b.startswith("@app."):
            continue
        if "async def" in b and "await" not in b:
            bad += 1
    return bad


def _blind_excepts() -> int:
    n = 0
    for d in ("app", "engine"):
        for p in (ROOT / d).rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            n += len(re.findall(r"except Exception|except\s*:", p.read_text(
                encoding="utf-8", errors="replace")))
    return n


def _fence_files() -> int:
    n = 0
    for p in (ROOT / "app").rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        if "_FENCE_RE" in p.read_text(encoding="utf-8", errors="replace"):
            n += 1
    return n


def _phase_labels() -> int:
    n = 0
    for d in ("app", "engine", "ui"):
        for p in (ROOT / d).rglob("*"):
            if p.suffix not in (".py", ".js") or "__pycache__" in str(p):
                continue
            n += len(re.findall(r"Phase [0-9][a-z0-9.]*|Layer [0-9]",
                                p.read_text(encoding="utf-8", errors="replace")))
    return n


# -------------------------------------------------------------- plan 2 premise
def check_plan2_premise() -> None:
    """The three defects plan 2 was written to fix. Plan 1 never touches these,
    so they should all still be TRUE (i.e. still broken) after phase 1."""
    has_logging = any(
        "import logging" in read(f) for f in
        ("main.py", "app/server.py", "app/store.py", "app/settings.py"))
    check("plan2.B1", "no application logging (expected still broken)",
          not has_logging, has_logging, "False")
    check("plan2.B2", "frozen build discards diagnostics (expected still broken)",
          "io.StringIO()" in read("main.py"), "io.StringIO()" in read("main.py"), "True")
    check("plan2.B3", "no version constant (expected still broken)",
          not (ROOT / "app/version.py").exists(),
          (ROOT / "app/version.py").exists(), "False")
    # plan 2 Task 6 sizes the facade at execution time rather than trusting a number
    facade = rx("app/store.py", r"^def [a-z]")
    check("plan2.T6", "store facade size (informational)", True, facade, "record it")


def main() -> int:
    check_patches()
    check_tasks()
    check_plan2_premise()
    ctx = git_context()
    failed = [r for r in RESULTS if not r["ok"]]

    if "--json" in sys.argv:
        print(json.dumps({"git": ctx, "passed": len(RESULTS) - len(failed),
                          "failed": len(failed), "results": RESULTS}, indent=2))
        return 1 if failed else 0

    print("=" * 74)
    print("PHASE 1 VERIFICATION")
    print("=" * 74)
    print(f"branch {ctx['branch']}  head {ctx['head']}  "
          f"origin/main {ctx['origin_main']}  ahead of dbe3abb: "
          f"{ctx['commits_ahead_of_dbe3abb']}  dirty: {ctx['dirty']}")
    print("-" * 74)
    for r in RESULTS:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['task']:<10} {r['label']}")
        if not r["ok"]:
            print(f"         expected {r['expected']}, got {r['observed']}")
    print("-" * 74)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        by_task = sorted({r["task"] for r in failed})
        print(f"\nINCOMPLETE OR NOT LANDED: {', '.join(by_task)}")
        print("Finish these plan-1 tasks before starting plan 2.")
    else:
        print("\nPhase 1 complete. Plan 2 Stage 0 satisfied.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
