"""Automated follow-up orchestration (CRM-style).

Enrolment is event-driven: when an outreach email is approved, `enroll_from_approval` creates a
FollowUp for the NEXT step (if follow-ups are enabled and the sequence is under the cap). The
follow-up's `due_at` chains from the ORIGINAL's approval time using the configured per-step delays
(default 3-7-7). This mirrors the self-scheduling task pattern used by tools like OpenOutreach and
the "action spins up a follow-up task with a due date" pattern in Warmbly/Salesforce.

The actual follow-up email is produced lazily (pipeline.draft_followup) and then flows through the
existing draft -> approve -> stage(.eml) machinery unchanged. This module owns only the pending
follow-up records and the Work-Queue view logic (sort oldest-first, elapsed time, due/overdue).
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from .models import CompanyState, FollowUp, FollowUpStatus
from . import store
from . import settings as S


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _delay_days_for_step(step: int, delays: list[int]) -> int:
    """Delay before follow-up `step` (1-indexed). Reuse the last configured delay beyond the list."""
    if not delays:
        return 3
    idx = min(step - 1, len(delays) - 1)
    return max(0, int(delays[idx]))


def _parent_step_of(slug: str) -> tuple[str, int]:
    """('acme__f2', ...) -> ('acme', 2); ('acme', ...) -> ('acme', 0). The step of the email just
    approved; the follow-up we enrol is step+1."""
    if "__f" in slug:
        base, _, tail = slug.rpartition("__f")
        try:
            return base, int(tail)
        except ValueError:
            return slug, 0
    return slug, 0


def enroll_from_approval(cs: CompanyState, origin_message_id: str = "") -> Optional[FollowUp]:
    """Called right after an outreach email is archived on approval. Create the FollowUp for the
    next step if enabled and under the cap. Returns the created FollowUp, or None if not enrolled.
    Idempotent: never creates a duplicate for the same (parent, step)."""
    st = S.load_settings()
    if not getattr(st, "follow_up_enabled", True):
        return None
    max_steps = int(getattr(st, "follow_up_max_steps", 1) or 0)
    if max_steps <= 0:
        return None

    parent_slug, prev_step = _parent_step_of(cs.slug)
    next_step = prev_step + 1
    if next_step > max_steps:
        return None

    fid = f"{parent_slug}__f{next_step}"
    if store.get_followup(fid) is not None:
        return None  # already enrolled

    approved_at = cs.approved_at or _now().isoformat()
    base_dt = _parse(approved_at) or _now()
    delays = list(getattr(st, "follow_up_delay_days", [3, 7, 7]) or [3, 7, 7])
    due_at = (base_dt + timedelta(days=_delay_days_for_step(next_step, delays))).isoformat()

    contact = (cs.cache or {}).get("contact") or {}
    fu = FollowUp(
        id=fid,
        parent_slug=parent_slug,
        name=cs.name,
        website=cs.website,
        contact_email=(cs.spec or {}).get("send_to") or contact.get("email", "") or "",
        contact_name=contact.get("name", "") or "",
        voice=cs.voice,
        step=next_step,
        original_subject=cs.subject or "",
        original_body=cs.final_email or cs.machine_email or "",
        original_approved_at=approved_at,
        due_at=due_at,
        status=FollowUpStatus.pending,
        origin_message_id=origin_message_id or "",
    )
    store.upsert_followup(fu)
    return fu


# ---------------------------------------------------------------------------
# Work-Queue view helpers
# ---------------------------------------------------------------------------

def elapsed_label(since_iso: str) -> str:
    """Human 'time since original approved' — the number the tab shows on each row."""
    dt = _parse(since_iso)
    if not dt:
        return ""
    secs = (_now() - dt).total_seconds()
    if secs < 0:
        secs = 0
    days = int(secs // 86400)
    if days >= 1:
        return f"{days}d ago"
    hours = int(secs // 3600)
    if hours >= 1:
        return f"{hours}h ago"
    mins = int(secs // 60)
    return f"{mins}m ago" if mins >= 1 else "just now"


def is_due(fu: FollowUp) -> bool:
    due = _parse(fu.due_at)
    return bool(due and _now() >= due)


def due_label(fu: FollowUp) -> str:
    """'due now' / 'due in 2d' — the recommended-timing hint next to elapsed time."""
    due = _parse(fu.due_at)
    if not due:
        return ""
    secs = (due - _now()).total_seconds()
    if secs <= 0:
        return "due now"
    days = int(secs // 86400)
    if days >= 1:
        return f"due in {days}d"
    hours = max(1, int(secs // 3600))
    return f"due in {hours}h"


def public(fu: FollowUp) -> dict:
    """Shape sent to the UI for one follow-up row."""
    return {
        "id": fu.id,
        "parent_slug": fu.parent_slug,
        "name": fu.name,
        "contact_name": fu.contact_name,
        "contact_email": fu.contact_email,
        "step": fu.step,
        "status": fu.status.value if hasattr(fu.status, "value") else fu.status,
        "original_subject": fu.original_subject,
        "original_approved_at": fu.original_approved_at,
        "due_at": fu.due_at,
        "elapsed_label": elapsed_label(fu.original_approved_at),
        "due_label": due_label(fu),
        "is_due": is_due(fu),
        "draft_slug": fu.draft_slug,
    }


def active_sorted() -> list[FollowUp]:
    """Follow-ups still needing action (pending/drafted), sorted OLDEST original-approval FIRST so
    the most overdue float to the top — the Work-Queue ordering the user asked for."""
    items = [f for f in store.load_followups()
             if f.status in (FollowUpStatus.pending, FollowUpStatus.drafted)]
    items.sort(key=lambda f: _parse(f.original_approved_at) or _now())
    return items


def list_public() -> list[dict]:
    return [public(f) for f in active_sorted()]
