> Historical build note ? kept for provenance, not maintained.

# Continuous Voice Improvement — Implementation Plan

**Feature:** voices that improve automatically from your manual edits — comparing every approved/sent
email against its original machine draft and updating the voice so future drafts drift toward how
you actually write, without your input.

**Scope of this doc:** grounded in (a) the actual Paris Outreach codebase, (b) open-source prompt-
optimization work (GEPA/DSPy, PRELUDE/CIPHER), and (c) shipping "learns-your-voice" email tools
(Fyxer, ForthWrite, NewMail/Nova). It is a build plan, not a rewrite: ~90% reuses machinery you
already have.

---

## 0. The one decision to make first

You are asking for something that runs directly against a **hard, thrice-stated invariant** in your
own codebase:

> "approve-first everywhere (nothing sends, auto-advances, deletes, **or rewrites voice content on
> its own**)" — `OUTCOME_AWARE_BUILD.md`, echoed in `MANUAL_OUTCOMES_BUILD.md`.

So the real decision is *how* voices are allowed to change themselves. The recommendation is **not**
to abolish the invariant but to make voice mutation a *first-class, versioned, reversible* operation
and expose a mode switch:

| Mode | Behaviour | Who this is for |
|---|---|---|
| `off` | today's behaviour, byte-for-byte | default / distrust |
| `suggest` | learning runs, proposes a voice patch, you accept/reject in the Voices UI (one click) | early adoption, calibration |
| `auto` | learning applies the patch automatically — **but** versioned, gated on evidence, bounded per cycle, one-click rollback | what you asked for |

`auto` gives you "improve without my input." Versioning + gating + bounded mutation are what stop it
from being reckless — and reversibility is *exactly* the pattern your draft loop already uses
("restore the original, compare"). You are extending that guarantee from the draft to the voice.

**Do not fine-tune.** You call Gemini/Claude over an API; you have no weights to update, and a
self-contained desktop app can't host a training run. The research consensus (below) is that for
this problem, prompt-level preference *inference* matches or beats fine-tuning, and is interpretable
and editable besides. The API-appropriate analogue of "train on my preference pairs" is
**prompt/example/parameter optimisation**, which is what this plan does.

---

## 1. What you already have (audit)

Your app is unusually close to this feature already. There are three working layers of a learning
loop; only the top one is missing.

**Layer 1 — Evidence (`app/voice_stats.py`).** Folds `sent_items.json` per voice into reply/bounce
rates with Wilson 95% intervals, per-situation buckets, min-n gating, and an `edit_intensity`
proxy. The module docstring literally calls itself "the learning loop's evidence layer."

**Layer 2 — Adaptive few-shot (`app/edit_ledger.py`).** At approve time (`server.py:_approve_rows`,
the `edit_ledger.record_edit(...)` call ~line 1077) it captures a body-level before/after pair per
voice and injects the most recent *k* pairs into the compose prompt as "RECENT REVISIONS BY THE
SENIORS." `_extract_edited_body` already isolates body-only edits and skips frame edits as too
ambiguous to attribute. This is in-context learning from edits — the raw form of what you want.

**Layer 3 — Routing bandit (`app/pipeline.py:resolve_voice`).** A stats-aware tiebreak that uses
reply rates to pick between eligible voices (gated on min-n + separated Wilson intervals; never
overrides an explicit choice). Outcomes already steer *which* voice runs.

**The gap (Layer 4, missing).** Nothing turns the accumulated edits/outcomes into changes to a
voice's *structured content* — the style sliders, `style.notes`, `style.examples`, per-block
`guidance`, or `evidence` prefs in `CustomVoice` (`app/models.py`). A voice only changes when you
open the editor and change it by hand. Layer 2 injects raw pairs but never *distils* them; the
signal evaporates after `k` edits (the ledger is trimmed to 50 and only the last 4 are used).

**Why the gap is cheap to close — the key architectural fact.** In your design *the voice **is** the
prompt.* `compose.build_voice_system()` compiles the voice into the system prompt: it already reads
`voice.style` (sliders → directives via `_compile_style`), `voice.style.notes`, `voice.evidence.
identity_note`, and `voice.style.examples`. So "update the voice" and "update the prompt" are the
same write. **The read path already consumes everything the learner would produce — the learning
loop only has to *write* into the voice record.** In a system with a hardcoded prompt this feature
is a large refactor; in yours it is an additive module.

**The signal is already on disk.** Per approved row you store `machine_email`/`machine_body` (the
original draft) and `final_email`/`edited_body` (the approved text, stored verbatim), plus
`SentItem.approved_body` and `SentItem.reply_state`. The bundled test data confirms it: `acme2`
(`role_small`) is an `edited` row whose 652-char machine draft was cut to 53 chars — a real
(draft → approved) pair ready to learn from.

