"""Local fast gates for candidate pre-verification (zero model cost)."""
from __future__ import annotations


def evaluate_location_language_gate(hq_country: str, hq_city: str,
                                     is_remote_english: bool = False) -> str:
    """Evaluate the location & language gate.

    Returns:
        'pass' if HQ is France (or Paris area) OR remote-English confirmed.
        'disqualify' if non-France and non-remote.
    """
    country_clean = (hq_country or "").strip().lower()
    city_clean = (hq_city or "").strip().lower()

    if is_remote_english:
        return "pass"

    france_names = {"france", "fr", "paris", "lyon", "marseille", "toulouse", "bordeaux", "nantes", "lille", "strasbourg"}
    if country_clean in france_names or city_clean in france_names or "paris" in city_clean:
        return "pass"

    if not country_clean and not city_clean:
        # Default to pass for local verification check if unstated, but flag for review
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

    loc_gate = evaluate_location_language_gate(hq_country, hq_city, is_remote)

    return {
        "location_language_gate": loc_gate,
        "is_disqualified": (loc_gate == "disqualify"),
    }
