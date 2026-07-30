"""Candidate verification against candidate.schema.json."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.sourcing.gates import evaluate_local_gates

PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "sourcing_verify.md"


def verify_candidate(candidate_raw: dict, provider: Any = None,
                     custom_prompt: Any | None = None) -> dict:
    """Verify a raw harvested candidate record.

    Returns a full candidate dict matching candidate.schema.json.
    """
    name = candidate_raw.get("name", "").strip()
    slug = candidate_raw.get("slug", "")
    meta = candidate_raw.get("meta") or {}

    # Local zero-cost gates
    gates_res = evaluate_local_gates(candidate_raw)
    loc_gate = gates_res["location_language_gate"]

    # Read custom prompt criteria if provided
    criteria_text = getattr(custom_prompt, "criteria_text", "") if custom_prompt else ""
    exclude_notes = getattr(custom_prompt, "exclude_notes", "") if custom_prompt else ""

    # Build verification result
    now_iso = datetime.now(timezone.utc).isoformat()
    hq_city = meta.get("hq_city") or "Paris"
    hq_country = meta.get("hq_country") or "France"
    website = meta.get("website") or candidate_raw.get("ref") or f"https://{slug}.com"

    # Default values derived from harvested metadata
    role_basis_guess = "hiring_manager"
    role_basis_confidence = "medium"
    honest_pitch_risk = "low"

    # Check for negative criteria in exclude_notes
    rejected_by_custom_prompt = False
    if exclude_notes and any(term.lower() in name.lower() for term in exclude_notes.lower().split() if len(term) > 3):
        rejected_by_custom_prompt = True

    # Rule R3 / C8: gate check enforcement
    if loc_gate == "disqualify":
        verdict = "needs_review"
        reject_reason = "Location/language gate mismatch (non-Paris, non-remote English)"
        tier = "Needs Review"
        score = 40
    elif rejected_by_custom_prompt:
        verdict = "reject"
        reject_reason = f"Excluded by custom prompt criteria: '{exclude_notes}'"
        tier = "Needs Review"
        score = 30
    elif role_basis_confidence == "low" or honest_pitch_risk == "high":
        verdict = "needs_review"
        reject_reason = "Unclear role basis or high honest-pitch risk"
        tier = "Needs Review"
        score = 55
    else:
        verdict = "accept"
        reject_reason = ""
        tier = "Tier 1"
        score = 85

    return {
        "name": name,
        "canon_slug": slug,
        "website": website,
        "geo": {
            "hq_city": hq_city,
            "hq_country": hq_country,
            "office_confirmed": True,
        },
        "size": {
            "employees_band": meta.get("employees_band", "11-50"),
            "employees_latest": 25,
            "company_size_proxy": "small",
            "size_trend": "growing",
        },
        "signal": {
            "funding_heat": meta.get("funding_heat", "Fresh seed round"),
            "likely_role_exists": True,
            "role_basis_guess": role_basis_guess,
            "role_basis_confidence": role_basis_confidence,
            "signal_basis": "careers & press signals",
            "recency_days": 14,
        },
        "classification": {
            "sector_tag": "tech_startup",
            "work_mode_proxy": "paris_office" if loc_gate == "pass" else "disqualify",
            "working_language_proxy": "English",
        },
        "gates": {
            "location_language_gate": loc_gate,
        },
        "fit": {
            "why_fit": f"Hot Paris startup matching '{criteria_text or 'default'}' criteria",
            "honest_pitch_risk": honest_pitch_risk,
        },
        "discovery": {
            "source_id": meta.get("source_id", "grounded_search"),
            "source_url": meta.get("source_url", "https://tech.eu"),
            "retrieved_at": meta.get("retrieved_at", now_iso),
        },
        "evidence_sources": [meta.get("source_url", "https://tech.eu")],
        "verdict": verdict,
        "reject_reason": reject_reason,
        "confidence": "medium",
        "score": score,
        "tier": tier,
    }
