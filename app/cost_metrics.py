"""Unit economics for session cost.

Total spend is a vanity metric: it falls when you simply do less work. Cost per
draft is comparable across sessions, voices and model changes, and only falls
when a draft genuinely gets cheaper.

Cost per approved draft is the sharper figure, since it divides by outcomes
rather than attempts and so counts the money spent on drafts that were never
good enough to send. It lags, though, because a draft can sit unapproved for
days, so it belongs in the detail view rather than the headline.
"""
from __future__ import annotations


def compute_metrics(stats: dict, approved: int = 0) -> dict:
    """Derive unit economics from a session-stats dict.

    Returns every field the UI needs, including the raw totals, so the client
    performs no arithmetic and carries none of the edge cases.
    """
    stats = stats or {}
    try:
        cost = float(stats.get("cost") or 0.0)
    except (TypeError, ValueError):
        cost = 0.0
    try:
        drafts = int(stats.get("drafts") or 0)
    except (TypeError, ValueError):
        drafts = 0
    try:
        approved = int(approved or 0)
    except (TypeError, ValueError):
        approved = 0

    return {
        "cost": cost,
        "drafts": drafts,
        "approved": approved,
        "per_draft": (cost / drafts) if drafts > 0 else 0.0,
        "per_approved": (cost / approved) if approved > 0 else 0.0,
        # Research and contact resolution can spend before any draft exists. That
        # is real money, and a meter reporting 0.0000 per draft while a total sits
        # above zero would be misleading, so the UI is told about it explicitly.
        "has_unattributed_spend": bool(cost > 0 and drafts == 0),
        "in": int(stats.get("in") or 0),
        "out": int(stats.get("out") or 0),
        "cached": int(stats.get("cached") or 0),
        "by_model": stats.get("by_model") or {},
    }
