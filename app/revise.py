"""One revision pass, driven by deterministic feedback.

Generate, measure, revise, keep the better one. Capped at a single pass: the
literature is explicit that refinement is not monotonic and that refined output
is not always superior, which argues for a stopping rule rather than looping until
a critic is satisfied.

Always returns a usable body. A failure, a stub provider, or a revision that
scores worse all yield the original.
"""
from __future__ import annotations

from typing import Any

from . import draft_feedback

REVISE_SYSTEM = """You are revising one paragraph of a cold outreach email. You will be given the \
current draft and specific instructions about what to change.

Follow the instructions exactly. Change only what they ask about; keep every sentence that is already \
specific and true. Do not add new claims, do not invent facts, and do not make the paragraph longer.

Return ONLY the revised paragraph, no preamble and no explanation."""


def revise_if_needed(body: str, ctx: dict, provider: Any = None) -> str:
    """Return the better of the original and one revision. Never returns empty."""
    notes = draft_feedback.feedback_for(body, ctx)
    if not notes:
        return body                                  # nothing to fix; do not spend a call
    if provider is None or getattr(provider, "is_stub", False):
        return body

    instructions = "\n".join(f"{i}. {n}" for i, n in enumerate(notes, 1))
    user = f"CURRENT DRAFT:\n{body}\n\nINSTRUCTIONS:\n{instructions}"
    try:
        res = provider.generate(system=REVISE_SYSTEM, user=user, use_web=False,
                                temperature=0.4, timeout_s=40)
        revised = (res.text or "").strip()
    except Exception:
        return body
    if not revised:
        return body

    # Refinement is not monotonic. Keep whichever version has fewer outstanding
    # instructions; on a tie prefer the revision, written knowing more.
    if draft_feedback.score(revised, ctx) >= draft_feedback.score(body, ctx):
        return revised
    return body
