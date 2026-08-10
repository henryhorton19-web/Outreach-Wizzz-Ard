"""Paris outreach draft engine (deterministic core).

Mirrors the HPE engine's contract and discipline, re-aimed for candidate outreach:
  * prepare(cache, voice_name)      -> spec (frame slots + the profile TIE + provenance)
  * writer_brief(spec)              -> the slim brief handed to compose
  * finalize(spec, parts)           -> {email, report, ...} assembled machine draft
  * critique(body, ask, spec)       -> Report(hard, soft) honesty gates (advisory)
  * mock_email(spec)                -> deterministic offline body+ask (stub path / tests)
  * normalize(text)                 -> dash + whitespace normalisation
  * render_research_detail(cache)   -> provenance/routing audit block

The engine never calls a model and never touches the web. Its job: pick which candidate-profile
evidence answers this target (the "tie"), supply the frame verbatim, and guarantee provenance —
no number or name reaches an email unless it traces to the target's sourced points or the fixed
candidate profile.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import config as C


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _first_name(full: str) -> str:
    full = (full or "").strip()
    if not full:
        return "there"
    # drop honorifics
    parts = [p for p in re.split(r"\s+", full) if p]
    HON = {"dr", "mr", "ms", "mrs", "prof", "mr.", "ms.", "dr."}
    while parts and parts[0].lower().strip(".") in {h.strip(".") for h in HON}:
        parts = parts[1:]
    return parts[0] if parts else "there"


_DASHES = ["\u2014", "\u2013", "\u2012", "\u2015", "--"]


def normalize(text: str, keep_dashes: bool = False) -> str:
    """Collapse runaway whitespace. Replaces dashes with commas unless keep_dashes.

    keep_dashes must be driven by the voice's allow_dashes flag. Stripping dashes
    here unconditionally made that flag a no-op: the compose prompt permitted them,
    this function removed them, and critique() then flagged them.
    """
    if not text:
        return ""
    out = text
    if not keep_dashes:
        for d in _DASHES:
            out = out.replace(d, ", ")
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = re.sub(r" *, *,", ",", out)
    return out.strip()


def _join_names(n: list[str]) -> str:
    if not n:
        return ""
    if len(n) == 1:
        return n[0]
    if len(n) == 2:
        return f"{n[0]} and {n[1]}"
    return ", ".join(n[:-1]) + f" and {n[-1]}"


# ---------------------------------------------------------------------------
# the profile TIE: choose which candidate evidence answers this target
# ---------------------------------------------------------------------------

# Map loose research signals to profile bridge tags.
def _target_bridge_tags(cache: dict) -> list[str]:
    tags: list[str] = []
    c_prof = cache.get("company_profile") or {}
    r_prof = cache.get("role_profile") or {}
    t_prof = cache.get("target_profile") or {}
    recent_pt = cache.get("recent_point") or {}
    recent_detail = recent_pt.get("detail", "") if recent_pt.get("present", True) else ""
    text = " ".join([
        (cache.get("company") or {}).get("what_they_do", ""),
        c_prof.get("description", ""),
        c_prof.get("industry", ""),
        r_prof.get("title", ""),
        r_prof.get("department", ""),
        t_prof.get("role_title", ""),
        " ".join(p.get("fact", "") for p in (cache.get("proof_points") or []) if isinstance(p, dict)),
        recent_detail,
        (cache.get("situation_read") or ""),
    ]).lower()

    def has(*words):
        return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words)

    if has("raise", "raised", "funding", "seed", "series", "round", "investor", "fundrais"):
        tags += ["fundraising", "investor_adjacent"]
    if has("ai", "llm", "model", "ml", "genai", "agent"):
        tags += ["ai_native", "fintech_ai", "technical"]
    if has("fintech", "capital markets", "trading", "asset management", "bank", "payments"):
        tags += ["fintech_ai", "analytical"]
    if has("data", "analytics", "metrics", "revenue", "growth", "arr", "unit econ"):
        tags += ["analytical", "research"]
    if has("ops", "operations", "revops", "bizops", "process", "tooling", "automation"):
        tags += ["ops", "builds"]
    if has("early", "pre-seed", "founding", "first hire", "zero to one"):
        tags += ["zero_to_one", "ownership"]
    # default: always fine to bridge analytical/builds (the spine)
    tags += ["analytical", "builds"]
    # de-dup, preserve order
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def target_domains(cache: dict) -> list[str]:
    """Extract domain keywords from company what_they_do, situation_read, proof_points."""
    text = " ".join([
        (cache.get("company") or {}).get("what_they_do", ""),
        (cache.get("situation_read") or ""),
        " ".join(p.get("fact", "") for p in (cache.get("proof_points") or []) if isinstance(p, dict)),
    ]).lower()
    doms = []
    def has(*words):
        return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words)

    if has("private markets", "private market", "private equity", "growth equity", "venture capital", "deal flow", "fund", "funds"):
        doms.append("private_markets")
    if has("sourcing", "outreach", "deal sourcing", "pipeline", "dealflow"):
        doms.append("sourcing_automation")
    if has("saas", "arr", "metrics", "financial", "valuation", "diligence"):
        doms.append("saas_metrics")
    if has("policy", "think tank", "research", "governance"):
        doms.append("policy")
    if has("ai", "llm", "automation", "genai"):
        doms.append("automation")
    return doms


def domain_overlap(exp: dict, doms: list[str]) -> bool:
    """True if experience's domains overlap with target domains."""
    exp_doms = set(exp.get("domains") or [])
    return bool(exp_doms & set(doms))


