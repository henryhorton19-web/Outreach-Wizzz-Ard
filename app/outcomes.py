"""Outcome transitions + side effects — the single surface both the automated inbox sweep and
the operator's manual marks call, so "manual == automatic" is structural rather than duplicated.

A send's outcome is `SentItem.reply_state` (awaiting|replied|bounced|bounced_exhausted) plus the
board-only `pipeline_flag` (none|no_response|reopened). This module owns every transition and the
effects each one implies:

  replied  -> pause (dismiss) the pending follow-up cadence for that parent.
  bounced  -> auto-suppress the dead address, then STAGE (never send) a re-draft to the next ladder
              rung — escalating to a DIFFERENT PERSON once the current person's formats are spent.
  awaiting -> correct a false positive: clear detection, and lift ONLY the suppression this bounce
              added (never a manually-added one).
  no_response / reopen -> the board's manual-only flags.

Unlike `voice_stats.record_reply/record_bounce` (which gate on `awaiting` and so cannot correct a
mis-detection), `set_outcome` writes the state directly and is fully reversible. `voice_stats`
folds live over `reply_state`, so there is no separate counter to keep in sync.

Every function swallows its own errors: a stat / suppression / retarget failure must never break a
mark, a sweep, or an approve. Extracted from the original private helpers in `sweep.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import store
from . import suppression as suppression_mod
from . import pipeline
from . import settings as S
from .models import FollowUpStatus, ReplyState


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- follow-up cadence pause (on reply) ------------------------------------

def pause_followup_for(sent_item) -> bool:
    """A reply on an outreach send pauses the pending follow-up cadence for that parent (we don't
    chase someone who answered). Matches by origin_message_id, else by parent slug. Returns True if
    a follow-up was dismissed. Never raises. (Moved verbatim from sweep._pause_followup_for.)"""
    try:
        mid = sent_item.message_id
        parent = sent_item.slug
        changed = False
        for fu in store.load_followups():
            if fu.status not in (FollowUpStatus.pending, FollowUpStatus.drafted):
                continue
            match = (mid and fu.origin_message_id == mid) or (fu.parent_slug == parent)
            if match:
                fu.status = FollowUpStatus.dismissed
                store.upsert_followup(fu)
                changed = True
        return changed
    except Exception:
        return False


# ---- bounce re-draft, person-aware -----------------------------------------

def mark_exhausted(sent_item) -> None:
    try:
        sent_item.reply_state = ReplyState.bounced_exhausted
        store.upsert_sent_item(sent_item)
    except Exception:
        pass


def _current_person(sent_item) -> str:
    """Which person the bounced address belonged to (lowercased), from the stored ladder."""
    to = (sent_item.sent_to or "").strip().lower()
    for c in sent_item.address_candidates:
        if (getattr(c, "email", "") or "").strip().lower() == to:
            return (getattr(c, "person_name", "") or "").strip().lower()
    return ""


def retarget_after_bounce(provider, sent_item, failed_addr: str) -> dict | None:
    """Auto-suppress the dead address, then stage a re-draft to the next ladder rung (approve-first).
    Walks the ranked ladder: the current person's remaining formats first, then a DIFFERENT PERSON
    (an `alt_person` rung), re-addressed to them. Returns {slug, email, person, tier, state} of the
    staged retry, or None if exhausted / no provider / cap hit. Never raises.
    (Moved from sweep._retarget_after_bounce and extended to be person-aware.)"""
    try:
        st = S.load_settings()
        max_retries = int(getattr(st, "max_bounce_retries", 2) or 0)
        dead = (failed_addr or sent_item.sent_to or "").strip().lower()
        if dead:
            suppression_mod.add(dead, reason="bounced", source="inbox")

        # A retry that reuses the approved copy needs no model; only a legacy regenerate does.
        has_approved = bool((getattr(sent_item, "approved_body", "") or "").strip())
        if sent_item.bounce_retry_count >= max_retries:
            mark_exhausted(sent_item)
            return None
        if provider is None and not has_approved:
            mark_exhausted(sent_item)
            return None

        tried = {dead, (sent_item.sent_to or "").strip().lower()}
        # collect any addresses already used across this slug's send history
        for si in store.load_sent_items():
            if si.slug == sent_item.slug and si.sent_to:
                tried.add(si.sent_to.strip().lower())

        cur_person = _current_person(sent_item)

        chosen = None
        for cand in sent_item.address_candidates:
            e = (getattr(cand, "email", "") or "").strip().lower()
            if not e or "@" not in e or e in tried:
                continue
            sup, _ = suppression_mod.is_suppressed(e)
            if sup:
                continue
            chosen = cand
            break
        if chosen is None:
            mark_exhausted(sent_item)
            return None

        next_email = (chosen.email or "").strip().lower()
        cand_person = (getattr(chosen, "person_name", "") or "").strip()
        new_person = None
        if cand_person and cand_person.lower() != cur_person:
            new_person = {"name": cand_person,
                          "title": getattr(chosen, "person_title", "") or "",
                          "confidence": getattr(chosen, "confidence", "low") or "low"}

        n = sent_item.bounce_retry_count + 1
        cs = pipeline.draft_retarget(provider, sent_item, next_email, bounce_n=n,
                                     new_person=new_person)
        # bump the retry count on the bounced SentItem so we don't loop
        sent_item.bounce_retry_count = n
        store.upsert_sent_item(sent_item)
        return {"slug": cs.slug, "email": next_email,
                "person": cand_person or "", "tier": getattr(chosen, "tier", "primary_person"),
                "state": cs.state.value if hasattr(cs.state, "value") else cs.state}
    except Exception:
        return None


# ---- suppression lift on reset (undo a false-positive bounce) --------------

def _lift_bounce_suppression(addr: str) -> bool:
    """Remove a suppression ONLY if it was added by a bounce (reason == 'bounced') and matches
    `addr`. Never touches a manually-added do-not-contact entry. Returns True if one was removed."""
    try:
        if not (addr or "").strip():
            return False
        target = suppression_mod.normalize_email(addr)
        for row in store.load_suppressions():
            val = (row.get("value") or "").strip().lower()
            if "@" not in val:
                continue
            if suppression_mod.normalize_email(val) == target and \
                    (row.get("reason") or "") == "bounced":
                return suppression_mod.remove(addr)
        return False
    except Exception:
        return False


# ---- the manual/auto setter ------------------------------------------------

def _to_state(si, state: ReplyState, source: str, *, clear_detected: bool = False) -> None:
    """Set reply_state directly (NOT gated on `awaiting`, so it can correct a mis-detection),
    stamp detection time + provenance, persist. voice_stats folds live over reply_state."""
    si.reply_state = state
    si.detected_at = None if clear_detected else _now()
    si.outcome_source = source
    store.upsert_sent_item(si)


def set_outcome(sent_id: str, outcome: str, *, provider=None, source: str = "manual") -> dict:
    """Transition one SentItem and (re)apply the side effects its outcome implies. Reversible.
    outcome in {"replied","bounced","no_response","reopen","awaiting"}. Never raises."""
    try:
        si = store.get_sent_item(sent_id)
        if not si:
            return {"ok": False, "error": "unknown send"}
        prev = si.reply_state.value if hasattr(si.reply_state, "value") else si.reply_state
        summary = {"ok": True, "sent_id": sent_id, "prev": prev, "new": outcome, "source": source}

        if outcome == "replied":
            _to_state(si, ReplyState.replied, source)
            summary["followup_paused"] = pause_followup_for(si)

        elif outcome == "bounced":
            _to_state(si, ReplyState.bounced, source)
            retry = retarget_after_bounce(provider, store.get_sent_item(sent_id), si.sent_to)
            summary["retry"] = retry
            summary["exhausted"] = retry is None

        elif outcome in ("no_response", "reopen"):
            si.pipeline_flag = "no_response" if outcome == "no_response" else "reopened"
            si.outcome_source = source
            store.upsert_sent_item(si)

        elif outcome == "awaiting":                       # correct a false positive
            was = prev
            _to_state(si, ReplyState.awaiting, source, clear_detected=True)
            si.pipeline_flag = "none"
            store.upsert_sent_item(si)
            if was in ("bounced", "bounced_exhausted"):
                summary["unsuppressed"] = _lift_bounce_suppression(si.sent_to)
            # Note: does not resurrect a dismissed follow-up (stale due_at). See plan §9.
        else:
            return {"ok": False, "error": f"unknown outcome {outcome}"}
        return summary
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
