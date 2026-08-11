"""Positive revision instructions for the shape of a cold draft."""
from __future__ import annotations

import re

MAX_NOTES = 3
_SELF_ASSESSED = ("could be useful", "would be useful", "could be helpful", "might help",
                  "this blend", "my background combines", "well positioned to", "could add value")
_MENU = re.compile(r"\b\w+,\s+\w+,\s+or\s+\w+", re.I)
_LEARNING = ("curious to learn", "would love to learn", "keen to learn", "how a real enterprise", "learn how you")


def _specifics(ctx: dict) -> list[str]:
    facts = [p.get("fact", "") if isinstance(p, dict) else str(p)
             for p in (ctx.get("proof_points") or [])]
    if ctx.get("observation"):
        facts.append(str(ctx["observation"]))
    return [f.strip() for f in facts if f and f.strip()]


def shape_notes(body: str, ctx: dict) -> list[str]:
    """Return at most three instructions naming what the revision should write."""
    notes, low = [], (body or "").lower()
    if any(phrase in low for phrase in _SELF_ASSESSED):
        notes.append("Replace the self-assessment with the one thing you would do first if you were working there.")
    if _MENU.search(body or ""):
        notes.append("Pick one concrete action rather than listing three ways you could help.")
    if any(phrase in low for phrase in _LEARNING):
        notes.append("Say what you would take off their plate, not what you want to learn from them.")
    specifics = _specifics(ctx)
    if specifics:
        useful = set(re.findall(r"[a-z0-9]{4,}", " ".join(specifics).lower())) - {
            "their", "this", "that", "with", "from", "they", "company", "platform", "business", "growth", "scale"}
        if not useful.intersection(re.findall(r"[a-z0-9]{4,}", low)):
            notes.append(f"Prove you looked by using this researched specific: {specifics[0]!r}.")
    from .opener_check import opener_notes
    from .proposal_check import proposal_notes
    return (opener_notes(body, ctx) + notes + proposal_notes(body, ctx))[:MAX_NOTES]
