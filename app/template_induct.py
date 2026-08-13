"""Template induction engine (Plan 26, Stage 4).

Induces a list of `Block` models from an accumulated exemplar corpus for a self-learning voice.
Common text across exemplars becomes `mode="fixed"` (skeleton); variable text becomes `mode="ai"`
(holes) with an inferred `fact_scope`.

Pure logic, standard library only (difflib, re). App layer, never raises.
Deliberately outcome-free: no reply_state / bounce signal.
"""
from __future__ import annotations

import difflib
import re

from . import exemplars, settings as S
from .models import Block, FACT_SCOPES


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\S+|\s+", text or "")


def _find_common_skeleton(texts: list[str], weights: list[float], support_req: int) -> list[tuple[str, str]]:
    """Align multiple exemplar texts to find weighted common sequence spans.

    Returns a list of `(kind, text)` tuples where kind is "fixed" or "hole".
    """
    if not texts:
        return []
    if len(texts) == 1:
        lines = [ln for ln in texts[0].split("\n") if ln.strip()]
        if len(lines) >= 3:
            greeting = lines[0]
            close = lines[-1]
            body_text = "\n".join(lines[1:-1])
            return [("fixed", greeting + "\n\n"), ("hole", body_text), ("fixed", "\n\n" + close)]
        return [("hole", texts[0])]

    base = texts[0]
    base_toks = _tokenize(base)

    match_scores = [0.0] * len(base_toks)
    total_w = sum(weights)

    for txt, w in zip(texts, weights):
        txt_toks = _tokenize(txt)
        sm = difflib.SequenceMatcher(None, base_toks, txt_toks, autojunk=False)
        for i, j, n in sm.get_matching_blocks():
            for idx in range(i, i + n):
                if idx < len(match_scores):
                    match_scores[idx] += w

    # A token must have support from multiple exemplars (>= 60% of total weight) to be skeleton
    threshold = total_w * 0.60 if total_w > 0 else 1.0

    spans: list[tuple[str, str]] = []
    curr_kind = None
    curr_tokens: list[str] = []

    for tok, score in zip(base_toks, match_scores):
        kind = "fixed" if score >= threshold else "hole"
        if curr_kind is None:
            curr_kind = kind
            curr_tokens = [tok]
        elif kind == curr_kind:
            curr_tokens.append(tok)
        else:
            spans.append((curr_kind, "".join(curr_tokens)))
            curr_kind = kind
            curr_tokens = [tok]

    if curr_tokens and curr_kind:
        spans.append((curr_kind, "".join(curr_tokens)))

    return spans


def _infer_fact_scope(hole_text: str) -> list[str]:
    """Infer appropriate fact_scope for a generated hole based on text cues."""
    txt = (hole_text or "").lower()
    scopes: set[str] = set()
    if any(w in txt for w in ("saw", "noticed", "read", "congrats", "article", "post")):
        scopes.add("earned_observation")
    if any(w in txt for w in ("help", "scale", "build", "grow", "team", "engineer")):
        scopes.add("profile_evidence")
    if any(w in txt for w in ("rais", "fund", "series", "round", "hiring", "launch")):
        scopes.add("situation_read")
    if not scopes:
        scopes = {"earned_observation", "profile_evidence"}
    return sorted([s for s in scopes if s in FACT_SCOPES])


def induct(voice_id: str) -> list[Block]:
    """Induct a list of `Block` models for a self-learning voice.

    Returns empty list if exemplars < min_for_induction.
    Never raises.
    """
    try:
        st = S.load_settings()
        min_req = int(getattr(st, "exemplar_min_for_induction", 2) or 2)
        recs = exemplars.load(voice_id)
        if len(recs) < min_req:
            return []

        # Sort exemplars by weight descending (authored first)
        recs = sorted(recs, key=lambda r: float(r.get("weight", 1.0)), reverse=True)
        texts = [r["final_email"] for r in recs]
        weights = [float(r.get("weight", 1.0)) for r in recs]

        raw_spans = _find_common_skeleton(texts, weights, support_req=2)
        if not raw_spans:
            return []

        blocks: list[Block] = []
        b_idx = 1

        for kind, chunk in raw_spans:
            chunk_str = chunk.strip()
            if not chunk_str:
                continue

            bid = f"block_{b_idx}"
            b_idx += 1

            if kind == "fixed":
                blocks.append(Block(
                    id=bid,
                    mode="fixed",
                    length="body",
                    text=chunk
                ))
            else:
                scopes = _infer_fact_scope(chunk_str)
                blocks.append(Block(
                    id=bid,
                    mode="ai",
                    length="body",
                    guidance="Adapt exemplar structure to the target company.",
                    fact_scope=scopes
                ))

        return blocks
    except Exception:
        return []