def link_score(exp: dict, tags: list[str], doms: list[str]) -> int:
    """Score experience against bridge tags and domain keywords."""
    score = 0
    bridges = set(exp.get("bridges") or [])
    exp_doms = set(exp.get("domains") or [])
    score += len(bridges & set(tags or []))
    score += 2 * len(exp_doms & set(doms or []))
    return score


def link_strength(short: list[dict], doms: list[str], target_tags: list[str] = None) -> str:
    """Recall-stage link strength: 'strong' if domain overlap, 'weak' if specific bridge tag overlap, else 'none'."""
    if not short:
        return "none"
    if doms:
        for e in short:
            if domain_overlap(e, doms):
                return "strong"
    # Specific bridge tags: ignore fallback ['analytical', 'builds'] when no specific keywords matched
    specific_tags = [t for t in (target_tags or []) if t not in ("analytical", "builds")]
    if specific_tags:
        tt_set = set(specific_tags)
        for e in short:
            if (e.get("_score", 0) > 0) or (set(e.get("bridges") or []) & tt_set):
                return "weak"
    return "none"


def rank_evidence(cache: dict, prefer=(), pin=(), exclude=(), weights=None) -> list[dict]:
    """Full voice-tilted ranking of candidate experiences. Deterministic: bridge-tag overlap with
    the target, HPE's standing nudge, plus voice tilts (prefer +2, category_weights per bridge);
    pinned float to the top; excluded and signal-gated optionals drop out. Each returned exp dict
    carries _key, _score, _pinned. The tie (select_evidence) and the {relevant} shortlist read this."""
    weights = weights or {}
    prefer = set(prefer or ())
    pin = set(pin or ())
    exclude = set(exclude or ())
    tags = set(_target_bridge_tags(cache))
    exps = C.CANDIDATE_PROFILE["experiences"]
    ranked = []
    for key, exp in exps.items():
        if key in exclude:
            continue
        pinned = key in pin
        bridges = set(exp.get("bridges", []))
        if exp.get("optional") and not pinned and key not in prefer:
            # optional experiences (e.g. Innova) only surface when the target rewards them
            if not (tags & {"ownership", "zero_to_one", "ops"}):
                continue
        score = len(tags & bridges)
        standing = C.CANDIDATE_PROFILE.get("standing_key", "hpe")
        if key == standing:
            score += 1
        if key in prefer:
            score += 2
        score += sum(int(weights.get(b, 0)) for b in bridges)
        ranked.append(dict(exp, _key=key, _score=score, _pinned=pinned))
    ranked.sort(key=lambda e: (1 if e["_pinned"] else 0, e["_score"]), reverse=True)
    return ranked


