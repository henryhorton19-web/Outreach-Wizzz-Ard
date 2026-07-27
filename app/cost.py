"""Session cost/token accounting (Phase 1e).

Providers already return token counts on every GenResult. This module prices them against the
per-model table in Settings and accumulates a per-session running total the header meter shows.
It also exposes a per-draft accumulator so the drawer can show a per-target cost.

Unobtrusive by design: pricing a model that isn't in the table yields 0 (never alarming), and every
function swallows its own errors — cost accounting must never break a draft or an approve.
"""
from __future__ import annotations

import threading

from . import store
from . import settings as S

_lock = threading.Lock()
# per-draft accumulator: slug -> {"in","out","cached","cost"}
_draft_acc: dict[str, dict] = {}
# thread-local "which slug is drafting right now" so the deep generate() sites can attribute cost
_ctx = threading.local()


def set_slug(slug: str | None) -> None:
    _ctx.slug = slug


def current_slug() -> str | None:
    return getattr(_ctx, "slug", None)


def price(model: str, tokens_in: int, tokens_out: int, tokens_cached: int = 0) -> float:
    """USD cost of one call. Prices are per 1,000,000 tokens. Unknown model -> 0.0."""
    try:
        table = S.load_settings().cost_prices or {}
        p = table.get(model) or {}
        cin = float(p.get("in", 0.0)) * tokens_in / 1_000_000
        cout = float(p.get("out", 0.0)) * tokens_out / 1_000_000
        ccached = float(p.get("cached", 0.0)) * tokens_cached / 1_000_000
        return round(cin + cout + ccached, 6)
    except Exception:
        return 0.0


def record(model: str, res, *, slug: str | None = None) -> None:
    """Fold one GenResult's token usage into the session total (and per-draft, if slug given).
    Never raises."""
    try:
        ti = int(getattr(res, "input_tokens", 0) or 0)
        to = int(getattr(res, "output_tokens", 0) or 0)
        tc = int(getattr(res, "cached_tokens", 0) or 0)
        if not (ti or to or tc):
            return
        cost = price(model or "", ti, to, tc)
        with _lock:
            s = store.load_session_stats()
            s["in"] = s.get("in", 0) + ti
            s["out"] = s.get("out", 0) + to
            s["cached"] = s.get("cached", 0) + tc
            s["cost"] = round(s.get("cost", 0.0) + cost, 6)
            bm = s.setdefault("by_model", {})
            mrow = bm.setdefault(model or "unknown", {"in": 0, "out": 0, "cached": 0, "cost": 0.0})
            mrow["in"] += ti
            mrow["out"] += to
            mrow["cached"] += tc
            mrow["cost"] = round(mrow["cost"] + cost, 6)
            store.save_session_stats(s)
            if slug:
                a = _draft_acc.setdefault(slug, {"in": 0, "out": 0, "cached": 0, "cost": 0.0})
                a["in"] += ti
                a["out"] += to
                a["cached"] += tc
                a["cost"] = round(a["cost"] + cost, 6)
    except Exception:
        pass


def take_draft(slug: str) -> dict:
    """Pop and return the accumulated per-draft usage for a slug (called at end of draft_one).
    Returns zeros if nothing was recorded. Never raises."""
    try:
        with _lock:
            return _draft_acc.pop(slug, {"in": 0, "out": 0, "cached": 0, "cost": 0.0})
    except Exception:
        return {"in": 0, "out": 0, "cached": 0, "cost": 0.0}


def bump_drafts(n: int = 1) -> None:
    try:
        with _lock:
            s = store.load_session_stats()
            s["drafts"] = s.get("drafts", 0) + n
            store.save_session_stats(s)
    except Exception:
        pass