---

## 2. Prior art this is grounded in

**PRELUDE / CIPHER — Gao et al., NeurIPS 2024, "Aligning LLM Agents by Learning Latent Preference
from User Edits."** The closest analogue to your problem. An agent observes a user's edits across
tasks (incl. composing emails) and *infers an interpretable, editable textual description of the
user's preference*, then conditions future generations on it — **explicitly optimising to minimise
future editing effort.** No fine-tuning; pure prompt-based GPT-4. Their winning recipe:
*retrieve context-specific examples* **+** *infer the preference in a separate step before
generating.* This validates the entire shape of Layer 4: distil edits into a preference description
(your `style.notes`), keep a few retrieved exemplars (your `style.examples`), and score yourself on
edit effort.

**GEPA — Agrawal et al., ICLR 2026 (oral); `github.com/gepa-ai/gepa`, `dspy.GEPA`.** A gradient-free
optimiser that uses *natural-language reflection* over execution traces + feedback to iteratively
mutate prompts, keeping model weights fixed. Maintains a Pareto pool of candidate prompts; a
reflection model reads a rollout's trace + feedback and proposes a revised prompt; it beats RL
(GRPO) by up to ~20% while using ~35× fewer rollouts, and often improves in a *single* reflective
update. This is the paradigm for your reflection step (Section 4.3) and, later, for offline batch
optimisation (Phase C). Decagon reports running GEPA in production for a voice/CX classifier.

**"Learning to Rewrite Prompts for Personalized Text Generation" — Li et al., WWW 2024.** A learned
prompt-rewriter beats hand-combining prompt pieces; notably, for *email* it learned a single
reusable "writing-style" description that improved outputs. Reinforces: distil one compact style
description rather than piling raw history.

**Shipping products that do exactly this:**
- **Fyxer** — learns your voice from your sent folder; goal is "review and send instead of edit and
  rewrite"; reports 55% of drafts sent unchanged among power users. The right north-star metric is
  *unedited-approval rate*, not a vibe.
- **ForthWrite** — publishes its loop: RAG over sent mail with MMR re-ranking, **edit-distance
  scoring**, and a **deterministic phrasing miner**. Direct engineering analogue: edit distance as
  the score (your PRELUDE "editing effort"), plus mining recurring phrasings you add/remove.
- **NewMail/Nova, Superhuman/Shortwave Ghostwriter** — one-time (or ongoing) analysis of sent
  emails to build a voice; nothing sends without approval. Confirms the human-in-the-loop framing.
- **Grammarly** — "teach it how you sound," but *can't save per-situation styles.* Your per-voice ×
  situation model is already more expressive than the market leader here; don't lose that.

**Consensus these converge on (your design rules):**
1. Learn an **interpretable, editable** style description, not opaque weights.
2. Objective = **minimise editing effort** (measurable via edit distance).
3. **2–5 exemplars** are enough for strong style imitation — promote a few, don't hoard.
4. **Bounded, incremental** mutation per cycle (GEPA), never wholesale rewrites.
5. **Gate on evidence** and **surface current state** to the user for reflection.

---

## 3. Architecture

```
                approve hook (server.py:_approve_rows)   ← already fires here
                          │  captures (machine → final) + sent_id + voice
                          ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  app/edit_ledger.py  (extend)                                      │
   │  • body-level pair  (exists)                                       │
   │  • + block-level diff, edit-distance score, sent_id link (new)     │
   └─────────────────────────────────────────────────────────────────┘
                          │  triggered every N approvals for a voice, or on demand
                          ▼
   ┌─────────────────────────────────────────────────────────────────┐
   │  app/voice_learning.py  (NEW — the missing Layer 4)                │
   │  1 gather  : recent (draft, approved, outcome, Δ) triples/voice    │
   │  2 weight  : reply↑↑  bounce=EXCLUDE  awaiting=neutral ; Δ = effort │
   │  3 reflect : ONE structured LLM call → JSON voice-patch            │
   │  4 validate: patch bounded + passes honesty floor + CustomVoice ok │
   │  5 apply   : version current voice → write patch → save            │
   └─────────────────────────────────────────────────────────────────┘
                          │                              │
             suggest mode │                              │ auto mode
                          ▼                              ▼
        Voices UI: "proposed patch,          store.save_voice_version(prev)
        accept / reject / edit"              store.save_custom_voice(patched)
                          │                              │
                          └───────────────┬──────────────┘
                                          ▼
   compose.build_voice_system()  ← UNCHANGED. Already reads style/notes/examples.
                                          │
                          (optional Phase C) promotion arbitrated by
                          the reply-rate bandit you already have.
```