def select_evidence(cache: dict, prefer=(), pin=(), exclude=(), weights=None, count=2) -> list[dict]:
    """The tie: the top `count` experiences to weave into the narrative. Keeps only positively-scored
    or pinned experiences so a weak-signal target does not drag in irrelevant evidence; falls back to
    the speakable anchor when nothing scores."""
    ranked = rank_evidence(cache, prefer, pin, exclude, weights)
    picked = [e for e in ranked if e["_score"] > 0 or e["_pinned"]][:max(1, int(count or 2))]
    if not picked:
        exps = C.CANDIDATE_PROFILE["experiences"]
        fk = "solano" if "solano" in exps else next(iter(exps))
        picked = [dict(exps[fk], _key=fk, _score=0, _pinned=False)]
    return picked


def _select_evidence(cache: dict, voice_name: str = None) -> list[dict]:
    """Back-compat wrapper (prepare and the engine's own tests call this): default tie, voice-blind.
    The app uses select_evidence / rank_evidence with the voice's preferences instead."""
    return select_evidence(cache, count=2)


# ---------------------------------------------------------------------------
# prepare: cache -> spec
# ---------------------------------------------------------------------------

_EMPTY_VOICE = {"greeting": "", "opening_fallback": "", "subject": "", "ask": "", "lead": ""}


def _resolve_voice(voice_name: str) -> tuple[str, dict]:
    # Voice frame content now lives in the editable store (app layer), not here: the app supplies
    # every frame slot after prepare(), so the engine only needs a neutral, empty frame and no
    # longer reads any static voice definition. Kept tolerant so prepare() cannot KeyError whether
    # or not a legacy config.VOICES is present.
    voices = getattr(C, "VOICES", {}) or {}
    vc = voices.get(voice_name)
    if vc is None:
        return (voice_name or C.DEFAULT_VOICE), dict(_EMPTY_VOICE)
    return voice_name, vc


def prepare(cache: dict, voice_name: str = None) -> dict:
    """Build the spec: frame slots (verbatim), the selected profile evidence (the tie), the
    recipient, and the provenance material the gates use."""
    voice_name = voice_name or C.DEFAULT_VOICE
    vid, v = _resolve_voice(voice_name)

    company = cache.get("company") or {}
    name = company.get("name", "the company")
    contact = cache.get("contact") or {}
    contact_first = _first_name(contact.get("name", ""))
    role_title = company.get("role_title", "")
    role_or_company = role_title or name

    recent = cache.get("recent_point") or {}
    has_recent = bool(recent.get("present") and recent.get("detail"))

    evidence = _select_evidence(cache, vid)

    # allowed facts = target proof points + recent + the selected candidate anchors/facts
    allowed_facts = []
    for p in (cache.get("proof_points") or []):
        if isinstance(p, dict) and p.get("fact"):
            allowed_facts.append({"text": p["fact"], "source": p.get("source", ""), "about": "target"})
    if has_recent:
        allowed_facts.append({"text": recent["detail"], "source": recent.get("source", ""), "about": "target"})
    for exp in evidence:
        allowed_facts.append({"text": exp["anchor"], "source": "candidate_profile", "about": "candidate"})

    spec = {
        "voice": vid,
        "company": name,
        "role_title": role_title,
        "role_or_company": role_or_company,
        "greeting": v["greeting"].replace("{name}", contact_first),
        "opening_fallback": v["opening_fallback"].replace("{company}", name).replace(
            "{role_or_company}", role_or_company),
        "subject": v["subject"].replace("{company}", name).replace("{role_or_company}", role_or_company),
        "ask": v["ask"],
        "lead": v["lead"],
        "send_to": (cache.get("contact") or {}).get("email", ""),
        "contact_name": contact.get("name", ""),
        "contact_first": contact_first,
        # the tie: what the compose brief must weave in (candidate side)
        "evidence": [{"name": e["name"], "anchor": e["anchor"], "bridges": e.get("bridges", [])}
                     for e in evidence],
        "spine": C.CANDIDATE_PROFILE["spine"],
        "recent": {"present": has_recent, "detail": recent.get("detail", ""),
                   "kind": recent.get("kind", "other")},
        "situation_read": cache.get("situation_read", ""),
        "proof_points": [p.get("fact", "") for p in (cache.get("proof_points") or []) if isinstance(p, dict)],
        "allowed_facts": allowed_facts,
        "candidate_name": C.CANDIDATE_PROFILE["name"],
        "link": cache.get("candidate_link") or {},
        "link_strength": (cache.get("candidate_link") or {}).get("link_strength", "none"),
    }
    return spec


