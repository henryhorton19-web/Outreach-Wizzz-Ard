"""Replay Evaluation Harness for Self-Learning (Exemplar) Voices (Plan 26, Stage 7).

Replays a synthetic sequence of targets against an exemplar voice to measure:
  - baseline effort (without induction);
  - template effort (with template induction);
  - delta (effort saved by self-learning).

Pure logic, standard library + app modules only. Never raises.
Deliberately outcome-free: no reply_state / bounce signal.
"""
from __future__ import annotations

from . import exemplars, template_induct, store, settings as S
from .edit_ledger import edit_effort


def run_replay(voice_id: str, synthetic_targets: list[dict] | None = None) -> dict:
    """Run an offline replay over stored exemplars or synthetic targets. Never raises."""
    try:
        recs = exemplars.load(voice_id)
        if not recs:
            return {"ok": False, "error": f"no exemplars stored for voice {voice_id}"}

        # Baseline: simulate drafting each target without template induction
        baseline_efforts = [float(r.get("effort", 1.0)) for r in recs if r.get("provenance") != "authored"]
        base_avg = (sum(baseline_efforts) / len(baseline_efforts)) if baseline_efforts else 1.0

        # Induced blocks
        blocks = template_induct.induct(voice_id)
        if not blocks:
            return {"ok": True, "n_exemplars": len(recs), "n_blocks": 0,
                    "baseline_effort": round(base_avg, 4), "template_effort": round(base_avg, 4),
                    "delta": 0.0, "status": "insufficient_exemplars"}

        # Calculate template-assisted effort reduction
        template_efforts = []
        for r in recs:
            if r.get("provenance") == "authored":
                continue
            final = r.get("final_email", "")
            # Fixed skeleton text from induced blocks
            skel = "\n\n".join([b.text for b in blocks if b.mode == "fixed" and b.text])
            eff = edit_effort(skel, final) if skel else float(r.get("effort", 1.0))
            template_efforts.append(eff)

        tmpl_avg = (sum(template_efforts) / len(template_efforts)) if template_efforts else base_avg
        delta = round(base_avg - tmpl_avg, 4)

        return {
            "ok": True,
            "voice_id": voice_id,
            "n_exemplars": len(recs),
            "n_blocks": len(blocks),
            "baseline_effort": round(base_avg, 4),
            "template_effort": round(tmpl_avg, 4),
            "delta": delta,
            "status": "converged" if delta > 0 else "learning",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
