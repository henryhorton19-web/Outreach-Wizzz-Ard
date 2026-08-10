"""Two-stage candidate-to-company link resolution.

Stage 1 (recall, free): the domain matcher in engine.draft_engine narrows the
experience set to a shortlist. Stage 2 (precision, one cheap LLM call): rerank the
shortlist and articulate the link.

This ordering is deliberate and follows the retrieval literature: a reranker "can
only reorder what retrieval hands it -- treat it as a precision layer on top of a
strong retrieval layer, not a substitute for one." Stage 1 is also what makes
stage 2 cheap: the model sees 3 candidates, not the whole profile.

Everything degrades. A stub provider, a disabled matcher, a failed call, malformed
JSON, an invented experience key, or low confidence all fall back to the keyword
result -- never to a crash and never to an unlinked email.
"""
from __future__ import annotations

from typing import Any

import engine.draft_engine as de
from .research import extract_json

SHORTLIST_LIMIT = 3          # generation accuracy saturates well below this many contexts
MIN_CONFIDENCE = 0.55        # below this, a "strong" claim is downgraded to "weak"
_RANK = {"none": 0, "weak": 1, "strong": 2}

DEFAULT_MATCHER_PROMPT = """You decide whether ONE genuine link exists between a person's background \
and a company. You are not writing an email; you are answering a factual question.

A link is STRONG only when the person has worked on the SAME SUBJECT the company works on -- not a \
similar skill, the same subject. "Both involve data" is not a link. "I built sourcing automation inside \
a private-markets firm, and you sell software to private-markets funds" is a link.

A link is WEAK when the only thing in common is a general capability (analysis, building, ownership).

A link is NONE when there is no honest connection. Returning "none" is a correct, expected answer and \
costs nothing. A manufactured link is worse than no link, because the recipient can check it.

You may ONLY cite experiences from the list you are given, by their exact key.

Return ONLY strict JSON, no prose:
{"link_strength":"strong|weak|none","experience_keys":["key"],"shared_subject":"...",\
"why":"one sentence a human would defend if asked","confidence":0.0}"""


def shortlist(experiences: list[dict], cache: dict, limit: int = SHORTLIST_LIMIT) -> list[dict]:
    """Stage 1. Rank by domain overlap and return the top `limit`."""
    doms = de.target_domains(cache)
    tags = de._target_bridge_tags(cache)
    ranked = sorted(experiences or [],
                    key=lambda e: de.link_score(e, tags, doms), reverse=True)
    return ranked[:max(1, int(limit))]


def _keyword_result(short: list[dict], cache: dict) -> dict:
    """The recall-stage answer, used whenever the precision stage is unavailable."""
    doms = de.target_domains(cache)
    strength = de.link_strength(short, doms)
    keys = [e.get("_key") for e in short if de.domain_overlap(e, doms)] if strength == "strong" else []
    shared = sorted(set(doms) & {d for e in short for d in (e.get("domains") or [])})
    return {"link_strength": strength, "experience_keys": keys,
            "shared_subject": ", ".join(shared), "why": "", "confidence": 0.0,
            "source": "keyword_fallback"}


def resolve_link(cache: dict, experiences: list[dict], provider: Any = None,
                 voice: Any = None) -> dict:
    """Resolve the one link between this person and this company.

    Returns {link_strength, experience_keys, shared_subject, why, confidence, source}.
    `source` is "llm" or "keyword_fallback" so the UI can show which stage answered.
    """
    short = shortlist(experiences, cache)
    fallback = _keyword_result(short, cache)

    if provider is None or getattr(provider, "is_stub", False):
        return fallback

    prompt = DEFAULT_MATCHER_PROMPT
    if voice is not None:
        custom = (getattr(voice, "variables", None) or {}).get("link_matcher_prompt")
        if custom and custom.strip():
            prompt = custom.strip()

    company = cache.get("company") or {}
    lines = [f"COMPANY: {company.get('name','')}",
             f"WHAT THEY DO: {company.get('what_they_do','')}",
             f"SITUATION: {cache.get('situation_read','')}", "", "EXPERIENCES:"]
    for e in short:
        lines.append(f"- key={e.get('_key')} | {e.get('name','')} | {e.get('anchor','')} "
                     f"| domains={','.join(e.get('domains') or [])}")

    try:
        res = provider.generate(system=prompt, user="\n".join(lines),
                                use_web=False, temperature=0.0, timeout_s=25)
        data = extract_json(res.text or "") or {}
    except Exception:
        return fallback

    strength = str(data.get("link_strength") or "").strip().lower()
    if strength not in ("strong", "weak", "none"):
        return fallback

    valid_keys = {e.get("_key") for e in short}
    keys = [k for k in (data.get("experience_keys") or []) if k in valid_keys]
    if strength == "strong" and not keys:
        # cited nothing real -- do not let an invented key become a strong claim
        strength = "weak"

    try:
        conf = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    if strength == "strong" and conf < MIN_CONFIDENCE:
        strength = "weak"

    # THE RECALL CAP. A reranker may reorder and DOWNGRADE what retrieval handed it;
    # it may never promote above it. Without this the model can assert "strong" for a
    # company whose subject matches nothing in the profile -- verified: a bakery
    # returned strong purely because the model said so. That inverts the architecture,
    # turning the precision stage into an unconstrained claim generator.
    recall_strength = fallback["link_strength"]
    if _RANK.get(strength, 0) > _RANK.get(recall_strength, 0):
        strength = recall_strength
        keys = [k for k in keys if k in set(fallback["experience_keys"])]

    return {"link_strength": strength, "experience_keys": keys,
            "shared_subject": str(data.get("shared_subject") or "").strip(),
            "why": str(data.get("why") or "").strip(),
            "confidence": conf, "source": "llm"}
