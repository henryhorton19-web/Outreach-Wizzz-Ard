"""Deterministic measurements expressed as revision instructions.

The earlier design had these as booleans feeding critique(), which runs after the
email is assembled and whose report nothing acts on, so a verdict changed nothing
about what got written. Here each measurement returns a sentence telling the
generator what to do differently, which Stage 4 feeds back for one revision pass.

These are code, not a model grading its own work, so they are the external signal
that makes refinement reliable. Intrinsic self-critique is documented as
unreliable precisely because a model favours its own output.

Capped at three instructions. A revision prompt carrying ten complaints produces
worse output, not better.
"""
from __future__ import annotations

import re

MAX_NOTES = 3
_SCAFFOLD_N = 7
_SELF = (" i ", " my ", " me ", " myself ", "i've", "i have", "i am", "i'd")
_YOU = (" you ", " your ", " you're", " yours ")


def _ngrams(text: str, n: int) -> set:
    words = re.findall(r"[a-z']+", (text or "").lower())
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def _distinctive(ctx: dict) -> list[str]:
    """Specific material available to this draft that a generic sentence would miss."""
    out = []
    obs = (ctx.get("observation") or "").strip()
    if obs:
        out.append(obs)
    for p in (ctx.get("proof_points") or []):
        s = p.get("fact", "") if isinstance(p, dict) else str(p)
        if s.strip():
            out.append(s.strip())
    sr = (ctx.get("situation_read") or "").strip()
    if sr:
        out.append(sr)
    return out


def _checks_enabled(ctx: dict) -> set:
    """Per-voice: variables["feedback_checks"] is a comma-separated subset of
    scaffold, specifics, balance. Absent or empty means all three."""
    raw = (ctx.get("feedback_checks") or "").strip()
    if not raw:
        return {"scaffold", "specifics", "balance"}
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def feedback_for(body: str, ctx: dict) -> list[str]:
    """Revision instructions for this draft, most important first, capped."""
    notes: list[str] = []
    enabled = _checks_enabled(ctx)

    # 1. Borrowed skeleton. Highest priority: it is what makes an email
    #    reconstructable by the recipient.
    if "scaffold" in enabled:
        grams = _ngrams(body, _SCAFFOLD_N)
        for ex in (ctx.get("style_examples") or []):
            shared = grams & _ngrams(ex, _SCAFFOLD_N)
            if shared:
                notes.append(
                    f"Your opening reuses this exact run from a previous email: "
                    f"{sorted(shared)[0]!r}. Rewrite the first sentence so it shares no phrasing "
                    f"with it. Lead with something different: their situation, or the specific "
                    f"observation, rather than your own background.")
                break

    # 2. Available specifics went unused. Name them, so the instruction is
    #    actionable rather than a complaint.
    if "specifics" in enabled:
        specifics = _distinctive(ctx)
        if specifics:
            low = body.lower()
            stop = {"the", "and", "for", "with", "that", "this", "their", "they", "you", "your",
                    "are", "was", "has", "have", "from", "into", "more", "than", "while",
                    "company", "platform", "business", "growth", "scale", "scaling", "keeping"}
            used = False
            for s in specifics:
                words = [w for w in re.findall(r"[a-z]{5,}", s.lower()) if w not in stop]
                if any(w in low for w in words):
                    used = True
                    break
            if not used:
                notes.append(
                    f"Nothing in this draft is specific to this company. Use this, which research "
                    f"already found and you have not mentioned: {specifics[0]!r}. Replace the most "
                    f"generic sentence with one built on it.")

    # 3. Autobiography balance.
    if "balance" in enabled:
        padded = f" {body.lower()} "
        me = sum(padded.count(m) for m in _SELF)
        you = sum(padded.count(m) for m in _YOU)
        if me and me > (you * 2):
            notes.append(
                f"This draft is mostly about you ({me} references to yourself, {you} to them). "
                f"Cut your background to one clause and give the space to what they are dealing with.")

    return notes[:MAX_NOTES]


def score(body: str, ctx: dict) -> int:
    """Fewer outstanding instructions is better. Used to choose between the
    original and the revision, since refinement is not always an improvement."""
    return -len(feedback_for(body, ctx))
