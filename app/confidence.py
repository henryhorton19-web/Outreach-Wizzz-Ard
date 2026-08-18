"""What a draft is standing on (Plan 32).

This module replaces an earlier, rejected design in which a missing contact or thin research stopped
the pipeline outright, leaving the operator with no draft and a retry button that could not help. The
product is human-in-the-loop -- every letter is read and approved before it sends -- so a draft with a
visible gap is strictly more useful than no draft at all. The job of this module is therefore never to
say no. It is to try one further, cheaper thing, and then to say plainly what the letter is built on.

Two independent axes, because they fail independently and the operator needs to know which one:
  contact:  "found" (a real named person) | "guessed" (a name, but the address is inferred) |
            "generic" (no person found; addressed to a role or the team)
  research: "full" (at least one real proof point and a stated business) | "thin" (neither)

Nothing here blocks anything. Every function returns a value or a labelled fallback; none raises and
none refuses. App layer only, no I/O, no model call.
"""
from __future__ import annotations

from .preflight import PLACEHOLDER_NAMES, PLACEHOLDER_LOCALS

# Ordered by how impersonal they read; the first is tried first.
_ROLE_LOCALS = ("founder", "hello", "hi", "team")


def contact_flag(cache) -> str:
    """"found" | "guessed" | "generic". Never raises."""
    try:
        contact = (cache or {}).get("contact") or {}
        name = str(contact.get("name") or "").strip().lower()
        email = str(contact.get("email") or "").strip().lower()
        local = email.split("@", 1)[0] if "@" in email else ""
        if name in PLACEHOLDER_NAMES or local in PLACEHOLDER_LOCALS or local in _ROLE_LOCALS:
            return "generic"
        method = str(contact.get("email_method") or "").strip().lower()
        if method in ("pattern_guess", "repinned_to_company_domain", ""):
            return "guessed"
        return "found"
    except Exception:
        return "generic"


def apply_contact_fallback(cache) -> dict:
    """When no named person was found, address the letter to a role instead of a placeholder.

    "Hi Unknown," is worse than "Hi there," or a letter opened to the team, because it visibly
    announces that something failed rather than reading as a deliberate, if impersonal, choice.
    Never raises; returns the cache unchanged when there is no domain to build a role address on
    or when a real contact already exists.
    """
    try:
        if not isinstance(cache, dict):
            return cache if isinstance(cache, dict) else {}
        if contact_flag(cache) != "generic":
            return cache
        company = cache.get("company") or {}
        dom = str(company.get("resolved_domain") or "").strip().lower()
        if not dom:
            return cache
        contact = dict(cache.get("contact") or {})
        contact["name"] = ""              # cleared, not "Unknown" -- absence, not a false name
        contact["email"] = f"{_ROLE_LOCALS[0]}@{dom}"
        contact["email_method"] = "role_fallback"
        contact["email_confidence"] = "low"
        contact["contact_verified"] = False
        out = dict(cache)
        out["contact"] = contact
        return out
    except Exception:
        return cache if isinstance(cache, dict) else {}


def research_flag(cache) -> str:
    """"full" | "thin". Never raises."""
    try:
        company = (cache or {}).get("company") or {}
        proofs = [p for p in ((cache or {}).get("proof_points") or []) if p]
        has_what = bool(str(company.get("what_they_do") or "").strip())
        return "full" if (has_what and proofs) else "thin"
    except Exception:
        return "thin"


def draft_confidence(cache) -> dict:
    """{"contact": ..., "research": ...}. What the card shows. Never raises."""
    try:
        return {"contact": contact_flag(cache), "research": research_flag(cache)}
    except Exception:
        return {"contact": "generic", "research": "thin"}
