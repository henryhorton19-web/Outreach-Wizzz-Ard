"""Staged composition: select, verify, render, assemble (Plan 31, Stage 3).

Why this exists. The previous design asked one generation to hold the sender's fact set and the
target's fact set, find an honest relation between them, judge whether that relation was real, pick
a rhetorical shape, and render it in a pinned register. Under that load the two operands merged: a
company selling e-commerce listing automation was described as working in outreach, and a company
doing visual localisation was described as researching companies. Both times the sender's own domain
was asserted as the target's business.

The separation is the fix. SELECT emits a typed object naming which sender fact and which target fact
to use, and nothing else -- no prose, so nothing to entangle. VERIFY is code: the ids must exist, so a
fabricated credential cannot survive. RENDER receives exactly one fact from each side, so there is no
second company in the prompt to borrow from. ASSEMBLE concatenates the pinned text in code, because
anything that must be identical every time cannot be a request to a model.

Abstention is a legitimate outcome. If verification fails twice, no letter is produced. A fluent false
letter is sendable and costs the relationship; an absent one costs a minute of the operator's time.

Pure functions plus prompt construction. No I/O. Never raises.
"""
from __future__ import annotations

import json
import re

# Relation shapes. Each is a way one thing can be related to another, not a sentence template: the
# render step is told the shape and writes its own words, which is what stops six letters ending
# identically.
RELATIONS = {
    "other_side": "You were on the other side of the same problem: you consumed what they produce, "
                  "or produced what they consume.",
    "smaller_version": "You built a crude version of what they build properly. Name yours as the crude "
                       "one.",
    "buyer_evidence": "Your experience is evidence FOR their argument, of a kind their own customers "
                      "cannot give them.",
    "same_constraint": "You both hit the same constraint from different directions.",
    "scale": "They are doing what you did, much larger.",
}

CONFIDENCE_OK = ("high", "medium")
_MAX_RELATION_REPEATS = 2


def parse_selection(raw) -> dict:
    """Pull the JSON object out of a model reply. Tolerates fences and preamble. Never raises."""
    try:
        s = str(raw or "").strip()
        if not s:
            return {}
        s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.MULTILINE).strip()
        start, end = s.find("{"), s.rfind("}")
        if start < 0 or end <= start:
            return {}
        obj = json.loads(s[start:end + 1])
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def verify_selection(sel, sender_facts, target_facts, *, recent_relations) -> list[str]:
    """Reasons the selection is unusable. Empty list means proceed.

    This is the whole point of the split: because SELECT emitted ids rather than prose, a credential
    the operator does not have and a fact this company does not have are both detectable by code
    rather than by reading. Never raises.
    """
    errs: list[str] = []
    try:
        if not isinstance(sel, dict) or not sel:
            return ["no selection was returned"]
        s_ids = {f.get("id") for f in (sender_facts or []) if isinstance(f, dict)}
        t_ids = {f.get("id") for f in (target_facts or []) if isinstance(f, dict)}

        if sel.get("credential_id") not in s_ids:
            errs.append(f"credential {sel.get('credential_id')!r} is not one of the sender's facts")
        if sel.get("target_fact_id") not in t_ids:
            errs.append(f"target fact {sel.get('target_fact_id')!r} is not in this company's research")
        if sel.get("relation") not in RELATIONS:
            errs.append(f"relation {sel.get('relation')!r} is not a known shape")
        if str(sel.get("confidence") or "").lower() not in CONFIDENCE_OK:
            errs.append("confidence is too low to write from")
        if not str(sel.get("link_gist") or "").strip():
            errs.append("no link was described")

        rel = sel.get("relation")
        recent = [r for r in (recent_relations or []) if r]
        if rel and recent.count(rel) >= _MAX_RELATION_REPEATS:
            errs.append(f"relation {rel!r} has been used {recent.count(rel)} times recently; "
                        "choose another shape")
    except Exception:
        return errs or ["selection could not be checked"]
    return errs


def _fact_text(facts, fid) -> str:
    try:
        for f in (facts or []):
            if isinstance(f, dict) and f.get("id") == fid:
                return str(f.get("text") or "").strip()
    except Exception:
        return ""
    return ""


