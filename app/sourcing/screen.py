"""Candidate scoring & tier classification."""
from __future__ import annotations


def screen_candidate(verified: dict) -> dict:
    """Assign score and tier to a verified candidate object."""
    verdict = verified.get("verdict", "needs_review")
    score = verified.get("score", 50)

    if verdict == "accept" and score >= 75:
        tier = "Tier 1"
    elif verdict == "accept":
        tier = "Tier 2"
    else:
        tier = "Needs Review"

    verified["tier"] = tier
    return verified
