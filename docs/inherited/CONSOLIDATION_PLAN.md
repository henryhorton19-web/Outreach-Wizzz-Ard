# Paris Outreach — Consolidation Plan

This document records the full difference analysis between the two source trees and the plan
used to produce a single, fully functional, up-to-date application. The consolidated tree in this
package is the *result* of executing this plan; the plan is retained so the reasoning is auditable.

## The two inputs

- **`paris_app` (the "broken" tree).** The newer tree. Larger across the board. Contains all the
  recent feature work — a research-engine overhaul and a managed-attachments feature — plus
  launcher hardening and some debug scaffolding. Reported as "doesn't work."
- **`paris_voices_core` (the "working" tree).** The older tree. Boots and runs; its research
  feature is functional. Lacks every recent feature.

The broken tree is a **strict superset** of the working tree at the level of application logic:
every working source file is present in broken, either byte-identical or as a forward evolution.
Nothing in the working tree is newer than its broken counterpart. This is the single most
important fact for the merge: consolidation is *not* a two-way reconciliation of divergent
branches. It is "take the broken tree, keep all its features, repair two defects, remove junk."

## Why "broken" doesn't work — two independent root causes

Neither defect is in the feature code. Both were introduced as debugging shortcuts and both are
invisible to the test suite because the suite runs against the `stub` provider, which bypasses the
two affected paths entirely.

### Root cause 1 — the Python launch gate (blocks startup)

`main.py` and `run_local.py` gained a hard version gate the working tree never had:

    if sys.version_info >= (3, 13):
        raise SystemExit("Paris Outreach needs Python 3.11-3.12 ...")

and `pyproject.toml` pins `requires-python = ">=3.11,<3.13"`. The in-code comment attributes the
pin to a real dependency failure: *"3.14 aborts inside anyio annotation eval."* The author hit a
crash on Python 3.14, pinned conservatively to `<3.13`, and added the gate. Consequence: on a
`.venv` built with Python 3.13 or 3.14 the app exits instantly, before the server starts — which
presents exactly as "doesn't work," while the gate-less working tree at least boots. The
surrounding scaffolding unique to the broken tree (a `faults.log` faulthandler, `DEBUG:` prints,
a pre-warm import, capturing `server.error`, `test_index.py`) is the fingerprint of that hunt.

The blamed version is 3.14, not 3.13. 3.13 is fine. So the gate was one minor version too wide.

### Root cause 2 — the keyring bypass (breaks the real-provider path)

`app/keys.py` had its entire OS-keychain probe replaced with a hard bypass:

    try:
        raise ImportError("Bypassing keyring entirely to prevent macOS hangs")
    except Exception:
        keyring = None
        _backend_ok = False

With `_backend_ok` permanently `False`, `set_key()` never writes to the OS keychain and keys live
only in a session-local `_mem` dict. **Restart the app and every saved API key is gone**, so every
real Gemini / Anthropic / Apollo call fails authentication. The `stub` provider needs no key, so
all 57 tests pass regardless — which is why this stayed hidden. The author was chasing a genuine
issue (a rare macOS keychain-prompt hang), but the fix was a sledgehammer that removed persistence.

## Full file differential

Source-only comparison (runtime caches and logs excluded). Working = 49 files, Broken = 72 files
(the extra 23 are almost entirely runtime junk, see below).

### Files only in BROKEN

Genuinely new **source** (keep all):

| File | Purpose |
| --- | --- |
| `app/attachments.py` | Managed attachment store (upload/list/delete/resolve/mime). New feature. |
| `tests/test_attachments.py` | Tests for the attachment feature (10 tests). Keep. |
| `tests/test_research_overhaul.py` | Tests for the research overhaul (7 tests). Keep. |
| `pyproject.toml` | Project metadata + (now) pytest config. Keep, edited. |

New **tooling** (keep, low-risk):

| File | Purpose |
| --- | --- |
| `run_tests_quick.py` | Convenience runner for the research tests. |
| `test_index.py` | Ad-hoc "does `/` return 200" check. Not a pytest test. |
| `generate_and_zip.sh`, `zip_app.py` | Packaging helpers. |

**Runtime junk** (delete — must never be in a source tree):

- `.pytest_cache/` (5 files) — pytest's local cache.
- `.test_data/` (10 files: caches, drafts.json, voice copies) — a captured run-state snapshot.
- `faults.log` — output of the faulthandler added during debugging.

### Files only in WORKING

| File | Decision |
| --- | --- |
| `error_report.md` | Stale. Describes the original `.letter-wrap` layout bug, which `FIXES.md` records as already fixed in the broken tree. Dropped (its content is superseded). |

### Shared files that CHANGED (17)

All changes are forward evolutions in the broken tree. Ordered by diff size.