# ---------------------------------------------------------------------------
# writer_brief: the slim brief handed to compose
# ---------------------------------------------------------------------------

def writer_brief(spec: dict) -> dict:
    """Only the facts and directives compose needs — never the whole cache."""
    return {
        "company": spec["company"],
        "role": spec.get("role_title") or None,
        "recipient_first_name": spec.get("contact_first", "there"),
        "voice": spec["voice"],
        "lead_style": spec["lead"],
        "the_why_now": spec["recent"]["detail"] if spec["recent"]["present"] else "",
        "situation_read": spec.get("situation_read", ""),
        "target_proof_points": spec.get("proof_points", []),
        "candidate_evidence_to_tie_in": [e["anchor"] for e in spec.get("evidence", [])],
        "candidate_spine": spec["spine"],
        "ask_line": spec["ask"],
        "allowed_facts": [f["text"] for f in spec.get("allowed_facts", [])],
        "hard_rules": [
            "70-120 words, phone-readable.",
            "No dashes of any kind; commas or full stops only.",
            "Lead with the deliverable/read, not a CV recital.",
            "Do NOT recite what the company does back to the founder.",
            "No sign-off (the mail client appends the signature).",
        ],
    }


# ---------------------------------------------------------------------------
# finalize: assemble the machine draft
# ---------------------------------------------------------------------------

def _opening_line(spec: dict) -> str:
    """The opener: from the recent point when present, else the voice fallback."""
    if spec["recent"]["present"]:
        det = spec["recent"]["detail"].strip().rstrip(".")
        kind = spec["recent"].get("kind")
        if kind == "raise":
            return f"Congratulations on {det}."
        return f"I saw {det}, which is what prompted me to write."
    return spec["opening_fallback"]


def finalize(spec: dict, parts: dict) -> dict:
    """Assemble greeting + opening + body + ask into the email, normalise
    dashes on the machine text, and run critique. The body is the composed text; the ask/close is
    the voice's fixed ask."""
    keep_dashes = bool(spec.get("allow_dashes", False))
    body = normalize((parts.get("body") or "").strip(), keep_dashes=keep_dashes)
    greeting = spec["greeting"].strip()
    opening = normalize(_opening_line(spec), keep_dashes=keep_dashes)
    ask_block = normalize(spec["ask"], keep_dashes=keep_dashes)

    blocks = [greeting, opening, body, ask_block]
    email = "\n\n".join(b for b in blocks if b.strip())

    rep = critique(body, ask_block, spec)
    return {
        "email": email,
        "body": body,
        "machine_body": body,
        "ask": ask_block,
        "opening": opening,
        "greeting": greeting,
        "report": rep.to_dict(),
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# critique: honesty gates (advisory; nothing blocks)
# ---------------------------------------------------------------------------

@dataclass
class Report:
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"hard": self.hard, "soft": self.soft}


_NUM_RE = re.compile(r"(?<![\w.])(\$?\d[\d,]*(?:\.\d+)?\s*[kKmMbB%]?)\b")


