"""Guardrails for Self-Learning (Exemplar) Voices (Plan 26, Stage 6).

Three guardrails prevent model drift, copy-pasting, and runaway divergence:
  1. `leak_notes(body, ctx)`: detects company-specific proper nouns from other targets;
  2. `novelty_notes(body, recent_emails, max_overlap)`: caps n-gram overlap with recent emails;
  3. `should_freeze(effort_series, window)`: detects rising user effort over consecutive turns.

Also provides `merge_extra(notes, ctx)` to combine standard feedback notes with exemplar guards.
Pure logic, standard library only (re, difflib). App layer, never raises.
Deliberately outcome-free: no reply_state / bounce signal.
"""
from __future__ import annotations

import difflib
import re

from . import settings as S


def leak_notes(body: str, ctx: dict) -> list[str]:
    """Flag improper company name leaks or foreign company proper nouns. Never raises."""
    try:
        notes: list[str] = []
        if not body or not ctx:
            return []

        target_co = (ctx.get("company") or "").strip()
        body_txt = body.lower()

        # Common company names that might leak if copy-pasted
        KNOWN_PROPER_NOUNS = {"stripe", "adyen", "celonis", "personio", "revolut", "klarna",
                              "gocardless", "qonto", "spendesk", "payfit", "alan", "ledger"}

        for noun in KNOWN_PROPER_NOUNS:
            if noun in body_txt and noun != target_co.lower():
                notes.append(f"Foreign company reference detected: '{noun}' is not the target company ({target_co}).")
        return notes
    except Exception:
        return []


def novelty_notes(candidate: str, recent_emails: list[str], max_overlap: float = 0.72) -> list[str]:
    """Flag excessive n-gram / text overlap with recent emails to maintain novelty. Never raises."""
    try:
        notes: list[str] = []
        cand_str = (candidate or "").strip()
        if not cand_str or not recent_emails:
            return []

        for prev in recent_emails:
            prev_str = (prev or "").strip()
            if not prev_str:
                continue
            ratio = difflib.SequenceMatcher(None, cand_str, prev_str).ratio()
            if ratio > max_overlap:
                notes.append(f"High text overlap ({round(ratio * 100)}%) with a recent sent email exceeds maximum allowed ({round(max_overlap * 100)}%).")
                break
        return notes
    except Exception:
        return []


def should_freeze(effort_series: list[float], window: int = 4) -> tuple[bool, str]:
    """Check if effort is strictly rising over the last `window` turns, indicating convergence failure.

    Returns `(should_freeze, reason)`. Never raises.
    """
    try:
        if not effort_series or len(effort_series) < window:
            return False, ""

        recent = effort_series[-window:]
        # Check if effort is strictly increasing in this window
        is_rising = all(recent[i] < recent[i + 1] for i in range(len(recent) - 1))
        if is_rising and recent[-1] >= 0.40:
            return True, f"User edit effort rose continuously over the last {window} turns ({recent[0]} -> {recent[-1]})."
        return False, ""
    except Exception:
        return False, ""


def merge_extra(notes: list[str], ctx: dict) -> list[str]:
    """Merge standard draft feedback notes with exemplar leak guard notes. Never raises."""
    try:
        st = S.load_settings()
        max_notes = int(getattr(st, "max_notes", 3) or 3)
        extra = leak_notes(ctx.get("body", "") or "", ctx)
        combined = (notes or []) + extra
        return combined[:max_notes]
    except Exception:
        return notes or []
