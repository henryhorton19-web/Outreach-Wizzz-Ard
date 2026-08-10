# Stage 2 — Composition (voice-driven)

Composition is owned by the **voice**, not the engine. The engine's only role at compose time is to
supply facts and enforce the honesty floor; the voice decides structure, order, per-block fixed/AI
mode, style, length, which candidate experiences are drawn on, and the token vocabulary.

The whole email is written in **one** model call (see `app/compose.py::compose_voice`). Fixed blocks
are token substitution; AI blocks are written by the model from their guidance, each scoped to only
the facts it requests. The model returns a JSON object keyed by block id, which is assembled in the
voice's block order.

## The honesty floor (the only fixed instruction)

Regardless of the voice, every draft must obey:

- **No invented facts.** Every number, name, or company must trace to `allowed_facts` (the target's
  proof points and recent "why now", the selected candidate evidence, the `{relevant}` shortlist,
  any experience dropped in by a `{key}` token, and the voice's custom facts).
- **Standing experience reflects active status** — state current role in present tense if ongoing; never misrepresent standing.
- **No sign-off** — the mail client appends the signature.

One further rule is a *voice knob*, not floor: dashes (off by default, `allow_dashes` opts in).

## Tokens

Fixed blocks and guidance may use tokens. Research tokens (`{company}`, `{contact_first}`,
`{recent}`, `{proof_1}`, `{situation_read}`, …) resolve from the research cache. Each candidate
experience has its own token (`{org_experience}`, `{bluefire}`, `{solano}`, `{innova}`, `{bright_blue}`) that
expands to that experience's anchor line. `{relevant}` is resolved by the model (or, offline, to the
top of the voice's evidence shortlist): it drops in the single experience that best fits the point.

## Evidence

The voice's `evidence` block steers selection deterministically: `prefer` nudges up, `pin` forces
in, `exclude` removes, `category_weights` tilt per bridge, and `count` sets how many experiences are
tied. An explicit `{key}` token always grounds that experience even if it is excluded from automatic
selection.

The advisory guard (`app/validate.py::floor_notes`) checks the floor after composition and surfaces
soft/hard notes; nothing is blocked.