### 3.1 The learning cycle

A "cycle" for a voice fires when it accumulates `voice_learning_min_edits` new edits since its last
cycle (default 5, aligned with the "2–5 exemplars" finding), or on demand from the UI ("Learn from
my edits now"). Cheap enough to also offer a manual button so you're never waiting on a threshold.

### 3.2 The signal model

For each approved row belonging to a voice, build a triple:

- **draft** = `machine_email` (or block-level `machine` parts if you store them; see 5.1)
- **approved** = `final_email`
- **outcome** = `SentItem.reply_state` for that send
- **effort** = normalised token/char edit distance between draft and approved (Levenshtein or a
  token diff; this is ForthWrite's "edit-distance scoring" and PRELUDE's objective)

**Weighting (mirror `voice_stats` semantics so the two layers agree):**

- `replied` → strong positive. The approved text *worked*; its deltas are high-value supervision.
- `bounced` / `bounced_exhausted` → **exclude entirely.** A bounce is a dead address, not a comment
  on the writing — `voice_stats` already excludes bounces from the reply denominator; do the same
  here or you'll learn from noise.
- `awaiting` → neutral/mild. Still a real human edit, so keep it, just don't over-weight vs. a reply.
- **effort** modulates *what* you learn: a near-zero edit means the draft was already right
  (reinforce the current voice — a weak positive example); a large edit means learn the delta
  (what got cut, added, softened, tightened).

Block-level beats whole-email: your composer already works per block and can store `machine` parts.
Diffing per block lets the learner attribute "you always cut the opening" vs. "you soften the close"
to the right block's `guidance`, instead of one blurry body diff. `_extract_edited_body` already
does the body-isolation half of this.

### 3.3 The reflection call (the heart of it)

One structured call per cycle (stub-backed offline, like the rest of the app). The prompt =
**the current voice** (compiled style + notes + examples) **+ the weighted triples** →
returns a **JSON voice-patch**. This is GEPA's "reflect on traces + feedback, propose a revision"
and CIPHER's "infer the preference in a separate step," specialised to your `CustomVoice` schema.

Patch schema (validated before apply):

```jsonc
{
  "style_deltas": {                 // bounded: at most ±1 per slider per cycle
    "directness": +1,               // e.g. edits consistently strip hedging
    "warmth": 0
  },
  "sentence_length": "short",       // optional categorical nudge
  "notes_add":   ["Cut the throat-clearing opener; lead with the specific hook."],
  "notes_remove":["Offer a read of their business as a hypothesis."],   // if contradicted by edits
  "promote_examples": ["<approved body that got a reply>"],  // ≤2, prefer replied+low-effort
  "block_guidance": { "close": "Keep it to one low-friction ask; no double questions." },
  "evidence": { "prefer_add": [], "exclude_add": [] },       // rare; only on a strong pattern
  "rationale": "3–5 edits removed the opening sentence; 2 replied. Directness raised, opener note dropped.",
  "evidence_strength": { "n_edits": 5, "n_replied": 2, "mean_effort": 0.34 }
}
```

Design constraints baked into the prompt (per Section 2 rules):
- **Bounded** deltas (±1 slider, ≤2 notes each way, ≤2 example swaps) → incremental, not a rewrite.
- **Justify from ≥2 independent edits.** One weird edit must not move the voice (PRELUDE's separate-
  inference step exists precisely to avoid over-fitting a single sample). Encode as: `notes_add`/
  slider deltas require the rationale to cite ≥2 rows; else drop them.
- **Never touch the honesty floor.** The learner may not add examples/notes that invent numbers,
  drop the Sciences-Po-once rule, flip present-tense firm, etc. Run the patch's new examples through
  the same advisory guard `validate.py`/compose already apply; reject offending examples.
- Prefer **replied, low-effort** bodies as promoted exemplars (they're the proof the style lands).

### 3.4 Apply, version, gate

- **Version first, always.** `store.save_voice_version(voice)` snapshots the current voice to
  `DATA_DIR/voice_history/{id}/{ts}.json` before any write. Rollback = copy a snapshot back over the
  live voice. This is the safety net that makes `auto` sane and mirrors your draft-level "restore
  original."
- **Gate `auto` on evidence:** don't auto-apply unless `n_edits ≥ voice_learning_min_edits` **and**
  (optionally) the voice has reply data clearing `voice_stats_min_n`. Below that, downgrade `auto`
  to `suggest` for that voice. (Honest-by-default, same spirit as `voice_stats` refusing a naked %.)
- **Example rotation:** cap `style.examples` (e.g. 5). When promoting, evict the oldest/weakest so
  the prompt stays lean (the "2–5 exemplars" ceiling; also keeps compose token cost flat).
- **Cooldown:** one applied cycle per voice per window (e.g. 24h or per session) so a burst of edits
  in one sitting doesn't thrash the voice.

### 3.5 (Phase C) Promotion via the bandit you already have

The elegant end-state: when a voice mutates, keep the prior version as a *challenger* under a
derived id, let both be selectable, and let `resolve_voice`'s existing reply-rate bandit arbitrate
which wins over time. Your **evidence layer becomes the fitness function for your learning layer** —
which is exactly GEPA's Pareto-pool idea, implemented with infrastructure you've already shipped. No
new statistics; reuse Wilson + min-n. This is optional and later; Phases A/B deliver the feature
without it.

---

## 4. Concrete implementation

### 4.1 New / changed files

- **`app/voice_learning.py`** (NEW). Owns gather → weight → reflect → validate → apply. Follows the
  house pattern of `edit_ledger`/`voice_stats`: app-layer only, **every function swallows its own
  errors** (learning must never break an approve or a draft), stub-backed offline path for tests.
- **`app/store.py`** (extend). Add `save_voice_version(voice)`, `list_voice_versions(id)`,
  `get_voice_version(id, ts)`, `restore_voice_version(id, ts)` under `VOICES_DIR`-sibling
  `voice_history/{id}/`. Reuse `safe_write_text` and the existing JSON-canonical pattern.
- **`app/edit_ledger.py`** (extend, back-compatible). Store richer records: add `sent_id`,
  per-block diffs, and an `effort` score alongside today's `before`/`after`. Keep old readers
  working (additive keys only). Add `triples_for_learning(voice, since_ts)` returning the weighted
  triples for the reflection call.
- **`app/server.py`** (extend). New endpoints (below) + call `voice_learning.maybe_run(voice_id)`
  at the tail of `_approve_rows` inside the existing `try/except` that already guards the CRM hook.
- **`app/settings.py`** (extend) + the `/api/settings` allowlist (currently ~line 285): add
  `voice_learning_mode` (`off|suggest|auto`, default `off`), `voice_learning_min_edits` (default 5),
  reuse `voice_stats_min_n`, optional `voice_learning_reflection_model`.
- **`ui/`** (extend). In the existing Voices editor: a "Learning" panel per voice showing mode,
  pending proposal (diff view: sliders before/after, notes added/removed, examples promoted, with
  the rationale + evidence_strength), Accept / Reject / Edit-then-accept, a version history list with
  Rollback, and a "Learn from my edits now" button. Reuse the existing token palette and the
  drawer's compare pattern.

### 4.2 Endpoints

```
GET    /api/voices/{id}/proposals              # pending patch(es) for suggest mode
POST   /api/voices/{id}/proposals/{pid}/apply  # accept (also used by "edit-then-accept")
POST   /api/voices/{id}/proposals/{pid}/reject
GET    /api/voices/{id}/history                # list versions
POST   /api/voices/{id}/rollback  {ts}         # restore a snapshot
POST   /api/voices/{id}/learn                  # run a cycle now (manual trigger)
```

All behind the existing session-token + Host-header middleware; `#`-in-id already handled by the
frontend's `encodeURIComponent` (see the manual-outcomes tests).

### 4.3 Compose path

**No change.** `build_voice_system()` already reads `style` (sliders → `_compile_style`),
`style.notes`, `evidence.identity_note`, and `style.examples`, then appends the edit-ledger block.
The learner writes into those fields; drafts start reflecting the learned voice on the very next
compose. Keep Layer 2's raw-pair injection too — it's the short-horizon memory; Layer 4 is the
long-horizon distillation. (Optionally, once a rule is distilled into `notes`, drop the pairs that
produced it from the injected window to avoid double-counting.)

### 4.4 Cost & performance

One reflection call per voice per cycle. At a 5-edit cadence that's ~1 extra call per 5 approvals per
voice — negligible; price it through `app/cost.py` like every other call. Use a cheaper "helper"
model (you already have `helper_model`/`compose_thinking_level` knobs) since reflection over ~5 short
diffs doesn't need your top compose model. Everything is offline-safe: a `stub` provider path returns
a deterministic no-op/trivial patch so `PARIS_PROVIDER=stub` tests stay hermetic.

### 4.5 Tests (mirror `test_outcome_aware.py` / `test_manual_outcomes.py` style, all offline)

- Signal: bounce excluded from learning; replied up-weighted; effort computed; block attribution.
- Reflection→patch parsing + schema validation; malformed JSON tolerated (like `_parse_blocks`).
- Guardrails: ±1 slider clamp; ≤2 notes/examples; single-edit patch rejected; honesty-floor-violating
  example rejected; example cap + rotation.
- Versioning: snapshot on apply; rollback restores byte-identical; history ordering.
- Modes: `off` = byte-for-byte today (the sacred "off = today" invariant); `suggest` never writes the
  live voice; `auto` writes + versions + respects cooldown/min-n.
- E2E over the real server (session-token auth) for the six endpoints.

---

## 5. Phasing (ship value early)

**Phase A — Suggest (1 module + UI panel, low risk, high signal).** Enrich edit capture (block diff +
outcome join + effort), add `voice_learning.py` with one reflection call, surface proposals in the
Voices editor with Accept/Reject. No versioning-critical path, no auto-write. You immediately see
what the loop *would* do and can calibrate the reflection prompt against your own judgement. This
alone likely captures most of the value (it's how you'll discover the loop is trustworthy).

**Phase B — Auto + versioning (the literal ask).** Add `save_voice_version`/rollback, flip the mode
switch to allow `auto`, wire the gates (min-edits, cooldown, honesty-floor check on examples). Now
voices improve "without your input," but every change is snapshotted and one-click reversible.

**Phase C — Bandit promotion / offline GEPA (optional, when data is thick).** Challenger voices
arbitrated by the existing reply-rate bandit; and/or an offline batch job that runs `dspy.GEPA` /
`gepa-ai/gepa` over your accumulated (draft, approved, reply) corpus to optimise a voice's `notes`/
`guidance` against an edit-distance-and-reply metric, emitting a patch through the same apply path.
This is where you graduate from per-cycle nudges to real search — but only once you have enough
approvals per voice for it to matter.

---

## 6. Risks and mitigations

- **Drift / degradation** (the thing your invariant guards against). Mitigate with bounded per-cycle
  deltas, evidence gating, cooldown, versioning + rollback, and a visible "current voice state" panel
  (the survey's "surface state for user reflection"). Keep the seed voices in `seed_voices/` as an
  immutable factory-reset floor.
- **Over-fitting to one odd edit.** Require ≥2 independent edits to justify any slider/notes change
  (PRELUDE's separate-inference rationale). Effort-weighting also naturally discounts single outliers.
- **Learned examples smuggling in honesty-floor violations.** Run every promoted example and note
  through the existing advisory guard; reject rather than store. The floor stays code, never learned.
- **Feedback loop / mode collapse** (voice narrows toward one template, kills variety). Cap examples
  and rotate; let the bandit (Phase C) punish a voice whose reply rate drops after a mutation.
- **Sparse outcomes today.** Your inbox is off, so `reply_state` is `awaiting` everywhere for now —
  the loop still works on edits alone (edits are the primary signal; replies are a *bonus* weight).
  When you connect the inbox/sweep, the reply weighting turns on with zero code change.
- **Privacy.** Same posture as the rest of the app: everything local under `~/.paris_outreach`, no
  new network surface beyond the existing provider call. Voice history is just more local JSON.

---

## 7. Reading list

- Gao et al., *Aligning LLM Agents by Learning Latent Preference from User Edits* (PRELUDE/CIPHER),
  NeurIPS 2024 — the exact problem; infer an editable preference description, minimise edit effort.
- Agrawal et al., *GEPA: Reflective Prompt Evolution Can Outperform RL*, ICLR 2026 — the reflection
  paradigm; `github.com/gepa-ai/gepa`, `dspy.GEPA`.
- Li et al., *Learning to Rewrite Prompts for Personalized Text Generation*, WWW 2024 — distil one
  reusable style description; it helped email specifically.
- DSPy docs — `dspy.GEPA`, `BootstrapFewShot` (the principled version of your edit-ledger few-shot).
- ForthWrite engineering write-up — edit-distance scoring + phrasing miner + RAG over sent mail.
- Fyxer / NewMail(Nova) — product framing: learn from sent mail, "review and send not edit,"
  unedited-approval rate as the metric, nothing sends without approval.

---

*Bottom line: you've already built the evidence layer, the adaptive few-shot layer, and the routing
bandit. This feature is the fourth layer — distil edits into structured voice updates — and because
the voice **is** the prompt in your design, it's an additive module plus a versioning safety net, not
a rewrite. Ship Phase A behind a `suggest` switch, watch it for a week, then flip `auto`.*
