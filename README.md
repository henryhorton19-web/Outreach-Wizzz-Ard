# Outreach Wizz-ard

A self-contained desktop app that drafts cold outreach emails for any target role, company, industry, or candidate profile. It generalizes job search outreach with customizable candidate profiles, editable voices, grounded research, and automated follow-ups.

## Quick Start & AI Agent Rules (System-Agnostic Launchers)

When opening, modifying, or launching Outreach Wizz-ard in a fresh chat or workspace, **always use the system-agnostic synced launcher command** for your operating system. These launchers automatically perform pre-launch git pulling (both public code and private data), start the native desktop GUI app (`pywebview`), and automatically commit & push any modified code on exit.

### Windows (PowerShell)
```powershell
cd "<path-to-paris-outreach>"
.\run-wizzard.ps1
```

### macOS / Linux (Bash/Zsh)
```bash
cd "<path-to-paris-outreach>"
./run-wizzard.sh
```

**AI Assistants & Collaborator Note**: Always launch the full desktop app GUI (`run-wizzard.ps1` / `run-wizzard.sh` -> `main.py`). Do not start only the headless web backend (`server.py` / `uvicorn`) unless explicitly asked. The launcher scripts handle the **Two-Repo Model**: public source code in `paris-outreach`, and private runtime data/voices in `%APPDATA%\OutreachWizzard` (or `~/.outreach_wizzard`). Environment variables use the `WIZZARD_*` prefix (with backward-compatible `PARIS_*` fallbacks).

## How it works

Two-stage pipeline, split by web access — provenance is the product:

1. **Research (grounded).** One web-grounded model call per target returns a single JSON object
   that validates against `engine/schema.json`: two sourced **proof points** about the target, one
   recent **trigger** (the "why now"), the **contact**, and routing signals (`role_exists`,
   `company_size`, `work_mode`, `working_language`). Cached per target so edits and re-runs never
   re-search.

