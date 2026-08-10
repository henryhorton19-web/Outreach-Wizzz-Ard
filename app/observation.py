"""The single non-transferable thing about this target.

Research retrieves specific material; the writing step is free to ignore it. This
puts the sharpest available detail into a named field that generation is told to
use and that Stage 3 can check was used.

Its own call, because a one-clause observation can be tested for transferability
and a whole email cannot. Per-voice customisable via
voice.variables["observation_prompt"].
"""
from __future__ import annotations

from typing import Any

_EMPTY = {"observation": "", "confidence": 0.0, "source": "none"}

DEFAULT_PROMPT = """You are given research about one company. Name the single most specific, \
non-obvious thing about what they do, or the tension in doing it.

The test: could this sentence be said, unchanged, about most companies in their sector? If yes it is \
worthless. "They are focused on growth" is worthless. "Holding a no-code promise while the agent count \
grows" is not.

Prefer a tension over a fact. A fact is decoration; a tension is something the reader is living with.

Set transferable=true if your own observation fails that test. Saying so is correct and expected: a \
generic observation is worse than none, because it occupies the one line that could have been specific.

Return ONLY strict JSON:
{"observation":"one clause, no preamble","transferable":true|false,"confidence":0.0}"""


def resolve_observation(cache: dict, provider: Any = None, voice: Any = None) -> dict:
    """Return {observation, confidence, source}. Empty is a valid outcome and must
    never break a draft."""
    if provider is None or getattr(provider, "is_stub", False):
        return dict(_EMPTY)

    company = cache.get("company") or {}
    proofs = [p.get("fact", "") if isinstance(p, dict) else str(p)
              for p in (cache.get("proof_points") or [])]
    user = "\n".join([
        f"COMPANY: {company.get('name', '')}",
        f"WHAT THEY DO: {company.get('what_they_do', '')}",
        f"SITUATION: {cache.get('situation_read', '')}",
        "PROOF POINTS:",
        *[f"  - {p}" for p in proofs if p],
    ])

    prompt = DEFAULT_PROMPT
    if voice is not None:
        custom = (getattr(voice, "variables", None) or {}).get("observation_prompt")
        if custom and custom.strip():
            prompt = custom.strip()

    try:
        res = provider.generate(system=prompt, user=user, use_web=False,
                                temperature=0.0, timeout_s=25)
        from .research import extract_json
        data = extract_json(res.text or "") or {}
    except Exception:
        return dict(_EMPTY)

    if data.get("transferable"):
        return dict(_EMPTY)

    text = str(data.get("observation") or "").strip()
    if not text:
        return dict(_EMPTY)
    try:
        conf = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return {"observation": text, "confidence": conf, "source": "llm"}
