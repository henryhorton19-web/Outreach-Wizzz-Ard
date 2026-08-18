"""Preconditions for composing a letter (Plan 31, Stage 2).

The system's worst output is not an ugly letter, it is a fluent one built on nothing. Fabrikam ID was
composed from a placeholder contact and a guessed address and produced a well-formed email addressed
to "Hi Unknown,". Every downstream quality mechanism -- voice, guards, variation -- assumes there is
something real to write about. This module is the check that there is.

Blockers are deliberately few and cheap. Each one describes a state where NO letter is better than
the letter the system would otherwise produce. A guessed address is not a blocker, because the
operator can see and correct it; a placeholder human being is, because there is nobody to write to.

App layer only, no I/O, no model call. Never raises.
"""
from __future__ import annotations

# Values research emits when it found nothing. A default in `derive_tokens` cannot catch these:
# the key is present, so the default never fires and the literal string renders into the greeting.
PLACEHOLDER_NAMES = frozenset((
    "", "unknown", "n/a", "na", "tbd", "none", "null", "founder", "founders", "team",
    "ceo", "cto", "hiring manager", "recruiter", "contact", "there", "-",
))
PLACEHOLDER_LOCALS = frozenset(("unknown", "info", "contact", "hello", "team", "founders", "admin"))

MIN_PROOF_POINTS = 1


def blockers_detailed(cache) -> list[dict]:
    """Blockers with the remedy attached: [{"kind", "text"}].

    `kind` is "needs_contact" (a human can supply it in ten seconds) or "needs_research" (only another
    research pass can help). Callers need the distinction to offer the right action: the UI was showing
    "Retry with fresh research" on a missing contact, which spends a call and cannot possibly succeed.

    The two contact conditions -- placeholder name, placeholder address -- are one problem and are
    reported once, because two lines describing one fault reads as two faults. Never raises.
    """
    out: list[dict] = []
    try:
        if not isinstance(cache, dict) or not cache:
            return [{"kind": "needs_research", "text": "no research for this target"}]
        company = cache.get("company") if isinstance(cache.get("company"), dict) else {}
        contact = cache.get("contact") if isinstance(cache.get("contact"), dict) else {}

        name = str(contact.get("name") or "").strip().lower()
        email = str(contact.get("email") or "").strip().lower()
        local = email.split("@", 1)[0] if "@" in email else ""
        no_name = name in PLACEHOLDER_NAMES
        no_addr = bool(local) and local in PLACEHOLDER_LOCALS
        if no_name or no_addr:
            bits = []
            if no_name:
                bits.append("no named contact was found")
            if no_addr:
                bits.append(f"the only address found is a placeholder ({local}@...)")
            out.append({"kind": "needs_contact",
                        "text": " and ".join(bits) + ". Add a contact to continue."})

        if not str(company.get("what_they_do") or "").strip():
            out.append({"kind": "needs_research",
                        "text": "research did not establish what this company does"})

        proofs = cache.get("proof_points")
        if not isinstance(proofs, list) or len([p for p in proofs if p]) < MIN_PROOF_POINTS:
            out.append({"kind": "needs_research",
                        "text": "research returned too few facts to write from"})
    except Exception:
        return out
    return out


def blockers(cache) -> list[str]:
    """String form, kept so existing callers and `pipeline` need no change. Never raises."""
    try:
        return [b["text"] for b in blockers_detailed(cache)]
    except Exception:
        return []