def _num_tokens(text: str) -> set[str]:
    """Extract number-like tokens for comparison against a fact set."""
    return {m.group(1).strip().lower().replace(",", "") for m in _NUM_RE.finditer(text or "")}


def numeric_guard(text: str, facts: list[dict], allowed_numbers: set[str] | None = None) -> list[str]:
    """Reject any figure in `text` that is not present in `facts` or `allowed_numbers`."""
    allowed_numbers = allowed_numbers or set()
    fact_text = " ".join(str(f.get("fact") or "") for f in (facts or []))
    fact_numbers = _num_tokens(fact_text) | allowed_numbers
    hits = []
    for tok in _num_tokens(text):
        if tok not in fact_numbers:
            hits.append(f"unsourced figure: {tok}")
    return hits


def find_unauthorized_commitments(text: str) -> list[str]:
    patterns = [r"\bi (can |will )?guarantee\b", r"\bi promise\b", r"\bwe('ll| will) definitely\b",
                r"\bi('ll| will) make sure\b.*\bwithin\b"]
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def find_unearned_superlatives(text: str) -> list[str]:
    words = [r"\bunmatched\b", r"\bunparalleled\b", r"\bthe best\b", r"\bindustry[- ]leading\b",
             r"\bworld[- ]class\b", r"\bbest[- ]in[- ]class\b"]
    return [w for w in words if re.search(w, text, re.IGNORECASE)]


def find_dramatised_opener(text: str) -> list[str]:
    first_two = ". ".join(text.strip().split(". ")[:2])
    if re.search(r"\bit'?s not\b.{0,40}\bit'?s\b", first_two, re.IGNORECASE):
        return ["dramatised opener"]
    return []


def find_unallowed_precedent(text: str, spec: dict) -> list[str]:
    allowed = set(spec.get("precedent_ids") or [])
    named = set(spec.get("precedent_names_in_pool") or [])
    hits = []
    for name in named - allowed:
        if name and name.lower() in text.lower():
            hits.append(f"named an unselected precedent: {name}")
    return hits


def find_identity_mechanics_leak(text: str, spec: dict) -> list[str]:
    patterns = spec.get("identity_leak_patterns") or []
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def critique(body: str, ask: str, spec: dict) -> Report:
    r = Report()
    text = f"{body}\n{ask}".strip()
    low = text.lower()

    # no dashes, unless this voice allows them
    if not spec.get("allow_dashes", False) and any(d in text for d in _DASHES):
        r.hard.append("em dash")

    # forbidden cliches
    for ph in C.FORBIDDEN_PHRASES:
        if ph in low:
            r.hard.append(f"forbidden: {ph}")

    # sign-off in body/ask
    for mk in C.SIGNOFF_MARKERS:
        if mk in low:
            r.hard.append(f"sign-off in body/ask: {mk.strip()}")

    # presumptuous opener
    first_sentence = re.split(r"(?<=[.!?])\s+", body.strip(), maxsplit=1)[0].lower() if body.strip() else ""
    for op in C.PRESUMPTUOUS_OPENERS:
        if op in first_sentence:
            r.hard.append(f"presumptuous opener: {op}")

    # soft: word count out of range
    wc = len(re.findall(r"\b\w+\b", body))
    if wc and (wc < C.WORD_MIN or wc > C.WORD_MAX):
        r.soft.append("word count")

    # soft: long sentences
    for sent in re.split(r"(?<=[.!?])\s+", body):
        n = len(re.findall(r"\b\w+\b", sent))
        if n >= 34:
            r.soft.append(f"{n}-word sentence")

    # soft: ask echoes body
    if ask and body:
        b_stem = re.sub(r"[^\w\s]", "", body.lower())
        a_stem = re.sub(r"[^\w\s]", "", ask.lower())
        if len(a_stem) > 20 and a_stem in b_stem:
            r.soft.append("ask repeats body text")

    # universal honesty-floor guards
    facts = spec.get("allowed_facts") or []
    allowed_numbers = set(spec.get("allowed_numbers") or [])
    for hit in numeric_guard(text, facts, allowed_numbers):
        r.hard.append(hit)
    for hit in find_unauthorized_commitments(text):
        r.hard.append(f"unauthorized commitment: {hit}")
    for hit in find_unearned_superlatives(text):
        r.hard.append(f"unearned superlative: {hit}")
    for hit in find_dramatised_opener(text):
        r.hard.append(hit)

    # organisation-audience gated guards
    if spec.get("audience") == "organisation":
        for hit in find_unallowed_precedent(text, spec):
            r.hard.append(hit)
        for hit in find_identity_mechanics_leak(text, spec):
            r.hard.append(hit)

    return r


