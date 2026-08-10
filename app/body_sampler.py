"""Verbalized Sampling for the body block.

Asking for one body invites the single most typical answer, which is the
documented cause of mode collapse: alignment concentrates probability mass on
familiar phrasings, so a direct request returns the same scaffold every time.
Asking for K candidates with verbalized probabilities relieves that pressure.

One call, not K calls. The candidates arrive in a single response.
"""
from __future__ import annotations

from typing import Any

DEFAULT_K = 4

VS_INSTRUCTION = """Generate {k} DIFFERENT candidate bodies and a probability for each, \
reflecting how likely you think each is to be right.

They must differ in STRUCTURE, not just wording. Vary what the first sentence does: one may open on \
their situation, one on the observation, one on what you noticed, one on the connection. Do not write \
{k} versions of the same sentence.

Return ONLY strict JSON:
{{"candidates":[{{"body":"...","p":0.0}}]}}"""


def sample_bodies(system: str, user: str, provider: Any = None, k: int = DEFAULT_K) -> list[dict]:
    """One call, k candidates with probabilities. [] on any failure."""
    if provider is None or getattr(provider, "is_stub", False):
        return []
    try:
        res = provider.generate(system=f"{system}\n\n{VS_INSTRUCTION.format(k=k)}", user=user,
                                use_web=False, temperature=1.0, timeout_s=45)
        from .research import extract_json
        data = extract_json(res.text or "") or {}
    except Exception:
        return []
    out = []
    for c in (data.get("candidates") or []):
        body = str(c.get("body") or "").strip()
        if not body:
            continue
        try:
            p = float(c.get("p") or 0.0)
        except (TypeError, ValueError):
            p = 0.0
        out.append({"body": body, "p": p})
    return out
