# Research notes — automated follow-up cadence

## Canonical architecture patterns (what mature/OSS tools do)

### Salesforce Sales Engagement (the reference model)
- Objects: Cadence -> Steps (typed) -> Tracker (per-target runtime state).
- Step types include SendAnEmail (MANUAL), AutoSendAnEmail (automatic), Wait
  (delay, WaitTimeInSeconds), CreateTask (manual action with instructions), Branch.
- A **Wait step** is a first-class primitive: it just encodes a delay before the next step.
- The **Tracker** records where each target is + history of completed steps + a
  ScheduledResumeDateTime (when a paused/waiting cadence resumes).
- Reps work a **Work Queue** that surfaces the *next due action per contact* — exactly
  the "another tab, sorted by due" UX the user wants.
- Manual email step = drafted for you, you review and send. This is the intended model:
  human-in-the-loop approval, not auto-send.

### OpenOutreach (self-hosted, Python/Django, 1.9k stars) — closest OSS analogue
- Persistent `Task` model drives a continuous queue; **each task type self-schedules
  its follow-on work** (re-arms the next follow-up after a send). Fully resumable from
  a local DB. This is the durable, event-driven cadence backbone.
- Per-lead **state machine**: QUALIFIED -> READY_TO_CONNECT -> PENDING -> CONNECTED ->
  COMPLETED. State transitions are the audit trail.
- Follow-up is a distinct task type that reads history and composes the next message.

### Warmbly (OSS, Apache-2.0) — the exact UX described
- "A positive reply spins up a typed **follow-up task on the deal, with a due date**,
  so the next step never slips."
- "Follow-ups **reply on the previous subject**, so they land in the same conversation."
- Every send/stage-change is tagged with its **sequence step** + which mailbox sent it.

### coldflow / Email-automation (PaulleDemon) / cold-cli
- Confirm the simple pattern: single-step sequence + "schedule a short follow-up N days
  later" as an explicit, rule-driven scheduled item. cold-cli: after each send, rebalance
  so future follow-ups chain from the *actual send time* (not enrol time).

## Timing / content defaults (2026 data-backed consensus)
- First follow-up: **3 days** after the original (2–5 day window; longer for execs/C-suite,
  shorter for fast startups). Next-day follow-ups REDUCE replies ~11%.
- Cadence: **3-7-7** => Day 3, Day 10, Day 17 (captures ~93% of replies).
- Volume: **2–3 follow-ups max.** 4+ total emails triples spam/unsubscribe. -> cap steps.
- ~42% of replies come from follow-ups (58% from first email) — the feature is worth it.
- Content rules for a follow-up: (1) re-anchor briefly, (2) add ONE new piece of value not
  in the original, (3) single easy CTA, (4) **never resend/"just checking in"**, (5) <80 words.
- **Same thread / same subject** ("Re: ...") — do not start a new subject line.
- Chain the delay from the *actual approval/send time* of the prior touch.

## Design implications for Paris Outreach (human-in-the-loop, offline .eml)
1. Model a follow-up as a first-class record with a **state machine** + a **due_at**
   computed from the parent's approval time + a per-step delay (default 3 days).
2. **Trigger on approval** (event-driven enrolment), mirroring OpenOutreach's self-scheduling.
3. Surface in a dedicated tab = the **Work Queue**, sorted oldest-first (most overdue on top).
4. Reuse the EXISTING draft->approve->stage(.eml) machinery for the manual follow-up — do not
   build a parallel send path. This matches "the same approve and send architecture".
5. Follow-up compose gets extra context: the ORIGINAL email body + research, plus a
   follow-up-specific brief (re-anchor, one new angle, single CTA, <80 words, same subject).
6. Cap the number of steps (default: allow 1 follow-up; configurable up to ~3).
7. Same subject line with "Re:" prefix to preserve the thread.
