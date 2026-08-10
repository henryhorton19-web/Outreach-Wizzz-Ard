> Historical build note ? kept for provenance, not maintained.

# Manual Outcome Control + Person-Aware Bounce Re-Targeting — Build Summary

Two capabilities layered onto the outcome-aware app, additive and opt-in. With no `contacts_alt`
in a cache and no manual marks, the app behaves byte-for-byte as before ("off = today").

1. **Manual detection.** Mark any approved send *replied / bounced / no-response / awaiting* by
   hand. A hand-mark fires **exactly the same effects** the automated IMAP sweep fires — because
   both now call one shared module (`app/outcomes.py`) instead of the sweep owning private helpers.
2. **Person-aware bounce escalation.** On a bounce (auto **or** manual), the auto-staged re-draft
   walks the address ladder to the next most likely address and, once a person's known/likely
   addresses are spent, escalates to a **different person** at the target — re-addressed to them.

## The single effect path
`app/outcomes.py` is the one place a send's outcome transitions and its side effects fire:

- `set_outcome(sent_id, outcome, *, provider, source)` — the reversible setter. Unlike
  `voice_stats.record_reply/bounce` (which gate on `awaiting` and so cannot correct a mis-detection),
  it writes `reply_state` directly, so a false-positive sweep result can be reset. `voice_stats`
  folds live over `reply_state`, so there is no counter to keep in sync.
- `pause_followup_for`, `retarget_after_bounce`, `mark_exhausted` — moved verbatim from `sweep.py`.
  `sweep.run` now imports and delegates to them, so "manual == automatic" is structural.
- `_lift_bounce_suppression` — on a reset from bounced, removes **only** a suppression that a bounce
  added (`reason == "bounced"`); a manually-added do-not-contact entry is never touched.

Approve-first is preserved throughout: a bounce (however marked) *stages* a re-draft; nothing sends,
auto-advances, or deletes.

## Person-aware ladder (`apollo.rank_address_candidates`)
`AddressCandidate` gained `person_name`, `person_title`, `tier` (`primary_person | alt_person`).
The ladder is now built in two passes so the escalation to a different person is actually reachable
within the retry budget (a naive person-then-formats order buried the alternate behind ~15
format×TLD guesses of the primary):

1. **Known addresses, by person** — primary (Apollo → research) then each alternate's known email.
   A *named different person's* address outranks low-confidence pattern-guesses of the primary.
2. **Pattern permutations** — primary person's, then alternates'.

With no `contacts_alt`, output is identical to before aside from the always-present person fields
(defaulting to the primary). Alternates come from research (see below); Apollo stays enrichment-only;
the manual retarget dialog is the backstop when the ladder has no known alternate.

## Re-addressing on escalation (`pipeline.draft_retarget`)
`draft_retarget(..., new_person=None)`: when the next rung belongs to a different person, the
**working copy** of the cache's `contact` is overridden (name/title/email) before `de.prepare`, so
`compose.derive_tokens` re-addresses the greeting/opener/"To:" to that person. The override is never
persisted (`save_cache` is not called), so the parent cache other sends reuse stays canonical. The
voice is deliberately reused — routing (`role_exists × company_size`) is a company-level signal, so
switching contact never silently swaps the voice.

## Research alternates (`engine/schema.json`, `engine/enrichment_brief.md`, `app/research.py`)
The single grounded research call now also names up to two backup contacts (`contacts_alt`,
different people, from the brief's existing fallback hierarchy) at ~zero extra cost — "name them from
what you already found, no extra searches." `_sanitize_cache` coerces the list, drops any entry equal
to the primary, and caps at two. Optional and back-compatible (`additionalProperties: true`); old
caches simply have no alternates and degrade to today's single-person ladder. The stub cache ships an
alternate so the offline demo (`PARIS_PROVIDER=stub`) exercises the escalation.

## Endpoints (`app/server.py`)
- `POST /api/sent/{id}/outcome {outcome}` — hand-mark replied/bounced/no_response/reopen/awaiting.
  Only a bounce needs a provider (to stage the retry); without one it still marks + suppresses.
- `POST /api/sent/{id}/retarget {email?, name?, title?}` — stage a re-draft to a specific person
  (the backstop), or auto-pick the next rung when no body is given.
- `GET /api/triage` — gains an `awaiting` bucket (every live send is hand-classifiable, not just
  stale ones), each row carrying `outcome_source` and, for bounces, a `next_rung` preview.

## Frontend (`ui/`)
Triage is now the manual worklist: a 4th **Awaiting** bucket; a per-row outcome menu (mark replied /
bounced / no-response / reset) on every bucket; a "marked" vs "detected" source label; the next-rung
person+email preview on bounced rows; and a real **"retarget to a different person…"** dialog
replacing the old dead-end link. Settings gains a **Bounce retries** control. All styling reuses the
existing token palette.

## Settings
`max_bounce_retries` default raised to **3** (retries now span the current person's formats *and* a
different person) and relabelled accordingly.

## Invariants held
Off = today · approve-first everywhere · read-only mailbox untouched (manual marking needs no
mailbox and works with IMAP off) · every effect swallows its own errors · one shared effect path for
manual and automatic · provenance intact (alternates sourced through the same grounded call; the
re-addressing cache override never persists).

## Tests
`tests/test_manual_outcomes.py` (12, unit) + `tests/test_manual_e2e.py` (4, full HTTP lifecycle over
the real server with session-token auth) cover: manual marks + their effects, reversible reset,
bounce-only suppression lift, the reordered person-aware ladder, alt-selection reachability,
re-addressing to a different person, the sanitiser rules, and the two endpoints (incl. the `#`-in-id
URL-encoding the frontend already does via `encodeURIComponent`). Plus `tests/test_portability.py`
(4) — see below. **Full suite: 123 passing** (was 103).

## Portability (runs on any machine — macOS / Windows / Linux)
The staged-draft outbox was hardcoded to a specific Windows OneDrive path
(`C:\Users\<you>\…\Paris Outreach`), which on a Mac created a garbage folder with backslashes
in its name and broke draft opening. Fixed to the app's own design (the README already described the
`.eml` outbox as living under the data dir):

- `settings.OUTBOX_DIR = DATA_DIR/outbox` — cross-platform default (created at startup).
- `apollo._eml_dir()` resolves: `Settings.eml_dir` / `PARIS_EML_DIR` override → `OUTBOX_DIR` →
  temp dir, each guarded, so staging a draft never fails because a configured path is missing on
  this machine. No hardcoded absolute path remains.
- Draft opening was already OS-branched (`os.startfile` / `open` / `xdg-open`); the stale
  "PowerShell COM / Outlook" module docstring was corrected to match.
- A **Draft outbox folder** field in Settings lets you route staged drafts to a findable/synced
  folder per-machine (blank = the portable default).
- `tests/test_portability.py` guards it: no hardcoded path in source, default resolves under the
  data dir, override honored, unwritable override falls back, and the written `.eml` lands in the
  outbox.