# ---------------------------------------------------------------------------
# mock_email: deterministic offline body+ask (stub path / tests)
# ---------------------------------------------------------------------------

def mock_email(spec: dict) -> dict:
    """A gate-clean deterministic body for the stub provider and tests. Ties one piece of
    candidate evidence to one target proof point, no dashes, in range, no forbidden phrases."""
    proof = (spec.get("proof_points") or [""])[0]
    ev = spec.get("evidence") or []
    ev_anchor = ev[0]["anchor"] if ev else C.CANDIDATE_PROFILE["experiences"]["solano"]["anchor"]

    lead = spec.get("lead")
    company = spec["company"]

    if lead == "read_and_offer":
        read = spec.get("situation_read") or f"what you are building at {company} looks like it is about to need more hands on the operating side"
        body = (f"In our conversations I keep coming back to {company}. {read}. "
                f"{ev_anchor} I would rather build inside a company than judge them from outside, "
                f"and I think I could take real work off your plate.")
    elif lead == "credential_first":
        body = (f"{ev_anchor} {spec['spine']} "
                f"I follow {company} closely and think the fit is strong.")
    else:  # fit_to_role
        body = (f"I want to build inside a company rather than evaluate them. {ev_anchor} "
                f"{spec['spine']} I am happy on the unglamorous day to day and I ship.")

    body = normalize(body)
    return {"body": body, "ask": ""}


# ---------------------------------------------------------------------------
# render_research_detail: provenance / routing audit
# ---------------------------------------------------------------------------

def render_research_detail(cache: dict) -> str:
    company = cache.get("company") or {}
    lines = ["Research Detail", ""]
    lines.append(f"Target: {company.get('name','?')}")
    if company.get("role_exists") is not None:
        lines.append(f"Role exists: {company.get('role_exists')}  |  size: {company.get('company_size','?')}")
    lines.append(f"Work mode: {company.get('work_mode','?')}  |  language: {company.get('working_language','?')}")
    if company.get("disqualified"):
        lines.append(f"DISQUALIFIED: {company.get('disqualify_reason','')}")
    lines.append("")
    lines.append("Proof points (about them):")
    for p in (cache.get("proof_points") or []):
        if isinstance(p, dict):
            lines.append(f"  - {p.get('fact','')}   [{p.get('source','no source')}]")
    rec = cache.get("recent_point") or {}
    if rec.get("present"):
        lines.append(f"Recent point: {rec.get('detail','')}   [{rec.get('source','no source')}]")
    contact = cache.get("contact") or {}
    lines.append("")
    lines.append(f"Contact: {contact.get('name','?')} ({contact.get('title','')}) "
                 f"<{contact.get('email','')}> conf={contact.get('email_confidence','?')} "
                 f"verified={contact.get('contact_verified', False)}")
    srcs = cache.get("evidence_sources") or []
    if srcs:
        lines.append("")
        lines.append("Sources:")
        for s in srcs:
            lines.append(f"  - {s}")
    fails = cache.get("research_failures") or []
    if fails:
        lines.append("")
        lines.append("Research failures:")
        for f in fails:
            lines.append(f"  - {f}")
    return "\n".join(lines)
