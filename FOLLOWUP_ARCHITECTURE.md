# Automated Follow-Up — Architecture (as built)

## What was asked for
A CRM-style automated follow-up:
- a **new tab you can toggle** to,
- approving an email to a company **triggers a suggested follow-up**,
- showing **time since the original was approved**, **sorted oldest→newest**,
- using **the same approve-and-send architecture** for a **manual** follow-up,
- **reusing the editable voices system**, but with a **separate voice set** for follow-ups,
- with **defaults grounded in industry standards / elite-application precedents**.

## Research basis (see RESEARCH_NOTES.md)
- **Salesforce Sales Engagement**: typed steps (manual/auto email, Wait, task) + a per-target
  tracker with a scheduled resume time + a Work Queue surfacing the next due action. The manual
  follow-up here = a "manual email step" (drafted for you, you review and send).
- **OpenOutreach** (self-hosted Python): a persistent per-lead state machine + a task queue where
  each completed action self-schedules the next, resumable from a local store. The backbone adapted.
- **Warmbly**: "an action spins up a typed follow-up task with a due date"; "follow-ups reply on
  the previous subject" to stay in-thread. The exact UX.
- **Timing/content consensus (2026)**: first follow-up ~3 days (2–5 window), 3-7-7 cadence
  (Day 3/10/17), cap 2–3 (4+ triples spam/unsubscribe), <80 words, re-anchor + ONE new angle +
  single CTA, never "just checking in", same subject thread, delays chained from the prior touch.

## Core decisions (as implemented)

1. **A follow-up is a first-class record** (`FollowUp`), persisted in its own store
   `follow_ups.json` (mirrors drafts/archive). It is the CRM "tracker" for one pending touch and
   carries everything needed to regenerate the email later without re-searching.

2. **The follow-up EMAIL reuses the whole existing draft→approve→stage(.eml) path.** When drafted,
   it materialises as an ordinary `CompanyState` keyed by `"{parent_slug}__f{step}"`, with its
   subject forced to `"Re: {original}"`. Approval goes through the *existing*
   `/api/companies/{slug}/approve` → `_approve_rows` → `apollo_verify` machinery unchanged. That is
   what "the same approve and send architecture" means, and it is why almost no new send code exists.

3. **Follow-up voices reuse the editable voice system, as a separate set.**
   - `CustomVoice` gained a `kind` field (`"outreach"` | `"followup"`; existing voices default to
     outreach, so nothing breaks).
   - `store.list_custom_voices(kind=...)` filters by set. Outreach pickers never show follow-up
     voices; the follow-up path only sees follow-up voices.
   - `ensure_seeded()` seeds each kind **independently**, so follow-up voices appear on upgrade even
     when outreach voices already exist, and each kind self-heals if wiped.
   - The Voices editor gains an **Outreach / Follow-up segmented toggle** — same block/style/
     evidence editor, two sets. (Session/default-voice routing controls apply to outreach only.)
   - `resolve_followup_voice(cache, override)` routes a follow-up to a follow-up voice by the SAME
     situation the original used (role_large original → `fu_role_large`).
   - `pipeline.draft_followup` runs that follow-up voice through the ORDINARY block machinery
     (`compose.produce_email`) with follow-up context, so editing a follow-up voice's blocks/style/
     evidence takes effect exactly like an outreach voice.

4. **The follow-up rules live in two appropriate places** (no hardcoded bespoke composer):
   - **Invariant floor** (`compose.followup_floor_preamble`) — the analogue of the honesty floor:
     don't resend, one new angle, single CTA, well under 80 words, same thread, no filler openers,
     never claim you spoke before. Enforced regardless of which follow-up voice is used.
   - **Tunable content** — tone, which angle, phrasing, subject — lives in the editable follow-up
     voices, whose seed defaults encode the best practices below.

5. **Trigger on approval (event-driven enrolment)**, like OpenOutreach self-scheduling. One hook at
   the end of `_approve_rows`: if the approved email was itself a follow-up, close out its record;
   then `enroll_from_approval` creates the next follow-up if enabled and under the cap. `due_at`
   chains from the original's approval time via per-step delays. Zero model cost at enrolment.

