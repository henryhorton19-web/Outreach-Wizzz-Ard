"""EXAMPLE candidate profile — safe template, committed to the repo to the public repo.

This file is git-ignored. It is the ONLY source of real personal facts (name, email,
phone, CV) that may enter an email. engine/config.py imports CANDIDATE_PROFILE from here
if this file exists; otherwise it falls back to the placeholder profile in config.py so
the app still runs for anyone who clones the repo.

To set up on a new device: copy config.local.example.py to config.local.py and fill in
your details (or let the private paris-data layer carry it).
"""

CANDIDATE_PROFILE = {
    "name": "Your Name",
    "email": "you@example.com",
    "phone": "+00 0000000000",
    "linkedin": "Your Name",
    "education": {
        "primary": "Your university, degree, dates, standing.",
        "exchange": "Any exchange / secondary programme.",
    },
    "one_line": "One speakable sentence describing who you are and what you want.",
    "spine": "The core claim reused across every voice — your durable through-line.",
    "experiences": {
        "role_key": {
            "name": "Company Name",
            "title": "Your Title",
            "when": "Mon YYYY - Mon YYYY",
            "tense": "present",            # 'present' if ongoing, else 'past'
            "anchor": "One speakable sentence summarising this role.",
            "facts": [
                "A guarded, CV-sourced claim the numeric/name guard will allow.",
                "Another concrete, verifiable fact.",
            ],
            "bridges": ["signal_a", "signal_b"],
            # "optional": True,            # gate: include only if the target rewards it
        },
        # add more experiences keyed by short slug...
    },
    "signals": [
        "A credibility signal (education, selection, leadership).",
    ],
    # Numbers the draft guard will allow to appear (strings). Add every figure you cite.
    "allowed_numbers": ["10", "100"],
}

