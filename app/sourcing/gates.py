"""Local fast gates for candidate pre-verification (zero model cost)."""
from __future__ import annotations


def evaluate_location_language_gate(hq_country: str, hq_city: str,
                                     is_remote_english: bool = False,
                                     allowed_locations: set[str] | None = None) -> str:
    """Location gate, driven by an explicit allow-list.

    allowed_locations=None or empty means NO geographic constraint -- every
    candidate passes. That is the correct default for a generalised tool: the
    previous behaviour hardcoded a French-cities set, so a Poland-focused company
    was rejected before anyone looked at it, regardless of what the user was
    actually sourcing for.
    """
    if not allowed_locations:
        return "pass"
    if is_remote_english:
        return "pass"
    country_clean = (hq_country or "").strip().lower()
    city_clean = (hq_city or "").strip().lower()
    allowed = {a.strip().lower() for a in allowed_locations if a and a.strip()}
    if country_clean in allowed or city_clean in allowed:
        return "pass"
    if any(a in city_clean for a in allowed if a):
        return "pass"
    if not country_clean and not city_clean:
        return "pass"
    return "disqualify"





def evaluate_local_gates(candidate_raw: dict) -> dict:
    """Run all zero-cost local pre-verification gates on a harvested candidate.

    Returns a dict with gate verdicts: location_language_gate, size_proxy, etc.
    """
    geo = candidate_raw.get("geo") or candidate_raw.get("meta") or {}
    hq_country = geo.get("hq_country") or candidate_raw.get("country") or ""
    hq_city = geo.get("hq_city") or candidate_raw.get("city") or ""
    is_remote = bool(candidate_raw.get("is_remote_english") or geo.get("is_remote_english"))

    allowed = candidate_raw.get("allowed_locations") or None
    loc_gate = evaluate_location_language_gate(hq_country, hq_city, is_remote, allowed)

    return {
        "location_language_gate": loc_gate,
        "is_disqualified": (loc_gate == "disqualify"),
    }
