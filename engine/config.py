"""All tunable content for the outreach engine. No YAML, no loader.

The candidate profile (the ONLY source of personal facts that may enter an email) is loaded
from the git-ignored engine/config_local.py when present, else a placeholder is used. The
numeric/name guards in draft_engine reject anything not traceable to the profile or to the
target's sourced research points.
"""

import os

# ---- global knobs ----------------------------------------------------------
DEFAULT_VOICE = "role_small"
TIMING_RECENT_MONTHS = 6          # a "recent point" should be within ~6 months
WORD_MIN = 70                     # phone-readable target range (soft)
WORD_MAX = 120


from pathlib import Path

# ---- candidate profile (from CV; guarded facts) ----------------------------
DEFAULT_PROFILE_TEMPLATE = {
    "name": "Your Name",
    "candidate_name": "Your Name",
    "email": "you@example.com",
    "phone": "+00 0000000000",
    "linkedin": "Your Name",
    "education": {"primary": "Your university, degree, dates, standing."},
    "one_line": "One speakable sentence describing who you are and what you want.",
    "spine": "The core claim reused across every voice.",
    "standing_key": "anchor_co",
    "standing_experience": "I led operations at Anchor Co.",
    "target_roles": ["Chief of Staff", "Operations Lead", "Growth Manager"],
    "target_firm_types": ["Growth / Tech / Venture"],
    "target_locations": ["Remote", "New York", "London", "Paris"],
    "allowed_locations": [],
    "experiences": {
        "anchor_co": {
            "name": "Anchor Co",
            "title": "Lead Operator",
            "when": "2024 - Present",
            "tense": "present",
            "anchor": "I led operations and strategy at Anchor Co.",
            "facts": ["Scales operations across teams."],
            "bridges": ["analytical", "builds", "ops"],
        },
    },
    "signals": ["A credibility signal."],
    "proof_points": [
        {
            "fact": "Shipped core product features and scaled operational workflows.",
            "metrics": ["100"],
            "tags": ["builds", "analytical"]
        }
    ]
}

_PLACEHOLDER_PROFILE = DEFAULT_PROFILE_TEMPLATE


import re

_PROFILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

def validate_profile_id(pid: str) -> str:
    pid = (pid or "").strip()
    if not pid or not _PROFILE_ID_RE.match(pid):
        raise ValueError(f"Invalid profile ID '{pid}': must be 1-64 alphanumeric, dash, or underscore characters")
    if pid.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"Invalid profile ID '{pid}': reserved Windows filename")
    return pid


