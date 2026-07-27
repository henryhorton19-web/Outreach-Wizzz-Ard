"""All tunable content for the outreach engine. No YAML, no loader.

The candidate profile (the ONLY source of personal facts that may enter an email) is loaded
from the git-ignored engine/config_local.py when present, else a placeholder is used. The
numeric/name guards in draft_engine reject anything not traceable to the profile or to the
target's sourced research points.
"""

# ---- global knobs ----------------------------------------------------------
DEFAULT_VOICE = "role_small"
TIMING_RECENT_MONTHS = 6          # a "recent point" should be within ~6 months
WORD_MIN = 70                     # phone-readable target range (soft)
WORD_MAX = 120


# ---- candidate profile (from CV; guarded facts) ----------------------------
# The candidate profile is the ONLY source of real personal facts that may enter an email.
# To keep this a shareable public repo, the REAL profile lives in engine/config.local.py
# (git-ignored). If that file is present it wins; otherwise this placeholder keeps the app
# runnable for anyone who clones the repo. Copy config.local.example.py -> config.local.py
# and fill it in (see that file for the full schema).
_PLACEHOLDER_PROFILE = {
    "name": "Your Name",
    "email": "you@example.com",
    "phone": "+00 0000000000",
    "linkedin": "Your Name",
    "education": {"primary": "Your university, degree, dates, standing."},
    "one_line": "One speakable sentence describing who you are and what you want.",
    "spine": "The core claim reused across every voice.",
    "experiences": {
        "example_role": {
            "name": "Company Name",
            "title": "Your Title",
            "when": "Mon YYYY - Mon YYYY",
            "tense": "past",
            "anchor": "One speakable sentence summarising this role.",
            "facts": ["A guarded, CV-sourced claim.", "Another verifiable fact."],
            "bridges": ["signal_a", "signal_b"],
        },
    },
    "signals": ["A credibility signal."],
    "allowed_numbers": ["10", "100"],
}

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
