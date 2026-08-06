"""Pure dictionary transform for imported voice JSONs (Part 3 of EXECUTION_PLAN_5).

Converts legacy voice structures (e.g. from prior org tools or legacy exports) into
valid CustomVoice dict shapes.
"""
import json
from pathlib import Path

MAP_FILE = Path(__file__).parent / "voice_import_map.json"


def _load_map() -> dict:
    if MAP_FILE.exists():
        try:
            return json.loads(MAP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "field_renames": {
            "sector_weights": "category_weights",
            "custom_angles": "custom_facts",
            "firm_positioning_note": "identity_note",
            "firm_identity": "identity_record",
            "firm_name": "identity_name",
        }
    }


def migrate_voice_dict(data: dict) -> dict:
    if not isinstance(data, dict):
        return data
    d = dict(data)
    mapping = _load_map().get("field_renames", {})
    for old_k, new_k in mapping.items():
        if old_k in d and new_k not in d:
            d[new_k] = d.pop(old_k)

    # Fold legacy candidate_evidence/spine to profile_evidence/spine if present
    if "candidate_evidence" in d and "profile_evidence" not in d:
        d["profile_evidence"] = d.pop("candidate_evidence")
    if "candidate_spine" in d and "profile_spine" not in d:
        d["profile_spine"] = d.pop("candidate_spine")

    # Set audience default if missing
    if "audience" not in d:
        d["audience"] = "organisation" if d.get("identity_record") or d.get("identity_note") else "self"

    return d
