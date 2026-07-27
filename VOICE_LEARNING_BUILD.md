# Continuous voice learning — build notes (Layer 4 + Phase C)

Drafts now learn from the edits you make before sending. The app already had three layers of a
learning loop — evidence (`voice_stats`, reply rates + Wilson intervals), adaptive few-shot
(`edit_ledger`, which injects raw before/after pairs into compose), and a routing bandit
(`pipeline.resolve_voice`). This adds the missing fourth layer: distilling your edits into
**structured, versioned changes to the voice itself** — the sliders, `style.notes`,
`style.examples`, and per-block `guidance` that `compose.build_voice_system` already compiles into
the prompt. Because *the voice is the prompt*, writing the voice updates the prompt with no change
to the compose path.

## How it works

1. **Signal.** On approve, `edit_ledger.record_edit` now also stores the normalised edit distance
   (`effort`, via stdlib `difflib`) and the `sent_id`, linking each edit to its outcome.
2. **Gather.** `voice_learning.gather` joins edits to their `SentItem` outcome and weights them:
   replied ×2, awaiting ×1, **bounced excluded** (a dead address is not a comment on the writing —
   mirrors `voice_stats`).
3. **Reflect.** One small model call (helper model, or a deterministic offline heuristic under the
   stub) returns a JSON voice-patch, justified from ≥2 independent edits so one odd edit can't move
   it (cf. PRELUDE/CIPHER, GEPA).
4. **Clamp + floor.** `clamp_patch` enforces ±1 per slider within [0,4], validates categoricals,
   caps notes (≤2 each way) and examples, and runs every promoted example through an honesty-floor
   lint (`example_is_clean`) using the voice's own knobs (dashes, Sciences-Po-once, length).
5. **Apply.** `apply_patch` snapshots the voice first (aborts if it can't — never mutate something
   it can't roll back), then writes.

## Modes (Settings → “Learn how you write from your edits”)

- **off** — today's behaviour, byte-for-byte.
- **suggest** — stores a proposal; accept/dismiss it in the voice editor's *Learning* panel.
- **auto** — applies automatically, **versioned** (one-click rollback), bounded per cycle, gated on
  `voice_learning_min_edits` and `voice_learning_cooldown_hours`.

## Phase C — A/B before it wins

With **A/B** on (`voice_learning_promote`), an auto change is not applied to the proven voice.
Instead `spawn_challenger` clones the voice with the change under the same situations; the existing
reply-rate bandit routes some live sends to it (`resolve_voice` now includes challengers, which stay
hidden from the editor). `arbitrate` promotes the challenger into the champion when its Wilson
interval separates above, retires it if it clearly loses, else keeps testing — the same separation
test as `_learned_pick`. `voice_optimize.optimize` is the offline batch analogue (GEPA-flavoured:
several candidate patches scored on a held-out split of your edits, best spawned as a challenger;
points to `dspy.GEPA` for the heavyweight version).

## Files

- `app/voice_learning.py` — new. Layer 4 core: gather/reflect/clamp/apply, proposals, `maybe_run`
  (the approve-time hook), and Phase C challenger spawn/arbitrate.
- `app/voice_optimize.py` — new. Offline batch optimiser.
- `app/edit_ledger.py` — `edit_effort`, effort+`sent_id` capture, `triples_for_learning`.
- `app/store.py` — voice version snapshot/list/get/restore; `list_custom_voices(include_challengers=)`.
- `app/models.py` — `CustomVoice.origin`, `challenger_of`, `learning_meta` (additive).
- `app/settings.py` — `voice_learning_mode`, `_min_edits`, `_max_examples`, `_cooldown_hours`,
  `_promote`, `_reflection_model`; `VOICE_HISTORY_DIR`.
- `app/pipeline.py` — challengers made routable in `resolve_voice`.
- `app/server.py` — approve hook + endpoints: `GET/POST /api/voices/{id}/learning|learn`,
  `.../proposals/{pid}/apply|reject`, `.../history`, `.../rollback`, `.../optimize`,
  `POST /api/voices/arbitrate`.
- `ui/` — Settings controls + a *Learning* panel in the voice editor (status, accept/dismiss,
  history + rollback, A/B).
- `tests/test_voice_learning.py` — 21 tests. Full suite: **148 passing**.

## Safety

Everything is off by default. Every learning function swallows its own errors — learning can never
break an approve or a draft. Auto never changes a voice without a snapshot. The honesty floor is
enforced on anything learned. Bounces are excluded from the signal. Nothing sends.
