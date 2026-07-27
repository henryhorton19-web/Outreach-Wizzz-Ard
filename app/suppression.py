"""Suppression / do-not-contact list + archive-aware dedup (Phase 4a).

Closes the real "email someone twice / a dead address" gap: ingest today dedups only against the
queue + active drafts, so a contact already emailed (in the archive / SentItems) or one that has
bounced can be re-queued. This module normalises addresses, checks a persistent suppression list,
and answers "have we already contacted this?" against the sent history.

Invariants:
  * Error-prevention, not silent drop: callers surface the reason ("already contacted",
    "do-not-contact") and offer an "add anyway". Nothing here sends or deletes.
  * Off = today: an empty suppression list + no sent history means every check passes, so ingest
    behaves exactly as before.
  * Every function swallows its own errors — a check must never break an ingest or an approve.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(email: str) -> str:
    """Lowercase, trim, and canonicalise the local part for Gmail-style addresses (strip dots and
    +tags) so 'John.Doe+x@gmail.com' and 'johndoe@gmail.com' compare equal. Non-Gmail domains keep
    their local part verbatim (dots can be significant elsewhere)."""
    e = (email or "").strip().lower()
    if "@" not in e:
        return e
    local, _, domain = e.partition("@")
    if "+" in local:
        local = local.split("+", 1)[0]
    if domain in {"gmail.com", "googlemail.com"}:
        local = local.replace(".", "")
    return f"{local}@{domain}"


def _domain_of(email: str) -> str:
    e = normalize_email(email)
    return e.split("@", 1)[1] if "@" in e else ""


# Universal risky generics — never auto-send to these; flag for a human decision.
RISKY_GENERICS = frozenset({"info", "support", "hello", "contact", "admin", "sales",
                            "team", "office", "hi", "help", "enquiries", "inquiries"})


def is_risky_generic(email: str) -> bool:
    local = normalize_email(email).split("@", 1)[0]
    return local in RISKY_GENERICS


# ---- suppression list ------------------------------------------------------

def _index() -> tuple[set[str], set[str]]:
    """(suppressed_emails, suppressed_domains) — normalised."""
    emails, domains = set(), set()
    for row in store.load_suppressions():
        val = (row.get("value") or "").strip().lower()
        if not val:
            continue
        if "@" in val:
            emails.add(normalize_email(val))
        else:
            domains.add(val.removeprefix("www."))
    return emails, domains


def is_suppressed(email: str) -> tuple[bool, str]:
    """Return (suppressed?, reason). Matches the exact (normalised) address or its whole domain."""
    if not (email or "").strip():
        return False, ""
    emails, domains = _index()
    ne = normalize_email(email)
    if ne in emails:
        row = next((r for r in store.load_suppressions()
                    if normalize_email(r.get("value", "")) == ne), None)
        return True, (row or {}).get("reason", "manual")
    dom = _domain_of(email)
    if dom and dom in domains:
        return True, "domain"
    return False, ""


def add(value: str, reason: str = "manual", source: str = "manual") -> dict:
    """Add an email or a bare domain to the do-not-contact list. Idempotent on the normalised value.
    Returns the stored row. Never raises."""
    try:
        v = (value or "").strip().lower()
        if not v:
            return {}
        v = normalize_email(v) if "@" in v else v.removeprefix("www.")
        items = store.load_suppressions()
        for r in items:
            existing = r.get("value", "").strip().lower()
            existing = normalize_email(existing) if "@" in existing else existing.removeprefix("www.")
            if existing == v:
                return r  # already present
        row = {"value": v, "reason": reason, "source": source, "added_at": _now()}
        items.append(row)
        store.save_suppressions(items)
        return row
    except Exception:
        return {}


def remove(value: str) -> bool:
    try:
        v = (value or "").strip().lower()
        v = normalize_email(v) if "@" in v else v.removeprefix("www.")
        items = store.load_suppressions()
        kept = []
        removed = False
        for r in items:
            existing = r.get("value", "").strip().lower()
            existing = normalize_email(existing) if "@" in existing else existing.removeprefix("www.")
            if existing == v:
                removed = True
                continue
            kept.append(r)
        if removed:
            store.save_suppressions(kept)
        return removed
    except Exception:
        return False


# ---- archive-aware dedup ---------------------------------------------------

def already_contacted_domains() -> set[str]:
    """Domains we have already emailed (approved), from the archive + SentItems. Used to warn on
    re-ingest of a company we've already reached."""
    domains: set[str] = set()
    try:
        for rec in store.load_archive():
            email = ((rec.get("contact") or {}).get("email")) or ""
            d = _domain_of(email)
            if d:
                domains.add(d)
    except Exception:
        pass
    try:
        for si in store.load_sent_items():
            if si.recipient_domain:
                domains.add(si.recipient_domain.lower().removeprefix("www."))
            d = _domain_of(si.sent_to)
            if d:
                domains.add(d)
    except Exception:
        pass
    return domains
