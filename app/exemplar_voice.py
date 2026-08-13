"""Voice-level Operations for Self-Learning (Exemplar) Voices (Plan 26, Stage 4).

Manages the lifecycle of an exemplar voice:
  - `status(voice_id)`: returns count of exemplars, induced blocks, freeze state, convergence trend;
  - `preview(voice_id)`: returns preview of induced blocks without mutating the voice;
  - `apply_template(voice_id)`: snapshots current voice version, runs induction, saves updated voice.

App layer, standard library + app models only. Never raises.
Deliberately outcome-free: no reply_state / bounce signal.
"""
from __future__ import annotations

import datetime

from . import exemplars, template_induct, store, settings as S
from .models import CustomVoice


def status(voice_id: str) -> dict:
    """Status summary for a self-learning voice. Never raises."""
    try:
        v = store.get_custom_voice(voice_id)
        if v is None:
            return {"error": f"unknown voice: {voice_id}"}
        n_ex = exemplars.count(voice_id)
        series = exemplars.effort_series(voice_id)
        meta = v.template_meta or {}
        frozen = bool(meta.get("frozen", False))
        return {
            "voice_id": voice_id,
            "learning": getattr(v, "learning", "patch"),
            "n_exemplars": n_ex,
            "n_blocks": len(v.blocks),
            "frozen": frozen,
            "freeze_reason": meta.get("freeze_reason", ""),
            "induced_at": meta.get("induced_at"),
            "effort_series": series,
        }
    except Exception as e:
        return {"error": str(e)}


def preview(voice_id: str) -> dict:
    """Preview induced blocks for a voice without modifying it. Never raises."""
    try:
        blocks = template_induct.induct(voice_id)
        return {
            "voice_id": voice_id,
            "blocks": [b.model_dump() for b in blocks],
            "count": len(blocks),
        }
    except Exception as e:
        return {"error": str(e), "blocks": [], "count": 0}


def apply_template(voice_id: str) -> dict:
    """Run induction and update the voice definition, snapshotting first. Never raises."""
    try:
        v = store.get_custom_voice(voice_id)
        if v is None:
            return {"ok": False, "error": f"unknown voice: {voice_id}"}

        meta = v.template_meta or {}
        if meta.get("frozen"):
            return {"ok": False, "error": f"voice {voice_id} is frozen"}

        blocks = template_induct.induct(voice_id)
        if not blocks:
            return {"ok": False, "error": "insufficient exemplars for induction"}

        # Snapshot before mutating
        try:
            store.save_voice_version(voice_id)
        except Exception:
            pass

        v.blocks = blocks
        meta["induced_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        meta["n_exemplars"] = exemplars.count(voice_id)
        v.template_meta = meta

        store.save_custom_voice(v)
        return {"ok": True, "voice": v.model_dump(), "n_blocks": len(blocks)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
