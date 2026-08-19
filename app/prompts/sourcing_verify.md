# Sourcing Verification Prompt Template

You are evaluating a candidate company surfaced from startup-heat feeds and press coverage for Paris Outreach.

## Objective
Determine whether the candidate is a suitable target for an analyst / ops / GTM outreach pitch in Paris or a remote-English environment.

## Hard Location & Language Gate Rules (NON-NEGOTIABLE)
- `work_mode_proxy`: Must be `paris_office` (HQ/office in Paris area) or `remote_english` (Remote-first with English working language). If HQ is outside France and not remote-English, or French-only without Paris presence, set `location_language_gate = disqualify`.
- A `disqualify` location/language verdict MUST force `verdict = needs_review` or `reject`. It CANNOT be auto-accepted.

## Honest Pitch & Role Analysis
- `role_basis_guess`: Guess whether an analyst, ops, strategy, or GTM role exists or is plausibly createable (`hiring_manager`, `founder`, `partner`, or `unclear`).
- `role_basis_confidence`: `high`, `medium`, or `low`.
- `honest_pitch_risk`: `low`, `medium`, or `high`.
- `role_basis_confidence` and `honest_pitch_risk` are recorded for reference only. Do NOT set
  `verdict = needs_review` on either. They are job-application signals and do not determine whether a
  company is worth approaching.

## User Targeting Preferences & Criteria
{{USER_CRITERIA_BLOCK}}

## Output Format
Return JSON strictly conforming to `candidate.schema.json`:
```json
{
  "name": "...",
  "canon_slug": "...",
  "website": "...",
  "geo": { "hq_city": "...", "hq_country": "...", "office_confirmed": true },
  "size": { "employees_band": "...", "company_size_proxy": "small", "size_trend": "..." },
  "signal": {
    "funding_heat": "...",
    "likely_role_exists": true,
    "role_basis_guess": "hiring_manager",
    "role_basis_confidence": "medium",
    "signal_basis": "...",
    "recency_days": 14
  },
  "classification": {
    "sector_tag": "...",
    "work_mode_proxy": "paris_office",
    "working_language_proxy": "English"
  },
  "gates": { "location_language_gate": "pass" },
  "fit": { "why_fit": "...", "honest_pitch_risk": "low" },
  "discovery": { "source_id": "...", "source_url": "...", "retrieved_at": "..." },
  "evidence_sources": ["..."],
  "verdict": "accept",
  "reject_reason": "",
  "confidence": "medium",
  "score": 80,
  "tier": "Tier 1"
}
```
