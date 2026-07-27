"""Inbox sweep orchestration (Phase 5 + 6b).

Given an iterable of raw RFC822 messages and a provider, classify each (detect.py) and apply the
effects, all approve-first / suggest-don't-act:

  reply  -> mark the SentItem replied, PAUSE the parent follow-up cadence, feed voice-stats.
  bounce -> mark the SentItem bounced, feed voice-stats, AUTO-SUPPRESS the dead address, and STAGE
            (not send) a re-draft to the next ladder rung as a normal approvable draft — escalating
            to a DIFFERENT PERSON once the current person's formats are spent. Exhaustion
            (cap hit or ladder end) -> bounced_exhausted + a banner, no draft.

The effects themselves live in `outcomes.py` so the automated sweep and the operator's manual marks
(`/api/sent/{id}/outcome`) run the SAME code — "manual == automatic" is structural. Separated from
inbox.py so it is fully testable with canned fixtures and a fake mailbox (a list of raw bytes) — no
live IMAP server. Every effect swallows its own errors; a sweep must never crash the app.
"""
from __future__ import annotations

from . import detect
from . import store
from . import voice_stats as voice_stats_mod
from . import outcomes as outcomes_mod


def run(raw_messages, provider=None) -> dict:
    """Classify + apply effects for a batch of raw messages. Returns a summary the UI shows as a
    toast: {replied, bounced, retries[], exhausted, scanned}. `provider` is optional — without it,
    bounces are recorded + suppressed but no retry is staged."""
    sent_items = store.load_sent_items()
    index = detect.build_sent_index(sent_items)
    by_id = {si.id: si for si in sent_items}

    replied = bounced = 0
    retries = []
    exhausted = 0
    scanned = 0

    for raw in raw_messages:
        scanned += 1
        try:
            result = detect.classify(raw, index)
        except Exception:
            continue
        kind = result.get("kind")
        if kind == "reply":
            sid = result.get("sent_id")
            si = by_id.get(sid)
            if not si:
                continue
            voice_stats_mod.record_reply(sid)
            outcomes_mod.pause_followup_for(si)
            replied += 1
        elif kind == "bounce":
            failed = (result.get("failed_recipient") or "").lower()
            # find which awaiting SentItem this bounce is for: by failed recipient address
            target = None
            for si in sent_items:
                rs = getattr(si.reply_state, "value", si.reply_state)
                if rs != "awaiting":
                    continue
                if failed and (si.sent_to or "").lower() == failed:
                    target = si
                    break
            if target is None and failed:
                # domain-level fallback
                dom = failed.split("@", 1)[1] if "@" in failed else ""
                for si in sent_items:
                    rs = getattr(si.reply_state, "value", si.reply_state)
                    if rs == "awaiting" and dom and (si.recipient_domain or "").lower() == dom:
                        target = si
                        break
            if target is None:
                continue
            voice_stats_mod.record_bounce(target.id)
            bounced += 1
            # re-load: record_bounce persisted reply_state=bounced; use the fresh copy so the
            # retry-count bump below doesn't clobber that transition with a stale in-memory row.
            fresh = store.get_sent_item(target.id) or target
            retry = outcomes_mod.retarget_after_bounce(provider, fresh, failed)
            if retry:
                retries.append(retry)
            else:
                exhausted += 1
    return {"replied": replied, "bounced": bounced, "retries": retries,
            "exhausted": exhausted, "scanned": scanned}
