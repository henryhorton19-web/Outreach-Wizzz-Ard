"""Offline voice optimisation (Phase C) — a self-contained, GEPA-flavoured batch optimiser.

`voice_learning.maybe_run` does a *per-cycle* nudge from the recent edit window. This module does a
*batch* pass over a voice's ENTIRE accumulated edit corpus when you have enough data: it proposes
several candidate patches, scores each against a held-out split of your own edits, and spawns the
best-scoring candidate as an A/B challenger (never a blind overwrite). It is the local, dependency-
free analogue of running `dspy.GEPA` / `gepa-ai/gepa` over the corpus — same shape (reflect -> many
candidates -> score on held-out feedback -> keep the best), just small enough to ship inside a
self-contained desktop app with no extra install and an offline (stub) path for tests.

Scoring proxy (deterministic, no re-generation, cheap): a candidate is good if the phrasings/notes it
promotes align with what you consistently keep in APPROVED text and avoid what you consistently cut,
measured on the held-out edits via difflib overlap — a miniature 'phrasing miner' (cf. ForthWrite's
edit-distance + phrasing-miner loop). Higher = the candidate's exemplars look more like your approved
writing on edits it never saw. For the heavyweight version (generation-in-the-loop scoring, Pareto
pool, multi-objective), point this at dspy.GEPA with an edit-distance-and-reply metric.

App layer only; every function swallows its own errors and never touches a voice except via the
versioned challenger path.
"""
from __future__ import annotations

import difflib

from . import store
from . import voice_learning as VL


def _held_out_split(triples: list[dict]):
    """Split edits into train/eval. Deterministic (every 3rd item to eval) so runs are reproducible."""
    train = [t for i, t in enumerate(triples) if i % 3 != 0]
    evl = [t for i, t in enumerate(triples) if i % 3 == 0]
    return (train or triples), (evl or triples)


def _score_candidate(patch: dict, eval_triples: list[dict]) -> float:
    """How well a candidate's promoted exemplars resemble the held-out APPROVED bodies (and differ
    from the machine drafts). In [0, 1]-ish; higher is better. Deterministic; never raises."""
    try:
        exemplars = list(patch.get("promote_examples") or [])
        if not exemplars or not eval_triples:
            # a patch with no exemplars is scored by whether its direction (shorten) matches the data
            shorter = sum(1 for t in eval_triples if len(t["after"]) < len(t["before"]) * 0.9)
            wants_short = (patch.get("categorical") or {}).get("sentence_length") == "short"
            return (shorter / len(eval_triples)) if (wants_short and eval_triples) else 0.0
        score = 0.0
        for t in eval_triples:
            after, before = t["after"], t["before"]
            best_after = max(difflib.SequenceMatcher(None, ex, after).ratio() for ex in exemplars)
            best_before = max(difflib.SequenceMatcher(None, ex, before).ratio() for ex in exemplars)
            # reward resembling the approved text more than the machine draft; weight replied edits
            score += (best_after - best_before) * float(t.get("weight", 1.0))
        return score / max(1, len(eval_triples))
    except Exception:
        return 0.0


def optimize(provider, voice_id: str, *, candidates: int = 4, min_corpus: int = 6) -> dict:
    """Batch-optimise a voice: build several candidate patches, score on held-out edits, spawn the
    best as a challenger. Returns a summary. Requires a reasonable corpus. Never raises."""
    try:
        voice = store.get_custom_voice(voice_id)
        if voice is None:
            return {"ok": False, "error": "unknown voice"}
        if getattr(voice, "challenger_of", ""):
            return {"ok": False, "error": "cannot optimise a challenger"}
        triples = VL.gather(voice_id, k=200)
        if len(triples) < min_corpus:
            return {"ok": False, "error": f"need >= {min_corpus} edits, have {len(triples)}"}

        train, evl = _held_out_split(triples)

        # candidate generation: the offline heuristic gives a stable baseline; online, ask the
        # reflection model a few times over the TRAIN split for variety (GEPA's mutation step).
        cands: list[dict] = []
        base = VL.clamp_patch(VL._offline_patch(voice, train), voice)
        if not VL.patch_is_empty(base):
            cands.append(base)
        if provider is not None and not getattr(provider, "is_stub", False):
            seen = {id(base)}
            for _ in range(max(0, candidates - len(cands))):
                c = VL.clamp_patch(VL.reflect(provider, voice, train), voice)
                if not VL.patch_is_empty(c) and id(c) not in seen:
                    cands.append(c)
        if not cands:
            return {"ok": False, "error": "no viable candidate patch"}

        scored = sorted(((_score_candidate(c, evl), c) for c in cands),
                        key=lambda x: x[0], reverse=True)
        best_score, best = scored[0]
        ch = VL.spawn_challenger(voice, best)
        return {"ok": True, "voice_id": voice_id, "candidates": len(cands),
                "best_score": round(best_score, 4), "challenger": ch,
                "corpus": len(triples), "eval_n": len(evl)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