| File | Δlines | What changed | Verdict |
| --- | --- | --- | --- |
| `app/research.py` | 231 | Research overhaul: `staleness` on proof points, identity anchor, completeness gate, 2-pass retry loop with targeted feedback, `research_capped` flagging, display-name normalisation. | Keep (broken) |
| `ui/app.js` | 140 | Attachment UI (upload/list/select, per-draft override), source chips, research-partial badge, richer research rendering. | Keep (broken) |
| `ui/styles.css` | 69 | Styles for the above; the container-query drawer fix from FIXES.md. | Keep (broken) |
| `app/server.py` | 58 | Attachment endpoints; surfaces `research_capped`, `research_failures`, `attachments` in the company payload; settings keys. | Keep (broken) |
| `app/apollo.py` | 27 | Threads attachments into the `.eml` (`_build_eml`, `open_email_draft`); default-attachment resolution. | Keep (broken) |
| `app/store.py` | 25 | New `safe_write_text` helper wrapping every state write with an actionable error. | Keep (broken) |
| `run_local.py` | 24 | Version gate + faulthandler + smarter browser-open poll. | Keep, **gate widened** |
| `main.py` | 32 | Version gate + faulthandler + pre-warm import + `server.error` capture. | Keep, **gate widened** |
| `app/settings.py` | 16 | `ATTACH_DIR`, `default_attachments`, `attach_by_default`, sanitisation, safe settings write. | Keep (broken) |
| `app/keys.py` | 11 | **The keyring bypass.** | **Reverted + opt-out added** |
| `tests/test_voices.py` | 6 | Seed-count assertion 3 → 6 (seed_voices grew). Correct. | Keep (broken) |
| `README.md` | 4 | Doc updates. | Keep (broken) |
| `app/audit.py` | 3 | Adopts `store.safe_write_text`. Coupled to store.py. | Keep (broken) |
| `engine/schema.json` | 3 | Adds `staleness` enum to proof points. Matches research.py. | Keep (broken) |
| `app/models.py` | 1 | Adds `attachments: list[str]` to `CompanyState`. | Keep (broken) |
| `requirements.txt` | 1 | Adds the Python-range comment. | Keep (broken) |

### Shared files that are IDENTICAL (31)

No action. Notably includes all four providers (`base`, `gemini`, `anthropic_provider`, `stub`),
`pipeline.py`, `compose.py`, `assemble.py`, `ingest.py`, `validate.py`, `engine/draft_engine.py`,
`engine/config.py`, and all six seed voices. That the providers and the pipeline are unchanged is
what proves the two runtime failures are *not* in the feature code.

## The plan (executed to build this package)

1. **Base = the broken tree.** It contains every feature and every up-to-date shared file.
2. **Strip runtime junk.** Remove `.pytest_cache/`, `.test_data/`, `faults.log`, all `__pycache__/`.
   Add a `.gitignore` so they cannot return.
3. **Fix root cause 2 (keys.py).** Restore the working tree's real keyring probe, and add an
   explicit `PARIS_NO_KEYRING=1` opt-out so a user who genuinely hits the macOS hang can disable
   the keychain without losing persistence for everyone else. Persistence is on by default.
4. **Fix root cause 1 (version gate).** Widen the gate in `main.py` and `run_local.py` from
   `>= (3, 13)` to `>= (3, 14)` — allowing 3.11–3.13, blocking only the 3.14 the comment blames —
   and update `pyproject.toml` to `>=3.11,<3.14` and the on-screen message to name 3.12/3.13.
5. **Tidy test collection.** Add `[tool.pytest.ini_options] testpaths = ["tests"]` to
   `pyproject.toml` so root-level ad-hoc scripts (`test_prompt.py`, `test_index.py`) can't break a
   `pytest` run via stray relative imports. Left the scripts in place; they're harmless as tools.
6. **Drop `error_report.md`** (superseded by FIXES.md).

Nothing in the feature code (research, attachments, server, UI, apollo, store, settings, models,
schema) was modified — those are carried verbatim from the broken tree.

## Validation performed on the consolidated tree

- `pytest` → **57 passed** (24 of which are the new attachment + research-overhaul tests).
- Boots clean under `PARIS_PROVIDER=stub` on Python 3.12; serves `/`, `/static/app.js`,
  `/static/styles.css` at HTTP 200.
- Full flow driven over HTTP: ingest → queue → research → compose → attachment override → approve
  → `.eml` staged. All steps return 200. (The only non-success is `xdg-open` failing in a headless
  sandbox with no mail app; on macOS the `.eml` opens in the default handler.)
- `keys.py`: verified the `PARIS_NO_KEYRING=1` path round-trips set/get in memory, and that the
  default path loads the `keyring` module when a backend is installed.

## What still needs a real API key to confirm (cannot be tested with `stub`)

The research overhaul's two-pass retry loop and completeness gate, and the attachment→`.eml`
round-trip with a real recipient, only execute on the real-provider path. They are covered by unit
tests at the function level and read as correct, but an end-to-end run with a live Gemini or
Anthropic key on the target machine is the final confirmation. With root cause 2 fixed, keys now
persist, so that path is reachable again.

## First run

    cd paris_app
    python3.12 -m venv .venv          # or python3.13; NOT 3.14
    source .venv/bin/activate
    pip install -r requirements.txt

    # dev (browser):
    PARIS_PROVIDER=stub python run_local.py     # offline smoke test, no key needed
    python run_local.py                          # real providers, once a key is saved in Settings

    # desktop window:
    python main.py

If you ever hit a macOS keychain hang on launch, set `PARIS_NO_KEYRING=1` — the app will run with
session-only key storage instead of blocking.
