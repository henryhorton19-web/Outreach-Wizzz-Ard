> Historical build note ? kept for provenance, not maintained.

# Outcome-Aware Outreach — Build Summary

This implements the full Master Implementation Plan (Phases 0–7) plus the Frontend/UX spec, on top
of the existing Paris Outreach app. It is additive and opt-in: with the inbox disabled, learning
routing off, and no suppressions, the app behaves byte-for-byte as before ("off = today").

## The single seam
Everything hangs off the `Message-ID` that `apollo._build_eml` already generated and discarded.
It is now captured at the approve hook (`_approve_rows`) into a `SentItem` — the join point for
reply detection, bounce handling, pipeline stage, suppression, cost, and per-voice stats.

## What was added

### Models (`app/models.py`)
- `SentItem` (one row per approved send), `ReplyState`, `AddressCandidate`.
- `FollowUp.origin_message_id`; `TargetState` cost fields.

### Modules
- `apollo.py` — `_build_eml`/`open_email_draft` now return the Message-ID (kept even when the OS
  mail handler fails to launch); `rank_address_candidates` builds the ranked address ladder
  (Apollo-verified > research email > pattern permutations).
- `store.py` — `sent_items.json`, `suppressions.json`, `snippets.json`, `session_stats.json` CRUD;
  scope-aware CSV/XLSX export.
- `cost.py` — prices token usage from `GenResult` per a settings price table; per-session +
  per-draft accumulation.
- `pipeline_view.py` — pure 6-column stage mapping + board assembly.
- `voice_stats.py` — per-voice fold with Wilson intervals; bounces excluded from the reply
  denominator; min-n gating.
- `suppression.py` — Gmail-normalizing do-not-contact list, risky-generic guard, archive-aware
  dedup.
- `detect.py` — pure reply/bounce classification (In-Reply-To/References; DSN hard/soft; auto-reply
  filtering; Message-ID collision tie-break).
- `inbox.py` — read-only IMAP with a defence-in-depth mutation guard and stdlib fallback.
- `sweep.py` — applies detection effects: pause cadence on reply, record stats, auto-suppress
  bounces, stage (never send) a bounce re-draft.
- `pipeline.py` — `draft_retarget` (bounce re-draft, approve-first) + a stats-aware bandit tiebreak
  in `resolve_voice` (gated on min-n + separated Wilson intervals; never overrides an explicit
  choice).

### Endpoints (`app/server.py`)
`/api/export`(+count), `/api/cost`(+reset), `/api/pipeline`(+mark), `/api/voice_stats`,
`/api/suppressions`(CRUD+clear), `/api/snippets`(CRUD), `/api/inbox/test`, `/api/inbox/sweep`,
`/api/triage`, `/api/send_window`. SentItem creation + suppression/dedup checks in
`_approve_rows`/`_ingest_to_queue`. The `/api/settings` allowlist was extended with every new field.

### Frontend (`ui/`)
- Topbar refactor: left tab strip (Workspace · Follow-ups · Pipeline · Performance · Triage,
  `role=tablist`, arrow-key nav, badges) + right utility cluster (Sent · Voices · Settings · Guide)
  + a cost meter.
- New views: Pipeline board, Voice Performance table (never a bare %), Triage worklist.
- Drawer: why-voice line, staleness chips (dot + word), per-target cost, Insert-snippet popover.
- Settings sections: inbox (progressive disclosure + Test connection), learning routing, min-n /
  quiet-day thresholds, send-window toggle, do-not-contact manager.
- Keyboard layer (j/k/a/e/`/`/?) + cheat-sheet. All CSS derived from the existing token palette.

## Invariants held
Read-only mailbox (a guard refuses every mutation verb); approve-first everywhere (nothing sends,
auto-advances, deletes, or rewrites voice content on its own); every new subsystem swallows its own
errors so a sweep/stat/cost failure never breaks an approve or draft; every new setting is
opt-in and allowlisted; off = today.

## Tests
`tests/test_outcome_aware.py` (25) + `tests/test_smoke_e2e.py` (end-to-end) cover detection,
sweep effects, Wilson math, suppression/dedup, the ladder, pipeline mapping, the read-only IMAP
guard, provider-backed bounce retry, and the Phase-7 routing gates. All offline (canned RFC822
fixtures + a fake mailbox + the stub provider). **Full suite: 103 passing.**
