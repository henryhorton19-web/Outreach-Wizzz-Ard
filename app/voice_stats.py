"""Per-voice outcome statistics (Phase 3) — the learning loop's evidence layer.

Folds `sent_items.json` per voice into {sent, replied, bounced, reply_rate, bounce_rate, Wilson
interval, per-situation buckets}. Reply rate EXCLUDES bounces from the denominator (a bounce is a
dead address, not a non-reply); bounce rate is surfaced separately.

Honesty rules the UI depends on:
  * Never a bare percentage: always paired with n and a Wilson 95% interval.
  * Below `voice_stats_min_n` the rate is "not enough data yet", never a naked %.
  * Until the inbox is connected (Phase 5), `replied` is 0 everywhere, so this honestly reports
    sent counts + "reply rate: not enough data" rather than a misleading 0%.

App layer only; every function swallows its own errors — stats must never break a draft/approve.
"""
from __future__ import annotations

import math

from . import store
from . import settings as S
from . import edit_ledger


def _wilson(pos: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion — honest for small n (unlike normal approx).
    Returns (low, high) in [0, 1]. n == 0 -> (0, 0)."""
    if n <= 0:
        return 0.0, 0.0
    phat = pos / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _situation_of(si) -> str:
    """Coarse situation bucket for a send, if derivable from the stored voice's tags. We don't have
    per-send research here, so bucket by the voice's declared situations (best available signal)."""
    v = store.get_custom_voice(si.voice) if si.voice else None
    sits = getattr(v, "situations", None) or []
    return sits[0] if sits else "unrouted"


def _blank(voice_id: str, name: str) -> dict:
    return {
        "voice": voice_id, "display_name": name,
        "sent": 0, "replied": 0, "bounced": 0,
        "reply_rate": None, "reply_ci": None,   # None until data exists
        "bounce_rate": 0.0,
        "per_situation": {},                     # sit -> {sent, replied, bounced, reply_rate}
    }


def _finalize(bucket: dict, min_n: int) -> dict:
    sent = bucket["sent"]
    replied = bucket["replied"]
    bounced = bucket["bounced"]
    denom = max(0, sent - bounced)               # reply rate excludes bounces
    if denom >= min_n and denom > 0:
        rate = replied / denom
        lo, hi = _wilson(replied, denom)
        bucket["reply_rate"] = rate
        bucket["reply_ci"] = [lo, hi]
        bucket["reply_denom"] = denom
        bucket["enough_data"] = True
    else:
        bucket["reply_rate"] = None
        bucket["reply_ci"] = None
        bucket["reply_denom"] = denom
        bucket["enough_data"] = False
    bucket["bounce_rate"] = (bounced / sent) if sent else 0.0
    return bucket


def rebuild_all(kind: str | None = None) -> dict[str, dict]:
    """Fold every SentItem into per-voice buckets. `kind` filters SentItem.kind
    (outreach|followup); None = all. Returns {voice_id: bucket}."""
    st = S.load_settings()
    min_n = int(getattr(st, "voice_stats_min_n", 15) or 15)

    buckets: dict[str, dict] = {}
    for si in store.load_sent_items():
        if kind and (si.kind or "outreach") != kind:
            # follow-up sends counted under 'followup'; bounce_retry rolls up to outreach
            if not (kind == "outreach" and (si.kind or "outreach") == "bounce_retry"):
                continue
        vid = si.voice or "unrouted"
        if vid not in buckets:
            v = store.get_custom_voice(vid) if si.voice else None
            buckets[vid] = _blank(vid, getattr(v, "display_name", vid) if v else vid)
        b = buckets[vid]
        b["sent"] += 1
        sit = _situation_of(si)
        sb = b["per_situation"].setdefault(sit, {"sent": 0, "replied": 0, "bounced": 0})
        sb["sent"] += 1
        if si.reply_state == "replied" or getattr(si.reply_state, "value", None) == "replied":
            b["replied"] += 1
            sb["replied"] += 1
        elif si.reply_state in ("bounced", "bounced_exhausted") or \
                getattr(si.reply_state, "value", None) in ("bounced", "bounced_exhausted"):
            b["bounced"] += 1
            sb["bounced"] += 1

    for vid, b in buckets.items():
        _finalize(b, min_n)
        for sit, sb in b["per_situation"].items():
            denom = max(0, sb["sent"] - sb["bounced"])
            sb["reply_rate"] = (sb["replied"] / denom) if denom >= min_n and denom > 0 else None
            sb["reply_denom"] = denom
        # edit intensity from the edit ledger (how often the draft was rewritten before approval)
        try:
            b["edit_intensity"] = edit_ledger.edit_intensity(vid)
        except Exception:
            b["edit_intensity"] = None
    return buckets


def record_reply(sent_item_id: str) -> None:
    """Mark a send as replied (called by the inbox sweep). Idempotent; never raises."""
    try:
        from .models import ReplyState
        si = store.get_sent_item(sent_item_id)
        if si and si.reply_state == ReplyState.awaiting:
            from datetime import datetime, timezone
            si.reply_state = ReplyState.replied
            si.detected_at = datetime.now(timezone.utc).isoformat()
            store.upsert_sent_item(si)
    except Exception:
        pass


def record_bounce(sent_item_id: str, exhausted: bool = False) -> None:
    """Mark a send as bounced (called by the inbox sweep). Never raises."""
    try:
        from .models import ReplyState
        si = store.get_sent_item(sent_item_id)
        if si and si.reply_state == ReplyState.awaiting:
            from datetime import datetime, timezone
            si.reply_state = ReplyState.bounced_exhausted if exhausted else ReplyState.bounced
            si.detected_at = datetime.now(timezone.utc).isoformat()
            store.upsert_sent_item(si)
    except Exception:
        pass
