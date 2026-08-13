# Self-Learning Voice (Edit-Grounded Template Induction)

**Feature Specification & Architecture Reference (Plan 26)**

## Overview

Self-Learning Voices (`learning="exemplar"`) acquire their email structure, skeleton text, and fact scopes dynamically from the emails the user writes and approves under that voice. Unlike Layer 4 patch voices, exemplar voices do not rely on fixed prompt templates or outcome feedback loops (reply rates, bounce rates, or A/B arbitration). They are grounded strictly in human text and edits.

---

## 9-Stage Pipeline Architecture

```
[User Approves Email]
          │
          ▼
1. Data Capture (app/exemplars.py)
   ├── Store in DATA_DIR/exemplars/<voice>.jsonl
   └── Authored (weight=3.0) vs Tolerated (weight=1.0)
          │
          ▼
2. Blank Box Turn 0 (app/pipeline.py:author_blank)
   └── User writes email from scratch; machine_email = ""
          │
          ▼
3. Edit Decomposition (app/edit_align.py)
   └── Classifies diffs: slot | structural | register
          │
          ▼
4. Template Induction (app/template_induct.py)
   └── Pairwise SequenceMatcher -> fixed skeleton + ai holes
          │
          ▼
5. Generation from Template (app/compose.py)
   └── Retrieves k nearest exemplars by features -> injected into system prompt
          │
          ▼
6. Guardrails & Convergence (app/exemplar_guards.py)
   ├── Leak guard: flags foreign company proper nouns
   ├── Novelty guard: caps n-gram overlap with sent emails (max 0.72)
   └── Freeze guard: detects rising edit effort over 4 turns
          │
          ▼
7. Replay Evaluation Harness (app/exemplar_replay.py)
   └── GET /api/voices/{voice_id}/exemplar/replay -> delta effort saved
```

---

## Technical Summary

| Component | Responsibility |
|---|---|
| `app/models.py` | `CustomVoice.learning: "patch" \| "exemplar"`, `template_meta: dict`, `TargetState.machine_blocks` |
| `app/exemplars.py` | JSONL storage per voice, eviction by value score, feature similarity retrieval |
| `app/pipeline.py` | `author_blank` for turn 0 blank-box authoring |
| `app/edit_align.py` | Diff classification and character/token alignment |
| `app/template_induct.py` | Progressive multi-sequence alignment into `Block` models |
| `app/exemplar_voice.py` | `status`, `preview`, `apply_template`, `maybe_freeze`, `unfreeze` |
| `app/exemplar_guards.py` | Leak guard, novelty guard, effort freeze detector, `merge_extra` |
| `app/exemplar_replay.py` | Offline replay harness evaluating average effort reduction |
| `app/server.py` | API routing for `/api/companies/{slug}/blank`, `/api/voices/{id}/exemplar/*` |
