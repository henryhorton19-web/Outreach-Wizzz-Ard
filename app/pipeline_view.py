"""Pipeline board assembly (Phase 2).

Derives a read-first Kanban of exactly 6 columns from existing data (queue + drafts + SentItems +
FollowUps). No new state machine: the stage is a pure function of `State` + `reply_state` +
`pipeline_flag`. Manual moves map 1:1 to existing actions (approve, draft follow-up, mark
no-response / reopen); we never fake a drag into an engine-owned column.

The Replied / Bounced columns exist from day one but stay EMPTY until the inbox sweep (Phase 5)
starts transitioning SentItems — no rework when they light up.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import store
from . import settings as S
from .models import State

COLUMNS = ["researching", "drafted", "sent", "replied", "bounced", "no_response"]
COLUMN_LABELS = {
    "researching": "Researching",
    "drafted": "Drafted",
    "sent": "Sent",
    "replied": "Replied",
    "bounced": "Bounced",
    "no_response": "No response",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str | None):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _draft_stage(cs) -> str:
    """A draft/queued CompanyState's column (pre-send)."""
    stt = cs.state.value if hasattr(cs.state, "value") else cs.state
    if stt in ("input", "researched"):
        return "researching"
    if stt == "error":
        return "researching"
    if stt in ("drafted", "in_review", "edited", "approved", "verifying"):
        return "drafted"
    if stt == "ready":
        return "sent"
    return "researching"


def _sent_stage(si) -> str:
    """A SentItem's column from its reply_state + pipeline_flag."""
    rs = si.reply_state.value if hasattr(si.reply_state, "value") else si.reply_state
    if si.pipeline_flag == "no_response":
        return "no_response"
    if rs == "replied":
        return "replied"
    if rs in ("bounced", "bounced_exhausted"):
        return "bounced"
    return "sent"


def _quiet_days(ts: str | None) -> int:
    dt = _parse(ts)
    if not dt:
        return 0
    return max(0, int((_now() - dt).total_seconds() // 86400))


def assemble(list_id: str = "") -> dict:
    """Return {columns: {stage: [cards]}, summary: {...}}. A card is a compact dict the board
    renders with the existing row idiom. list_id="" means every list (all).
    """
    st = S.load_settings()
    stale_days = int(getattr(st, "pipeline_stale_days", 7) or 7)

    cols: dict[str, list] = {c: [] for c in COLUMNS}

    def _matches_list(item_list: str) -> bool:
        if not list_id:
            return True
        if list_id == "unassigned":
            return not item_list
        return item_list == list_id

    # queued (pre-pipeline) + active drafts → Researching / Drafted / Sent(ready)
    seen_slugs: set[str] = set()
    for cs in store.load_drafts():
        seen_slugs.add(cs.slug)
        item_list = getattr(cs, "source_list_id", "")
        if not _matches_list(item_list):
            continue
        stage = _draft_stage(cs)
        contact = (cs.cache or {}).get("contact") or {}
        cols[stage].append({
            "slug": cs.slug, "name": cs.name, "voice": cs.voice or "",
            "source_list_id": item_list,
            "contact": contact.get("name", "") or ((cs.spec or {}).get("send_to", "")),
            "stage": stage, "kind": "draft",
            "quiet_days": _quiet_days(cs.updated_at),
            "stale": False,
            "actions": _draft_actions(cs, stage),
        })

    queue_recs = store.load_queue(list_id=list_id) if (list_id and list_id != "unassigned") else store.load_queue()
    for rec in queue_recs:
        if rec["slug"] in seen_slugs:
            continue
        item_list = rec.get("source_list_id") or list_id
        if not _matches_list(item_list):
            continue
        cols["researching"].append({
            "slug": rec["slug"], "name": rec["name"], "voice": "", "contact": "",
            "source_list_id": item_list,
            "stage": "researching", "kind": "queued", "quiet_days": 0, "stale": False,
            "actions": ["draft"],
        })

    # sent history → Sent / Replied / Bounced / No-response
    items = store.load_sent_items()
    latest_sents = {}
    for si in sorted(items, key=lambda x: x.approved_at or "", reverse=True):
        if si.slug not in latest_sents:
            latest_sents[si.slug] = si

    filtered_sents_count = 0
    for si in latest_sents.values():
        item_list = getattr(si, "source_list_id", "")
        if not _matches_list(item_list):
            continue
        filtered_sents_count += 1
        stage = _sent_stage(si)
        quiet = _quiet_days(si.approved_at)
        cols[stage].append({
            "slug": si.slug, "sent_id": si.id, "name": si.name, "voice": si.voice or "",
            "source_list_id": item_list,
            "contact": si.sent_to, "stage": stage, "kind": "sent",
            "quiet_days": quiet,
            "stale": stage == "sent" and quiet >= stale_days,
            "subject": si.subject,
            "actions": _sent_actions(si, stage),
        })

    # summary funnel
    total_sent = filtered_sents_count
    replied = len(cols["replied"])
    bounced = len(cols["bounced"])
    denom = max(0, total_sent - bounced)
    reply_pct = round(100 * replied / denom) if denom else 0
    summary = {
        "sent": total_sent, "replied": replied, "bounced": bounced,
        "reply_pct": reply_pct,
        "counts": {c: len(cols[c]) for c in COLUMNS},
    }
    return {"columns": cols, "labels": COLUMN_LABELS, "order": COLUMNS, "summary": summary}


def _draft_actions(cs, stage: str) -> list[str]:
    stt = cs.state.value if hasattr(cs.state, "value") else cs.state
    if stage == "drafted" and stt in ("drafted", "edited", "in_review"):
        return ["approve", "open"]
    if stage == "researching" and stt in ("input", "error"):
        return ["draft", "open"]
    return ["open"]


def _sent_actions(si, stage: str) -> list[str]:
    if stage == "sent":
        return ["outcome_menu"]
    if stage == "no_response":
        return ["reopen"]
    if stage == "replied":
        return ["draft_reply"]
    if stage == "bounced":
        return ["view_retry"]
    return []


def mark(slug: str, flag: str) -> bool:
    """Manual user-owned move: mark the latest send for a slug as no_response / reopened. Maps to
    the two states the board lets the operator control by hand. Never raises."""
    try:
        items = store.load_sent_items()
        target = None
        for si in sorted(items, key=lambda s: s.approved_at or "", reverse=True):
            if si.slug == slug:
                target = si
                break
        if not target:
            return False
        if flag == "no_response":
            target.pipeline_flag = "no_response"
        elif flag == "reopen":
            target.pipeline_flag = "reopened"
        else:
            return False
        store.upsert_sent_item(target)
        return True
    except Exception:
        return False
