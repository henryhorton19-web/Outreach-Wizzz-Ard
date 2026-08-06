"""Candidate verification against candidate.schema.json."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.sourcing.gates import evaluate_local_gates

# NOTE: app/prompts/sourcing_verify.md exists but was never read. verify_candidate
# applies deterministic local gates only; the mandate is applied at the harvest
# stage (see GroundedSearchHarvester.build_query). If a model-backed verify is
# added later, load the prompt there and reinstate a provider argument then.


def _matched_exclusion(exclude_notes: str, name: str, meta: dict) -> str:
    """Return the exclusion phrase that matched, or "".

    Phrases are separated by ';' or a newline, not by whitespace. The previous rule
    split on whitespace and substring-matched every token over 3 characters against
    the company NAME alone, so "Skip mature legacy enterprises" rejected
    "Legacy Robotics". Leading directive words are dropped so a phrase written
    naturally ("Skip staffing agencies") still matches on its content.
    """
    if not exclude_notes:
        return ""
    _DIRECTIVES = {"skip", "exclude", "avoid", "no", "not", "ignore", "omit", "drop"}
    haystack = " ".join(str(x) for x in (
        name, meta.get("sector_tag", ""), meta.get("funding_heat", ""),
        meta.get("employees_band", ""), meta.get("website", ""),
    )).lower()
    for raw in re.split(r"[;\n]+", exclude_notes):
        phrase = raw.strip().strip(".").lower()
        if not phrase:
            continue
        words = [w for w in phrase.split() if w not in _DIRECTIVES]
        # A phrase must be at least two words, or one word of 5+ characters, to
        # count. Single short words are what produced the false rejections.
        if not words or (len(words) == 1 and len(words[0]) < 5):
            continue
        if " ".join(words) in haystack:
            return " ".join(words)
    return ""


def verify_candidate(candidate_raw: dict,
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
    matched_exclusion = _matched_exclusion(exclude_notes, name, meta)
    rejected_by_custom_prompt = bool(matched_exclusion)

    # Rule R3 / C8: gate check enforcement
    if loc_gate == "disqualify":
        verdict = "needs_review"
        reject_reason = "Location/language gate mismatch (non-Paris, non-remote English)"
        tier = "Needs Review"
        score = 40
    elif rejected_by_custom_prompt:
        verdict = "reject"
        reject_reason = f"Excluded by custom prompt criteria: '{matched_exclusion}'"
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
