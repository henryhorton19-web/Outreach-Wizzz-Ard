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


class ProfileStore:
    """Manages reading and persisting candidate profiles."""
    @staticmethod
    def profile_path() -> Path:
        try:
            custom_dir = os.environ.get("WIZZARD_DATA_DIR") or os.environ.get("PARIS_DATA_DIR")
            if custom_dir:
                return Path(custom_dir) / "candidate_profile.json"
            from app import settings as S
            return S.DATA_DIR / "candidate_profile.json"
        except Exception:
            return Path.home() / ".outreach_wizzard" / "candidate_profile.json"

    @classmethod
    def load(cls) -> dict:
        if os.environ.get("WIZZARD_PROFILE_SOURCE") == "fixture":
            from tests.fixtures.profile import FIXTURE_PROFILE
            return dict(FIXTURE_PROFILE)
        p = cls.profile_path()
        if p.exists():
            try:
                import json
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        return dict(CANDIDATE_PROFILE)

    @classmethod
    def save(cls, profile: dict) -> None:
        if os.environ.get("WIZZARD_PROFILE_SOURCE") == "fixture":
            return
        p = cls.profile_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        import json
        p.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        import sys
        for mod_name in ("config", "engine.config"):
            if mod_name in sys.modules:
                setattr(sys.modules[mod_name], "CANDIDATE_PROFILE", profile)

    @classmethod
    def reset_to_default(cls) -> dict:
        cls.save(DEFAULT_PROFILE_TEMPLATE)
        return DEFAULT_PROFILE_TEMPLATE


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