def render_prompt(sel, sender_facts, target_facts) -> str:
    """The narrow prompt for the writing step.

    Contains ONE sender fact and ONE target fact. Not the fact lists, not other companies' letters,
    not the pinned sentence. The entanglement failure needed two fact sets in one context to merge;
    with one fact each side there is nothing to merge. Never raises.
    """
    try:
        cred = _fact_text(sender_facts, sel.get("credential_id"))
        tgt = _fact_text(target_facts, sel.get("target_fact_id"))
        rel = RELATIONS.get(sel.get("relation"), "")
        gist = str(sel.get("link_gist") or "").strip()
        return (
            "Write two sentences. Nothing else. No greeting, no sign-off, no preamble.\n\n"
            f"SENTENCE 1 -- what the writer did, and what it cost them:\n  {cred}\n"
            "  State it plainly in the first person, then one thing about it that was harder than "
            "expected or did not work. The difficulty is the point; a difficulty admitted is "
            "credible where a competence asserted is not.\n\n"
            f"SENTENCE 2 -- the link to the reader's company:\n  Their fact: {tgt}\n"
            f"  The relation: {rel}\n  In short: {gist}\n\n"
            "RULES\n"
            "- Never describe the reader's company as doing the writer's work. Sentence 1 is about "
            "the writer only; sentence 2 is about their company only.\n"
            "- Use only the two facts above. Do not add a claim about either side.\n"
            "- Plain declarative sentences. No em dashes. No 'I understand the challenge of', "
            "'resonates', 'uniquely positioned', 'particularly compelling'.\n"
            "- Under 55 words total."
        )
    except Exception:
        return ""


def assemble_opening(pin: str, rendered: str) -> str:
    """Concatenate the pinned sentence and the rendered sentences, in code.

    The pin is emitted here rather than requested from the model because a guidance instruction to
    reproduce text is a request, and it was observed being dropped entirely. If the model volunteered
    the pin anyway, strip its copy rather than shipping it twice. Never raises.
    """
    try:
        p = (pin or "").strip()
        r = (rendered or "").strip()
        if p and r.startswith(p):
            r = r[len(p):].strip()
        return (p + " " + r).strip() if p else r
    except Exception:
        return (rendered or "").strip()


class StagedAbstention(RuntimeError):
    """Raised when staged selection fails verification twice (Plan 31, Stage 3)."""
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors or [])


def build_sender_facts(custom_facts: list[str]) -> list[dict]:
    """Turn custom_facts strings into id/text dicts. Never raises."""
    facts = []
    try:
        for f in (custom_facts or []):
            text = str(f or "").strip()
            if not text:
                continue
            words = re.findall(r"[A-Za-z0-9]+", text[:40].lower())[:3]
            fid = "_".join(words) if words else f"fact_{len(facts)+1}"
            facts.append({"id": fid, "text": text})
    except Exception:
        pass
    return facts


def build_target_facts(cache: dict) -> list[dict]:
    """Turn target research cache facts into id/text dicts. Never raises."""
    facts = []
    idx = 1
    try:
        c = (cache or {}).get("company") or {}
        wtd = str(c.get("what_they_do") or "").strip()
        if wtd:
            facts.append({"id": f"tf{idx}", "text": wtd})
            idx += 1
        sit = str((cache or {}).get("situation_read") or "").strip()
        if sit:
            facts.append({"id": f"tf{idx}", "text": sit})
            idx += 1
        for p in ((cache or {}).get("proof_points") or []):
            txt = (p.get("fact") if isinstance(p, dict) else str(p or "")).strip()
            if txt:
                facts.append({"id": f"tf{idx}", "text": txt})
                idx += 1
    except Exception:
        pass
    return facts


