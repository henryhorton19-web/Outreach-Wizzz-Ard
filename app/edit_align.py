"""Edit alignment & classification (Plan 26, Stage 3).

Decomposes the diff between a machine draft and the user's final approved email into per-block
edits, classified by type:

  * `authored`: blank-box email (no machine draft). Effort = 1.0.
  * `unchanged`: final email equals machine draft. Effort = 0.0.
  * `slot`: local word/phrase replacements within existing block boundaries, keeping skeleton text.
  * `structural`: block additions, removals, reorderings, or total rewrites (>60% token diff).
  * `register`: uniform stylistic tone shifts across multiple blocks.

Deterministically grounded in `difflib.SequenceMatcher`. Pure logic, standard library only.
App layer only, never raises. Deliberately outcome-free: no reply_state / bounce signal.
"""
from __future__ import annotations

import difflib
import re

from .edit_ledger import edit_effort


def align_block(original: str, edited: str) -> list[tuple]:
    """Align an original block against its edited version using SequenceMatcher.

    Emits a list of span tuples:
      - `("fixed", text)` for matching sequences;
      - `("edited", orig_text, edit_text)` for replaced sequences;
      - `("inserted", text)` for added text;
      - `("deleted", text)` for removed text.
    """
    sm = difflib.SequenceMatcher(None, original, edited, autojunk=False)
    if sm.ratio() < 0.35:
        return [("edited", original, edited)]
    spans: list[tuple] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        orig_chunk = original[i1:i2]
        edit_chunk = edited[j1:j2]
        if tag == "equal":
            if orig_chunk:
                spans.append(("fixed", orig_chunk))
        elif tag == "replace":
            spans.append(("edited", orig_chunk, edit_chunk))
        elif tag == "insert":
            if edit_chunk:
                spans.append(("inserted", edit_chunk))
        elif tag == "delete":
            if orig_chunk:
                spans.append(("deleted", orig_chunk))
    return _coalesce_spans(spans)


def _coalesce_spans(spans: list[tuple]) -> list[tuple]:
    """Merge adjacent spans of the same type to keep representation minimal."""
    if not spans:
        return []
    merged: list[tuple] = []
    for sp in spans:
        if not merged:
            merged.append(sp)
            continue
        prev = merged[-1]
        if prev[0] == sp[0] == "fixed":
            merged[-1] = ("fixed", prev[1] + sp[1])
        elif prev[0] == sp[0] == "edited":
            merged[-1] = ("edited", prev[1] + sp[1], prev[2] + sp[2])
        else:
            merged.append(sp)
    return merged


def classify(*, machine_email: str, machine_blocks: dict, final_email: str) -> dict:
    """Classify the user's edits between machine_email and final_email.

    Returns a dict:
      {
        "kind": "authored" | "unchanged" | "slot" | "structural" | "register",
        "overall_effort": float,
        "slot_edits": {block_id: {"original": str, "edited": str, "spans": [...]}},
        "structural_blocks": [block_id],
      }
    Never raises.
    """
    try:
        machine = (machine_email or "").strip()
        final = (final_email or "").strip()
        if not machine:
            return {"kind": "authored", "overall_effort": 1.0, "slot_edits": {}, "structural_blocks": []}

        if machine == final:
            return {"kind": "unchanged", "overall_effort": 0.0, "slot_edits": {}, "structural_blocks": []}

        eff = edit_effort(machine, final)
        blocks = machine_blocks or {}
        if not blocks:
            kind = "slot" if eff <= 0.35 else ("structural" if eff > 0.60 else "register")
            return {"kind": kind, "overall_effort": round(float(eff), 4),
                    "slot_edits": {}, "structural_blocks": []}

        b_keys = list(blocks.keys())
        m_parts = [str(blocks[k] or "").strip() for k in b_keys]
        f_parts = [p.strip() for p in final.split("\n\n") if p.strip()]

        slot_edits: dict[str, dict] = {}
        structural_blocks: list[str] = []

        if len(f_parts) == len(m_parts):
            for k, m_p, f_p in zip(b_keys, m_parts, f_parts):
                if m_p == f_p:
                    continue
                p_eff = edit_effort(m_p, f_p)
                if p_eff > 0.65:
                    structural_blocks.append(k)
                else:
                    spans = align_block(m_p, f_p)
                    slot_edits[k] = {"original": m_p, "edited": f_p, "spans": spans}
        else:
            for k, m_p in zip(b_keys, m_parts):
                if not m_p:
                    continue
                if m_p in final:
                    continue
                p_eff = edit_effort(m_p, final)
                if p_eff > 0.70:
                    structural_blocks.append(k)
                else:
                    spans = align_block(m_p, final)
                    slot_edits[k] = {"original": m_p, "edited": final, "spans": spans}

        if eff <= 0.40 and len(structural_blocks) == 0:
            kind = "slot"
        elif len(structural_blocks) > len(b_keys) / 2 or eff > 0.65:
            kind = "structural"
        else:
            kind = "register" if len(slot_edits) > 1 else "slot"

        return {
            "kind": kind,
            "overall_effort": round(float(eff), 4),
            "slot_edits": slot_edits,
            "structural_blocks": structural_blocks,
        }
    except Exception:
        return {"kind": "structural", "overall_effort": 1.0, "slot_edits": {}, "structural_blocks": []}