6. **The Follow-ups tab is the Work Queue**: a header toggle swaps the workspace for a list sorted
   OLDEST original-approval first (most overdue on top), each row showing elapsed time + a due/
   overdue hint, with Draft / Open-in-Drafts / Dismiss actions and a header overdue badge.

7. **Configurable in Settings** (now on the /api/settings allowlist and exposed via /api/status):
   `follow_up_enabled` (default on), `follow_up_max_steps` (default 1 → one follow-up; up to 3),
   `follow_up_delay_days` (default `[3,7,7]` → the 3-7-7 cadence). Raising the cap yields a
   multi-touch sequence with no further code change; approving each touch re-enrols the next.

## Seed follow-up voices (grounded in industry standards + elite-application precedent)
Three, one per situation, each a re-anchor (fixed) + new-angle (AI) + single-ask (fixed) shape,
short by design, no Sciences-Po restatement, subject left empty so it defaults to the in-thread
`Re:` (best practice):
- `fu_role_small` — direct, warm, one new concrete reason to reply, one clear ask.
- `fu_role_large` — precise, offers an easy forward / "who owns this" routing path for a big org.
- `fu_no_role_small` — speculative: warm, low-pressure, a small tangible offer, genuinely easy to
  decline (earns the reply on curiosity, since there is no advertised role to anchor to).
The elite-application register throughout: brief, high-signal, one specific new point, a single
low-friction ask, no groveling and no filler — concision as the signal of respect for the reader.

## FollowUp state machine
    pending  -> record exists; due_at may be future; no email yet (tab shows elapsed + due/overdue)
    drafted  -> a CompanyState follow-up draft was generated and is in review in Drafts
    approved -> the follow-up .eml was staged via the normal approve path (drops out of the queue)
    dismissed-> user skipped it (drops out of the queue)
pending vs "due" is computed from due_at vs now, not stored separately.

## Files (in the consolidated tree)
- models.py — `FollowUp` + `FollowUpStatus`; `kind` on `CustomVoice`.
- followups.py (new) — enrol on approval, cadence math, oldest-first sort, elapsed/due labels, public shape.
- store.py — `follow_ups.json` CRUD; `list_custom_voices(kind=...)`.
- settings.py — 3 follow-up settings + `sanitized()`; per-kind `ensure_seeded()`.
- compose.py — `followup_floor_preamble`; `produce_email`/`compose_voice`/`mock_voice` follow-up-aware.
- pipeline.py — `resolve_followup_voice`; `draft_followup` via the block machinery.
- server.py — enrol hook in `_approve_rows`; `/api/followups[...]`; kind on `/api/voices`;
  follow-up knobs added to the `/api/settings` allowlist.
- seed_followup_voices/ (new) — fu_role_small, fu_role_large, fu_no_role_small.
- ui/ — header toggle + badge; Follow-ups view + row template; Voices kind toggle; Settings controls; styles.
- tests/test_followups.py (new) — enrolment, cap, cadence, oldest-first sort, elapsed/due, lazy
  draft, follow-up-voice routing/separation, approve-through-existing-path + re-enrol, dismiss,
  settings-persistence regression.

## Validation
- 76 tests pass (was 57 before the feature).
- Clean boot under stub; full HTTP lifecycle smoke test passes: kind-filtered voices → ingest →
  draft → approve (enrols f1) → list (elapsed/due) → draft follow-up (via fu_role_small, Re: subject)
  → approve via the same endpoint (f1→approved, f2 enrolled) → dismiss; follow-up settings persist
  and surface in /api/status.

## Out of scope for v1 (state machine leaves room for these)
- Auto-*sending* (human-in-the-loop approve is retained by design).
- Reply detection / auto-pause on reply (no inbox integration in this offline .eml app).
- Per-step follow-up voices and branching cadences.

## Real-provider caveat
The follow-up compose path (block machinery + follow-up context) is covered at the unit level via
the stub provider and validated end-to-end over HTTP, but has not been run against a live Gemini/
Anthropic key. With keys persisting again (keyring fix), that path is reachable; a live run is the
final confirmation.