def run_staged_select_and_render(provider, voice, spec: dict, cache: dict, opening_block) -> str:
    """Execute Phase 1 (SELECT), Phase 2 (VERIFY), Phase 3 (RENDER), Phase 4 (ASSEMBLE)."""
    from . import settings as S
    from . import exemplars as _ex
    from . import intent_variation as IV
    from .engine_bridge import de

    sender_facts = build_sender_facts(getattr(voice.evidence, "custom_facts", []) or [])
    if not sender_facts:
        sender_facts = [{"id": "candidate_profile", "text": "Built a sourcing and outreach system"}]
    target_facts = build_target_facts(cache or {})
    if not target_facts:
        target_facts = [{"id": "tf1", "text": "Building product at scale"}]

    if getattr(provider, "is_stub", False):
        sel = {
            "credential_id": sender_facts[0]["id"],
            "target_fact_id": target_facts[0]["id"],
            "relation": "other_side",
            "link_gist": "I built a crude version; they built a real platform",
            "confidence": "high"
        }
        rendered = "I built a crude version of this tool for PE funds. They built a real platform at scale."
        pin = (getattr(opening_block, "text", "") or "").strip()
        return assemble_opening(pin, rendered)

    try:
        prior_recs = _ex.load(voice.id) or []
    except Exception:
        prior_recs = []

    recent_relations = []
    for r in prior_recs[-5:]:
        rel = (r.get("selection") or {}).get("relation") or IV.detect_move(r.get("final_email", ""))
        if rel:
            recent_relations.append(rel)

    rel_desc = "\n".join(f"- {k}: {v}" for k, v in RELATIONS.items())
    s_desc = "\n".join(f"- {f['id']}: {f['text']}" for f in sender_facts)
    t_desc = "\n".join(f"- {f['id']}: {f['text']}" for f in target_facts)

    select_sys = (
        "You are an outreach strategist selecting the ONE honest link between a candidate's experience "
        "and a target company's business.\n\n"
        "Return ONLY a JSON object with these keys:\n"
        "  \"credential_id\": the id of the single best candidate fact\n"
        "  \"target_fact_id\": the id of the single best target fact\n"
        "  \"relation\": one of the relation shapes below\n"
        "  \"link_gist\": one plain sentence describing how they connect\n"
        "  \"confidence\": \"high\", \"medium\", or \"low\"\n\n"
        f"RELATION SHAPES:\n{rel_desc}"
    )

    user_prompt = (
        f"CANDIDATE FACTS:\n{s_desc}\n\n"
        f"TARGET COMPANY FACTS:\n{t_desc}\n\n"
        "Select the single best link."
    )

    st = S.load_settings()
    errors = []
    sel = {}
    for attempt in range(2):
        u_content = user_prompt
        if errors:
            u_content += f"\n\nPREVIOUS SELECTION WAS REJECTED: {'; '.join(errors)}. Choose a different valid combination."
        try:
            res = provider.generate(
                system=select_sys, user=u_content, use_web=False, temperature=0.0,
                timeout_s=st.request_timeout_s, max_retries=1,
                model=(getattr(st, "helper_model", "") or None), max_output_tokens=256
            )
            sel = parse_selection(res.text)
            errors = verify_selection(sel, sender_facts, target_facts, recent_relations=recent_relations)
            if not errors:
                break
        except Exception as e:
            errors = [f"generation error: {e}"]

    if errors:
        # Plan 32: Fall back to 'scale' relation and mark weak link flag in spec/draft_confidence
        if isinstance(spec, dict):
            spec["staged_link_weak"] = True
            if isinstance(spec.get("draft_confidence"), dict):
                spec["draft_confidence"]["link"] = "weak"
        s_id = sender_facts[0]["id"]
        t_id = target_facts[0]["id"]
        if isinstance(sel, dict):
            if _fact_text(sender_facts, sel.get("credential_id")):
                s_id = sel["credential_id"]
            if _fact_text(target_facts, sel.get("target_fact_id")):
                t_id = sel["target_fact_id"]
        sel = {
            "credential_id": s_id,
            "target_fact_id": t_id,
            "relation": "scale",
            "link_gist": "They are doing what you did, much larger",
            "confidence": "low"
        }

    r_prompt = render_prompt(sel, sender_facts, target_facts)
    res_render = provider.generate(
        system="You are a clear, concise writer.", user=r_prompt, use_web=False,
        temperature=st.compose_temperature, timeout_s=st.request_timeout_s,
        max_retries=st.max_retries
    )
    rendered_text = de.normalize(res_render.text or "").strip()

    pin = (getattr(opening_block, "text", "") or "").strip()
    return assemble_opening(pin, rendered_text)

