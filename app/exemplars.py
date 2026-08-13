"""The exemplar corpus (Plan 26) — every email the user actually approved under a self-learning
voice, with the machine draft it came from and the company it was written for.

Why this exists alongside `edit_ledger`: the ledger stores a body-level before/after pair ONLY when
the body changed and the frame did not, keyed by voice, trimmed oldest-first, and capped at 4 pairs
injected by recency. For an exemplar-driven voice all three of those choices are wrong. The two most
informative records are exactly the ones the ledger drops:

  * an approval with NO edit at all -- the strongest possible statement of "this is what I send";
  * an email the user typed from scratch in the blank box -- the only pure human signal in the system.

So this module stores every approval, keeps the company features that generated it (without which
retrieval is impossible), keeps per-block text (without which an edit cannot be attributed to a
move), records provenance so authored text can permanently outweigh merely-tolerated text, and
evicts by value rather than by age.

There is deliberately NO outcome field. This voice type learns from the user's text and edits only.
Adding reply_state here would reintroduce a sparse, delayed, confounded signal that cannot be
estimated at per-company granularity anyway (voice_stats_min_n is 15).

App layer only. One JSONL file per voice under the data dir. Every public function swallows its own
errors: the corpus must never break an approve or a draft.
"""
from __future__ import annotations

import json
import datetime
import difflib
import re
from pathlib import Path

from . import settings as S
from .edit_ledger import edit_effort

CORPUS_DIR = S.DATA_DIR / "exemplars"
try:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# Provenance weights. Authored text is the only signal not contaminated by what the model proposed,
# so it outweighs tolerated text permanently rather than decaying. See Stage 6 on self-consumption.
WEIGHT = {"authored": 3.0, "tolerated": 1.0}

# Feature keys copied out of the draft spec. Kept small and flat on purpose: these are the retrieval
# key, and a wide dict makes every company look equally similar to every other.
FEATURE_KEYS = ("company", "what_they_do", "situation_read", "sector", "observation",
                "contact_role", "city")


def path(voice: str) -> Path:
    safe = "".join(ch for ch in (voice or "default") if ch.isalnum() or ch in "-_") or "default"
    return CORPUS_DIR / f"{safe}.jsonl"


def features_from_spec(spec: dict | None, cache: dict | None = None) -> dict:
    """Pull the retrieval key out of a draft spec. Never raises."""
    out: dict[str, str] = {}
    try:
        spec = spec or {}
        cache = cache or {}
        for k in FEATURE_KEYS:
            v = spec.get(k) or cache.get(k) or ""
            if isinstance(v, str) and v.strip():
                out[k] = v.strip()
        contact = (cache.get("contact") or {}) if isinstance(cache.get("contact"), dict) else {}
        if not out.get("contact_role") and contact.get("title"):
            out["contact_role"] = str(contact["title"]).strip()
    except Exception:
        return out
    return out


def record(*, voice: str, slug: str, provenance: str, machine_email: str,
           machine_blocks: dict, final_email: str, features: dict) -> bool:
    """Append one exemplar. Returns True when written. Never raises.

    `provenance` is "authored" (blank box, no machine draft) or "tolerated" (approved a machine
    draft, edited or not). Unlike edit_ledger.record_edit this NEVER refuses a record for being
    unedited or for touching the frame -- those are the records that matter most here.
    """
    try:
        final = (final_email or "").strip()
        if len(final) < 10:
            return False
        prov = provenance if provenance in WEIGHT else "tolerated"
        machine = (machine_email or "").strip()
        # An authored email has no prediction to compare against, so effort is 1.0 by definition:
        # the model contributed nothing. An unedited approval is 0.0.
        eff = 1.0 if (prov == "authored" or not machine) else edit_effort(machine, final)
        rec = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "voice": voice or "",
            "slug": slug or "",
            "provenance": prov,
            "machine_email": machine,
            "machine_blocks": {k: v for k, v in (machine_blocks or {}).items()
                               if isinstance(k, str) and isinstance(v, str)},
            "final_email": final,
            "features": {k: v for k, v in (features or {}).items() if isinstance(v, str)},
            "effort": round(float(eff), 4),
        }
        p = path(voice)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _evict(voice)
        return True
    except Exception:
        return False


def load(voice: str) -> list[dict]:
    """Every exemplar for a voice, oldest first. Corrupt lines are skipped, not fatal. Never raises."""
    out: list[dict] = []
    try:
        p = path(voice)
        if not p.exists():
            return []
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if isinstance(r, dict) and r.get("final_email"):
                r.setdefault("provenance", "tolerated")
                r.setdefault("weight", WEIGHT.get(r.get("provenance", "tolerated"), 1.0))
                r.setdefault("features", {})
                r.setdefault("machine_blocks", {})
                r.setdefault("effort", 1.0)
                out.append(r)
    except Exception:
        return out
    return out


def count(voice: str) -> int:
    return len(load(voice))


def effort_series(voice: str) -> list[float]:
    """Chronological effort per turn, authored turns EXCLUDED (they have no prediction, so their
    1.0 is definitional and would corrupt the convergence trend). Never raises."""
    try:
        return [float(r.get("effort", 1.0)) for r in load(voice)
                if r.get("provenance") != "authored"]
    except Exception:
        return []


def _value(rec: dict) -> float:
    """Eviction score. Authored beats tolerated; among tolerated, a draft the user barely touched
    is a better exemplar than one they rewrote. Higher is kept."""
    try:
        prov = rec.get("provenance", "tolerated")
        w = float(rec.get("weight") or WEIGHT.get(prov, 1.0))
        eff = float(rec.get("effort", 1.0))
        if prov == "authored":
            return 100.0 + w
        return w * (1.0 - min(1.0, max(0.0, eff)))
    except Exception:
        return 0.0


def _evict(voice: str) -> None:
    """Trim to the cap by VALUE, not by age. Authored exemplars are never evicted -- losing them
    would remove the only uncontaminated signal and leave the voice learning from itself."""
    try:
        st = S.load_settings()
        cap = int(getattr(st, "exemplar_corpus_cap", 200) or 200)
        recs = load(voice)
        if len(recs) <= cap:
            return
        authored = [r for r in recs if r.get("provenance") == "authored"]
        tolerated = [r for r in recs if r.get("provenance") != "authored"]
        room = max(0, cap - len(authored))
        tolerated = sorted(tolerated, key=_value, reverse=True)[:room]
        kept = sorted(authored + tolerated, key=lambda r: r.get("ts", ""))
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n"
        from . import store
        store.safe_write_text(path(voice), text)
    except Exception:
        pass


def retrieve(voice: str, features: dict, k: int = 2) -> list[dict]:
    """Retrieve top k exemplars closest in feature similarity to `features`, weighted by provenance.

    Never raises.
    """
    try:
        recs = load(voice)
        if not recs:
            return []

        def _similarity(rec: dict) -> float:
            rec_feats = rec.get("features", {}) or {}
            score = 0.0
            for fk in FEATURE_KEYS:
                v1 = (features.get(fk) or "").strip().lower()
                v2 = (rec_feats.get(fk) or "").strip().lower()
                if v1 and v2:
                    if v1 == v2:
                        score += 2.0
                    else:
                        score += difflib.SequenceMatcher(None, v1, v2).ratio()
            w = float(rec.get("weight") or WEIGHT.get(rec.get("provenance", "tolerated"), 1.0))
            return score * w

        scored = [(rec, _similarity(rec)) for rec in recs]
        scored.sort(key=lambda item: item[1], reverse=True)
        return [item[0] for item in scored[:k]]
    except Exception:
        return []
