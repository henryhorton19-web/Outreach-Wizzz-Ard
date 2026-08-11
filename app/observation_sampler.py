"""Verbalized Sampling for the earned observation.

Measured on three real runs, two of three observations opened with the same
frame: "Given [COMPANY]'s rapid expansion, a [key|critical] operational challenge
will [likely] be...". That is mode collapse at the research layer, upstream of
the body, so fixing the body alone would only carry a templated sentence forward
with more prominence.

Asking for K candidates with verbalized probabilities relieves the pressure to
produce the single most typical answer. Selection then deliberately does not take
the highest probability, because the most probable candidate IS the typical one.
"""
from __future__ import annotations

import re
from typing import Any

DEFAULT_K = 3
MAX_WORDS = 32

# Openings seen collapsing in real runs. Used to score candidates down, not to
# reject them: if every candidate is templated the least-bad one still ships.
_TIRED_OPENINGS = (
    "given ", "as a ", "with their ", "a key operational challenge",
    "a critical operational challenge", "they are likely focused",
)

VS_INSTRUCTION = """Generate {k} DIFFERENT candidate observations about this company, each with a mood \
and a probability reflecting how likely you think it is to be the right one.

They must differ in KIND, not just wording. One may be a tension between two things they have committed \
to. One may be a question you would genuinely want answered. One may be something surprising that sits \
in the facts.

Do not begin with "Given", and do not write "a key operational challenge will be". Those are the shapes \
this system produces when it is not really looking.

mood is "tension", "question" or "hypothesis". Prefer a tension between two public commitments; it
states no private problem. If no honest tension exists, prefer a question over a hypothesis.

Never hedge with "must be", "will likely", "probably", "may be" or "might be".

Keep each under {max_words} words.

Return ONLY strict JSON:
{{"candidates":[{{"read":"...","mood":"question","p":0.0}}]}}"""


def sample_observations(system: str, user: str, provider: Any = None,
                        k: int = DEFAULT_K) -> list[dict]:
    """One call, k candidates. [] on any failure."""
    if provider is None or getattr(provider, "is_stub", False):
        return []
    sys_prompt = f"{system}\n\n{VS_INSTRUCTION.format(k=k, max_words=MAX_WORDS)}"
    try:
        res = provider.generate(system=sys_prompt, user=user, use_web=False,
                                temperature=1.0, timeout_s=30)
        from .research import extract_json
        data = extract_json(res.text or "") or {}
    except Exception:
        return []
    out = []
    for c in (data.get("candidates") or []):
        read = str(c.get("read") or "").strip()
        if not read:
            continue
        try:
            p = float(c.get("p") or 0.0)
        except (TypeError, ValueError):
            p = 0.0
        mood = str(c.get("mood") or "hypothesis").strip().lower()
        out.append({"read": read, "mood": mood if mood in ("tension", "question", "hypothesis") else "hypothesis",
                    "p": p})
    return out


def _quality(c: dict) -> float:
    """Higher is better. Probability is a weak input, not the decider, because the
    most probable candidate is the most typical one."""
    read = (c.get("read") or "").strip()
    score = float(c.get("p") or 0.0) * 0.25          # weak tiebreak only

    low = read.lower()
    if any(low.startswith(t) or t in low for t in _TIRED_OPENINGS):
        score -= 1.0                                  # the collapsed shape

    if c.get("mood") == "question":
        score += 0.5                                  # deferential by construction
    elif c.get("mood") == "tension":
        score += 1.0                                  # public commitments, not a diagnosis

    hedges = ("must be", "will likely", "likely be", "is likely", "probably",
              "may be", "might be", "could be", "presumably", "i imagine", "i suspect")
    score -= 0.6 * sum(1 for hedge in hedges if hedge in low)

    words = len(re.findall(r"\S+", read))
    if words > MAX_WORDS:
        score -= 0.5 * ((words - MAX_WORDS) / 10.0)   # verbosity is hedging

    return score


def select_observation(candidates: list[dict]) -> dict:
    """Best candidate by quality. Never returns None when given any candidate:
    if all are templated, the least-bad one still ships."""
    if not candidates:
        return {"read": "", "mood": "", "p": 0.0}
    return max(candidates, key=_quality)