class ProfileStore:
    """Manages reading and persisting candidate profiles (keyed multi-profile support)."""

    @classmethod
    def _data_dir(cls) -> Path:
        try:
            custom_dir = os.environ.get("WIZZARD_DATA_DIR") or os.environ.get("PARIS_DATA_DIR")
            if custom_dir:
                return Path(custom_dir)
            from app import settings as S
            return S.DATA_DIR
        except Exception:
            return Path.home() / ".outreach_wizzard"

    @classmethod
    def profiles_dir(cls) -> Path:
        d = cls._data_dir() / "profiles"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def manifest_path(cls) -> Path:
        return cls._data_dir() / "profiles.json"

    @classmethod
    def active_profile_id(cls) -> str:
        mp = cls.manifest_path()
        if mp.exists():
            try:
                import json
                data = json.loads(mp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("active"):
                    return str(data["active"])
            except Exception:
                pass
        return "default"

    @classmethod
    def list_profiles(cls) -> list[dict]:
        cls._ensure_migrated()
        mp = cls.manifest_path()
        if mp.exists():
            try:
                import json
                data = json.loads(mp.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "profiles" in data:
                    return data["profiles"]
            except Exception:
                pass
        return [{"id": "default", "name": "Default Profile"}]

    @classmethod
    def set_active_profile(cls, profile_id: str) -> bool:
        pid = validate_profile_id(profile_id)
        if not cls.profile_path(pid).exists():
            return False
        profs = cls.list_profiles()
        if not any(p["id"] == pid for p in profs):
            profs.append({"id": pid, "name": pid})
        import json
        manifest = {"active": pid, "profiles": profs}
        cls.manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return True

    @classmethod
    def profile_path(cls, profile_id: str | None = None) -> Path:
        pid = validate_profile_id(profile_id or cls.active_profile_id())
        return cls.profiles_dir() / f"{pid}.json"

    @classmethod
    def _ensure_migrated(cls):
        if os.environ.get("WIZZARD_PROFILE_SOURCE") == "fixture":
            return
        d = cls._data_dir()
        legacy_file = d / "candidate_profile.json"
        default_file = cls.profiles_dir() / "default.json"
        if legacy_file.exists() and not default_file.exists():
            try:
                import json
                text = legacy_file.read_text(encoding="utf-8")
                default_file.write_text(text, encoding="utf-8")
            except Exception:
                pass
        if not default_file.exists():
            import json
            default_file.write_text(json.dumps(DEFAULT_PROFILE_TEMPLATE, indent=2), encoding="utf-8")
        if not cls.manifest_path().exists():
            import json
            cls.manifest_path().write_text(json.dumps({"active": "default", "profiles": [{"id": "default", "name": "Default Profile"}]}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, profile_id: str | None = None) -> dict:
        if os.environ.get("WIZZARD_PROFILE_SOURCE") == "fixture":
            from tests.fixtures.profile import FIXTURE_PROFILE
            import sys
            for mod_name in ("config", "engine.config"):
                if mod_name in sys.modules:
                    setattr(sys.modules[mod_name], "CANDIDATE_PROFILE", FIXTURE_PROFILE)
            return dict(FIXTURE_PROFILE)
        cls._ensure_migrated()
        p = cls.profile_path(profile_id)
        if p.exists():
            try:
                import json
                data = json.loads(p.read_text(encoding="utf-8"))
                data.setdefault("id", p.stem)
                import sys
                for mod_name in ("config", "engine.config"):
                    if mod_name in sys.modules:
                        setattr(sys.modules[mod_name], "CANDIDATE_PROFILE", data)
                return data
            except Exception:
                pass
        data = dict(CANDIDATE_PROFILE)
        data["id"] = profile_id or cls.active_profile_id()
        return data

    @classmethod
    def get_profile(cls, profile_id: str) -> dict | None:
        p = cls.profile_path(profile_id)
        if not p.exists():
            return None
        return cls.load(profile_id)

    @classmethod
    def create_profile(cls, profile_id: str, name: str, copy_from: str | None = None):
        pid = validate_profile_id(profile_id)
        cls._ensure_migrated()
        base = cls.load(copy_from) if (copy_from and cls.profile_path(copy_from).exists()) else dict(DEFAULT_PROFILE_TEMPLATE)
        base["id"] = pid
        base["full_name"] = name
        base["name"] = name
        cls.save(base, profile_id=pid)

        profs = cls.list_profiles()
        if not any(p["id"] == pid for p in profs):
            profs.append({"id": pid, "name": name})
            import json
            manifest = {"active": cls.active_profile_id(), "profiles": profs}
            cls.manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        from types import SimpleNamespace
        import json
        res = json.loads(json.dumps(base))
        res["id"] = pid
        res["full_name"] = name
        return SimpleNamespace(**res)

    @classmethod
    def save(cls, profile: dict, profile_id: str | None = None) -> None:
        if os.environ.get("WIZZARD_PROFILE_SOURCE") == "fixture":
            from tests.fixtures.profile import FIXTURE_PROFILE
            import sys
            for mod_name in ("config", "engine.config"):
                if mod_name in sys.modules:
                    setattr(sys.modules[mod_name], "CANDIDATE_PROFILE", FIXTURE_PROFILE)
            return
        cls._ensure_migrated()
        pid = validate_profile_id(profile_id or profile.get("id") or cls.active_profile_id())
        profile["id"] = pid
        p = cls.profile_path(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        import json
        p.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        import sys
        for mod_name in ("config", "engine.config"):
            if mod_name in sys.modules:
                setattr(sys.modules[mod_name], "CANDIDATE_PROFILE", profile)

    @classmethod
    def delete_profile(cls, profile_id: str) -> bool:
        pid = validate_profile_id(profile_id)
        if pid == "default" or pid == cls.active_profile_id():
            return False
        p = cls.profile_path(pid)
        if p.exists():
            p.unlink()
        profs = [x for x in cls.list_profiles() if x["id"] != pid]
        import json
        manifest = {"active": cls.active_profile_id(), "profiles": profs}
        cls.manifest_path().write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return True

    @classmethod
    def reset_to_default(cls, profile_id: str | None = None) -> dict:
        pid = profile_id or cls.active_profile_id()
        data = dict(DEFAULT_PROFILE_TEMPLATE)
        data["id"] = pid
        cls.save(data, profile_id=pid)
        return data


if os.environ.get("WIZZARD_PROFILE_SOURCE") == "fixture":
    try:
        from tests.fixtures.profile import FIXTURE_PROFILE
        CANDIDATE_PROFILE = FIXTURE_PROFILE
    except Exception:
        CANDIDATE_PROFILE = _PLACEHOLDER_PROFILE
else:
    try:
        from config_local import CANDIDATE_PROFILE  # type: ignore  # noqa: F401
    except Exception:
        try:
            from .config_local import CANDIDATE_PROFILE  # type: ignore  # noqa: F401
        except Exception:
            CANDIDATE_PROFILE = _PLACEHOLDER_PROFILE


# ---- voices ----------------------------------------------------------------
# Voice frame content is no longer defined here. Every voice is editable data in the app-layer
# store (app/models.CustomVoice, seeded once from app/seed_voices/*.json). The engine's prepare()
# supplies a neutral empty frame and the app overrides every slot from the chosen voice, so there
# are no static voice templates in code. DEFAULT_VOICE below is a nominal id only (a routing
# fallback name), not voice content.



# ---- gate marker lists (honesty floor, not style policing) -----------------
# Forbidden investor/AI cliches and hype the reviewer would strike anyway.
FORBIDDEN_PHRASES = [
    "passionate", "results-driven", "synergy", "leverage my", "value-add", "circle back",
    "touch base", "game-changer", "disruptive", "cutting-edge", "best-in-class", "rockstar",
    "ninja", "guru", "thought leader", "move the needle", "low-hanging fruit",
]
# Sign-off tokens that must NOT appear in the body/ask (the mail client appends the signature).
SIGNOFF_MARKERS = ["best,", "best regards", "kind regards", "regards,", "sincerely,", "cheers,",
                   "thanks,", "many thanks", "warm regards", "yours "]
# Presumptuous second-person diagnosis openers to avoid.
PRESUMPTUOUS_OPENERS = ["your biggest problem", "your hardest problem", "you are struggling",
                        "you must be", "i know you are", "your pain is"]