2. **Compose (no web).** One call writes the whole email, driven entirely by the chosen **voice**.
   Fixed blocks are token substitution; AI blocks are written by the model from their guidance, each
   scoped to only the facts it needs; the call returns the blocks as JSON, assembled in the voice's
   order. The only fixed rules are the honesty floor: no invented numbers/names (everything must
   trace to the target's research or your profile), HPE stays present tense, and no sign-off. Dashes
   and whether Sciences Po is named are per-voice knobs.

**The tie.** The engine picks which parts of your profile answer this target and offers them to the
voice (fundraising target → HPE/Solano; AI target → BlueFire/HPE; ops or early-team → ownership
evidence, with Innova gated on an ownership/ops signal). A voice can steer this with prefer / pin /
exclude / weights and set how many experiences to tie.

**Voices are editable data, not static templates.** Three voices ship, auto-matched to each target
by situation (`role_exists` × `company_size`):

| | Small company | Large company |
|---|---|---|
| **Role exists** | `role_small` | `role_large` |
| **No role** | `no_role_small` (create the seat) | out of scope |

But nothing about them is fixed. On first run the three are **seeded once** into your voice store as
ordinary records (`app/seed_voices/*.json`), and from then on every voice — the three that ship and
any you create — is the same fully editable, deletable thing. There are no read-only presets and no
"reset to default." Open **Voices** in the header to edit, duplicate, or delete any voice, set which
situations auto-select it, pick a **default voice** (the fallback when a situation has no match),
force one voice for a whole session, or leave it on Auto.

A voice is the whole recipe. It owns an ordered list of **blocks** (each **Fixed text** or **Written
by AI**, with a length and an optional flag), a structured **style** (formality / warmth /
directness sliders plus sentence length, hedging, humour, focus, proof density) with freeform notes
and gold examples, **evidence** preferences (which experiences to prefer / pin / exclude, weights,
and how many to tie, plus custom facts), its own word-count range, custom **variables**, and the two
floor knobs. Blocks and guidance use **tokens**: research tokens like `{company}` and `{recent}`; a
token per experience (`{hpe}`, `{bluefire}`, `{solano}`, `{innova}`, `{bright_blue}`) that expands
to that experience's line; and `{relevant}`, which lets the model drop in whichever experience best
fits the point. Your approved edits are stored per voice (the edit ledger) and fed into the next
compose prompt, so drafts drift toward how you write. After composition an advisory guard checks the
honesty floor (voice-aware word count, dashes only if disallowed, Sciences Po once if the voice names
it); nothing is blocked.

## Review loop

Queue → draft → review/edit/approve → sent. Open any draft to see the research and the tied
profile evidence with source links. The **body is yours** and stored verbatim (never re-written
after you touch it). You can restore the original, compare, or redraft under any voice (or Auto) in
one click.

**Outcomes (Triage).** After approving, each send is tracked in the Triage worklist. The inbox
sweep can mark replies and bounces automatically, but you can also mark any send *replied / bounced
/ no-response* by hand (or reset a false positive) — a hand-mark fires the same effects as the
sweep. On a bounce (auto or manual), a re-draft is staged automatically to the next most likely
address, escalating to a **different person** at the target once the first person's addresses are
spent, re-addressed to them. Nothing sends: retries are approvable drafts. See
`MANUAL_OUTCOMES_BUILD.md`.

## Security

Localhost bind only, a per-launch session token on every `/api/*` request, a Host-header check
(anti DNS-rebinding), and no CORS. API keys live in the OS keychain (via `keyring`), never on disk
or in a draft/audit record. Attachment uploads are restricted by size (max 15MB) and type (`.pdf`, `.doc`, `.docx`, `.png`, `.jpg`, `.jpeg`), and are sanitized with path-traversal guards to prevent directory traversal.

## Configuration

Settings (gear icon) or environment variables (`PARIS_PROVIDER`, `PARIS_GEMINI_MODEL`,
`PARIS_COMPOSE_MODEL`, `PARIS_PORT`, `PARIS_DATA_DIR`, ...). Model choices match the HPE app:
research on Gemini 2.5 Flash, compose on 2.5 Pro; Claude is the optional provider. Model IDs are
defaults — verify against the provider docs and change them in Settings if they've moved.

Data lives in `~/.paris_outreach` (or `%APPDATA%/ParisOutreach` on Windows): caches, drafts,
archive, audit records, custom voices, the staged `.eml` outbox (`outbox/`), and uploaded
attachments (under `attachments/`). Note that embedding attachments in `.eml` files increases disk
usage for each staged draft by the size of the attached file.

**Cross-platform.** The app runs the same on macOS, Windows, and Linux — no Outlook/COM dependency.
Approving a draft writes an `.eml` to the outbox and opens it with the OS default mail app
(`open` on macOS, the registered `.eml` handler on Windows, `xdg-open` on Linux). The outbox
defaults to `outbox/` under the data dir; set **eml_dir** in Settings (or `PARIS_EML_DIR`) to route
staged drafts to a findable/synced folder instead. That path is per-machine, so a config synced
between machines stays portable.

## Your profile

Your CV facts live in `engine/config.py` as `CANDIDATE_PROFILE` — the **only** source of facts
about you that can enter an email. Edit it there to update roles, anchors, or the allowed-number
whitelist. HPE is marked ongoing (present tense); Sciences Po is stated as starting in September.

## Tests

```bash
cd paris_app
PARIS_PROVIDER=stub python -m pytest tests/ -q
```

Covers the engine's provenance/timeline/word-count/tie guarantees and the app-layer pipeline
(voice CRUD, bootstrap-once seeding, situation routing + default fallback, the custom
draft path, frame-block + Sciences-Po guards, disqualifier stop, verbatim edit, ingest).

## Layout

```
paris_app/
  main.py            desktop launcher (pywebview) + headless fallback
  run_local.py       dev launcher (browser)
  build.spec         PyInstaller bundle
  requirements.txt
  app/               orchestration + services (research, compose, assemble, validate,
                     pipeline, send, tracker, store, keys, settings, server)
    providers/       gemini / anthropic / stub  (the only code that calls a model or the web)
    seed_voices/     the three starter voices (bootstrap payload, seeded once into your store)
  engine/            deterministic core (never calls a model): draft_engine.py, config.py
                     (CANDIDATE_PROFILE; no voice content), schema.json, the two briefs
  ui/                index.html, app.js, styles.css  (single-page workbench)
  tests/             engine + app-layer tests
```
