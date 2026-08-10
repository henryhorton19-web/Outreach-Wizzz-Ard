"""Stage-2 composition: the whole email in ONE model call, driven entirely by the voice.

Fixed blocks are token substitution (no model). AI blocks are written by the model in a single call
whose prompt is a short honesty floor, then the voice (compiled structured style + notes + identity
emphasis + examples + edit-ledger), then per-block specs each carrying only its scoped facts. The
model returns a JSON object keyed by block id. This replaces the old body-only compose_body plus the
per-block compose_block: blocks now stay coherent, Sciences-Po-once holds by construction, and it is
one call regardless of block count.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .providers.base import Provider, ProviderError
from .engine_bridge import de, engine_config as EC
from . import settings as S
from . import edit_ledger

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TOKEN_RE = re.compile(r"\{(\w+)\}")
_QUOTES = "\u201c\u201d\u2018\u2019\"'"


class ComposeError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The honesty floor — the ONLY fixed instruction (everything else is the voice)
# ---------------------------------------------------------------------------

def floor_preamble(*, allow_dashes: bool, has_profile_evidence: bool) -> str:
    lines = [
        "You are writing parts of a short cold outreach email from a candidate.",
        "Use ONLY the facts provided for each block. Invent no numbers, names, companies, or claims.",
        "No sign-off; the mail client appends the signature.",
    ]
    if not allow_dashes:
        lines.append("No dashes of any kind. Use commas or full stops.")
    lines.append("Format longer blocks into short, readable paragraphs separated by blank lines (\\n\\n). Do not write a single massive wall of text.")
    return " ".join(lines)


def followup_floor_preamble() -> str:
    """Invariant follow-up rules, grounded in 2026 cold-outreach data and elite-application practice.
    These are the follow-up analogue of the honesty floor: they hold regardless of which follow-up
    voice is used. Tunable content (tone, which angle, phrasing) lives in the editable follow-up voice.
    """
    return (
        "This is a FOLLOW-UP to an earlier email that has not received a reply; the recipient will "
        "read it under the quoted original in the same thread. "
        "Do not resend, restate, or paraphrase the original. Do not claim you have spoken before or "
        "that they replied. "
        "Re-anchor in at most one short clause, add exactly ONE new, specific point that was NOT in "
        "the original, and close with a SINGLE low-friction ask. "
        "Never use filler openers ('just checking in', 'circling back', 'bumping this', 'following "
        "up'). Keep the whole message tight — well under 80 words — and respect the reader's time; "
        "concision is the signal."
    )


# ---------------------------------------------------------------------------
# Tokens (the vocabulary): research + profile + per-experience anchors + variables
# ---------------------------------------------------------------------------

def derive_tokens(spec: dict, variables: dict | None = None) -> dict:
    company = spec.get("company", "")
    role = spec.get("role_title", "")
    proofs = spec.get("proof_points", []) or []
    recent = spec.get("recent", {}) or {}
    name = EC.CANDIDATE_PROFILE.get("name", "")
    tokens = {
        "company": company,
        "contact_first": spec.get("contact_first", "there"),
        "name": spec.get("contact_first", "there"),   # legacy alias (old greeting used {name})
        "contact_full": spec.get("contact_name", ""),
        "role": role,
        "role_or_company": role or company,
        "what_they_do": spec.get("what_they_do", "") or "",
        "situation_read": spec.get("situation_read", "") or "",
        "recent": recent.get("detail", "") if recent.get("present") else "",
        "recent_short": (recent.get("detail", "").split(",")[0] if recent.get("present") else ""),
        # Which kind of recent point research found (raise | funding | launch | hire |
        # expansion | other), or "" when none. Drives CustomVoice.recent_point_templates:
        # a voice can swap a fixed block's standing text for a kind-specific opener.
        "recent_kind": (recent.get("kind", "") if recent.get("present") else ""),
        "proof_1": proofs[0] if len(proofs) > 0 else "",
        "proof_2": proofs[1] if len(proofs) > 1 else "",
        "city": spec.get("city", "") or "",
        "candidate_name": name,
        "profile_first": (name.split(" ")[0] if name else ""),
        "candidate_first": (name.split(" ")[0] if name else ""),   # backward-compat alias
        "link_strength": spec.get("link_strength") or (spec.get("link") or {}).get("link_strength", "none"),
        "shared_subject": (spec.get("link") or {}).get("shared_subject", ""),
        "why": (spec.get("link") or {}).get("why", ""),
    }
    # one token per candidate experience -> its anchor (derived, not hardcoded)
    for k, e in EC.CANDIDATE_PROFILE.get("experiences", {}).items():
        tokens[k] = e.get("anchor", "")
    for k, v in (variables or {}).items():
        tokens[str(k)] = str(v)
    return tokens


def render(text: str, tokens: dict) -> str:
    """Substitute {token}s; leave unknown tokens (including {relevant}) intact for later handling."""
    if not text:
        return ""
    return _TOKEN_RE.sub(lambda m: tokens.get(m.group(1), m.group(0)), text)


def _clean(text: str) -> str:
    t = _FENCE_RE.sub("", text or "").strip().strip(_QUOTES).strip()
    return de.normalize(t).strip()


# ---------------------------------------------------------------------------
# {relevant} for fixed blocks (AI blocks handle it inside the single call)
# ---------------------------------------------------------------------------

def _relevant_anchor(provider: Provider, point_text: str, shortlist: list, register: str) -> str:
    """Pick the best-fit experience anchor for a fixed block's {relevant}. Offline/stub -> top of
    the shortlist; online -> a one-shot model pick, falling back to the top on any issue."""
    if not shortlist:
        return ""
    if getattr(provider, "is_stub", False):
        return shortlist[0]["anchor"]
    try:
        st = S.load_settings()
        options = "\n".join(f"{i}: {e['anchor']}" for i, e in enumerate(shortlist))
        res = provider.generate(
            system="Choose the single option that best supports the given point. Return ONLY the "
                   "integer index, nothing else.",
            user=f"POINT:\n{point_text}\n\nOPTIONS:\n{options}", use_web=False, temperature=0.0,
            timeout_s=st.request_timeout_s, max_retries=1,
            model=(getattr(st, "helper_model", "") or None), max_output_tokens=8, thinking_budget=0)
        idx = int(re.search(r"\d+", (res.text or "0")).group())
        return shortlist[max(0, min(idx, len(shortlist) - 1))]["anchor"]
    except Exception:
        return shortlist[0]["anchor"]


# Which block the recent-point template replaces. The original contract, quoted in
# the migrated voices' own guidance, is that the OPENING line is swapped -- not every
# fixed block. Without this scope the template also replaced the greeting, identity
# paragraph and close, producing an email that was the same sentence four times.
_RECENT_SWAP_BLOCK_ID = "opening"


def resolve_fixed(provider: Provider, block, tokens: dict, shortlist: list, register: str = "",
                  voice=None) -> str:
    """Render a fixed block's text.

    If the voice declares a recent_point_templates entry matching the kind of recent
    point research found, that template replaces the block's standing text. This is
    the "raise-swap" the original migrated voices documented in their opening-block
    guidance ("if research reports a recent capital raise, this line is replaced by
    'Congratulations on your recent {detail}.'") but which this app never
    implemented -- the field existed on CustomVoice and was read by nothing.

    Generic by design: keyed on any recent_point.kind, applied to any fixed block,
    configured per voice. A voice with an empty map (every voice today) is
    completely unaffected.
    """
    templates = getattr(voice, "recent_point_templates", None) or {}
    kind = (tokens.get("recent_kind") or "").strip()
    source = block.text
    if kind and templates.get(kind) and block.id == _RECENT_SWAP_BLOCK_ID:
        source = templates[kind]
    text = render(source, tokens)
    if "{relevant}" in text:
        anchor = _relevant_anchor(provider, text.replace("{relevant}", "").strip(), shortlist, register)
        text = text.replace("{relevant}", anchor)
    return de.normalize(text).strip()


# ---------------------------------------------------------------------------
# Structured style -> deterministic prompt directives
# ---------------------------------------------------------------------------

def _compile_style(style) -> list[str]:
    def pick(seq, i):
        return seq[max(0, min(int(i), len(seq) - 1))]
    out = [
        "Tone: " + pick(["very casual and conversational", "casual", "neutral in formality",
                         "fairly formal", "formal and professional"], style.formality) + ".",
        pick(["Keep it cool and businesslike.", "Measured warmth.", "Some warmth.", "Warm.",
              "Very warm and personable."], style.warmth),
        "Be " + pick(["very diplomatic and soft", "diplomatic", "fairly direct",
                      "direct and to the point", "blunt, with no soft preambles"], style.directness) + ".",
        {"short": "Short sentences.", "medium": "Medium-length sentences.",
         "flowing": "Prefer flowing sentences; merge closely related fragments."}.get(style.sentence_length, ""),
        {"hedged": "Hedge claims; avoid overstating.", "neutral": "",
         "assertive": "State things plainly and confidently."}.get(style.hedging, ""),
        {"none": "", "dry": "A touch of dry wit is fine.",
         "light": "Light, friendly humour is welcome."}.get(style.humor, ""),
        {"recipient_first": "Centre the email on the recipient and their situation.",
         "sender_first": "Lead from the candidate's angle.",
         "balanced": "Balance the recipient and the candidate."}.get(style.person_focus, ""),
        {"single": "Tie exactly one piece of candidate evidence to one point; do not pile.",
         "few": "Weave at most two supporting proofs.",
         "several": "You may reference several proofs if they genuinely fit."}.get(style.proof_density, ""),
    ]
    return [x for x in out if x]


# ---------------------------------------------------------------------------
# fact_scope -> the facts a block may use
# ---------------------------------------------------------------------------

def _scoped_facts(scope, spec: dict, shortlist: list) -> list[str]:
    scope = set(scope or [])
    recent = spec.get("recent", {}) or {}
    facts: list[str] = []
    if "recent" in scope and recent.get("present"):
        facts.append(recent.get("detail", ""))
    if "target_proofs" in scope:
        facts += [p for p in (spec.get("proof_points") or []) if p]
    if "situation_read" in scope and spec.get("situation_read"):
        facts.append(spec["situation_read"])
    if "profile_evidence" in scope or "candidate_evidence" in scope:
        facts += [e["anchor"] for e in (spec.get("evidence") or [])]
    if ("profile_spine" in scope or "candidate_spine" in scope) and spec.get("spine"):
        facts.append(spec["spine"])
    if "custom_facts" in scope:
        facts += list(spec.get("custom_facts") or [])
    return [f for f in facts if f]


_LEN_HINT = {"one_line": "exactly one sentence", "short": "one or two sentences",
             "medium": "two to three sentences", "body": None}


# ---------------------------------------------------------------------------
# The one compose call
# ---------------------------------------------------------------------------

def build_voice_system(voice, has_profile_evidence: bool) -> str:
    parts = [floor_preamble(allow_dashes=voice.allow_dashes,
                            has_profile_evidence=has_profile_evidence),
             "\n--- VOICE (the author's intent; follow it) ---"]
    parts += _compile_style(voice.style)
    if (voice.style.notes or "").strip():
        parts.append("Notes: " + voice.style.notes)
    if (voice.evidence.identity_note or "").strip():
        parts.append("Emphasis: " + voice.evidence.identity_note)
    ex = [e for e in (voice.style.examples or []) if (e or "").strip()]
    if ex:
        parts.append("\n--- EXAMPLES (match the style; do not copy their facts) ---")
        for i, e in enumerate(ex, 1):
            parts.append(f"Example {i}:\n{e}")
    led = edit_ledger.examples_block(voice.id)
    if led and led.strip():
        parts.append(led)
    return "\n".join(parts)


def _blockspecs(voice, ai_blocks, spec, tokens, shortlist):
    specs, uses_relevant = [], False
    for b in ai_blocks:
        hint = f"{voice.length_min} to {voice.length_max} words" if b.length == "body" \
            else _LEN_HINT.get(b.length)
        bs = {
            "id": b.id, "label": b.label or b.id,
            "instruction": render(b.guidance, tokens) or f"Write the {b.label or b.id}.",
            "length": hint,
            "facts": _scoped_facts(b.fact_scope, spec, shortlist),
        }
        if "{relevant}" in (b.guidance or "") or "{relevant}" in (b.text or ""):
            uses_relevant = True
            bs["choose_from_experiences"] = [e["anchor"] for e in shortlist]
        seed = render(b.text, tokens).strip()
        if seed:
            bs["seed"] = seed
        specs.append(bs)
    return specs, uses_relevant


def compose_voice(provider: Provider, voice, ai_blocks, spec: dict, tokens: dict,
                  shortlist: list, followup: dict | None = None) -> dict:
    """One model call -> {block_id: text} for the AI blocks. Stub/offline -> mock_voice.
    When `followup` is provided ({original_subject, original_body, step}), the follow-up floor is
    prepended and the prior email is passed as context so AI blocks write a follow-up, not a fresh
    pitch."""
    if getattr(provider, "is_stub", False):
        return mock_voice(voice, ai_blocks, spec, tokens, shortlist, followup=followup)

    has_cv = any(("profile_evidence" in (b.fact_scope or [])) or
                 ("candidate_evidence" in (b.fact_scope or [])) or
                 ("profile_spine" in (b.fact_scope or [])) or
                 ("candidate_spine" in (b.fact_scope or [])) for b in ai_blocks)
    system = build_voice_system(voice, has_profile_evidence=has_cv)
    if followup:
        system = followup_floor_preamble() + "\n\n" + system
    specs, _ = _blockspecs(voice, ai_blocks, spec, tokens, shortlist)
    task = ("Write each block of one cold outreach email. Return ONLY a JSON object mapping "
            "each block id to its text. Use only the facts listed for that block, and do not "
            "repeat the same fact across blocks. Where a block lists choose_from_experiences, "
            "weave in the ONE that best fits its point, named naturally, and nothing outside "
            "that list.")
    instruction: dict = {"task": task, "blocks": specs}
    if followup:
        instruction["task"] = ("Write each block of one short FOLLOW-UP email (follow-up #%d). "
                               "Return ONLY a JSON object mapping each block id to its text. Obey "
                               "the follow-up rules above." % int(followup.get("step", 1)))
        instruction["prior_email"] = {
            "subject": followup.get("original_subject", ""),
            "body": followup.get("original_body", ""),
        }
    st = S.load_settings()
    user = json.dumps(instruction, ensure_ascii=False, indent=2)
    last = None
    for _ in range(2):
        try:
            res = provider.generate(system=system, user=user, use_web=False,
                                    temperature=st.compose_temperature,
                                    timeout_s=st.request_timeout_s, max_retries=st.max_retries,
                                    **_compose_gen_kwargs(st))
            try:
                from . import cost as _cost
                _cost.record(getattr(st, "compose_model", "") or "", res, slug=_cost.current_slug())
            except Exception:
                pass
            return _parse_blocks(res.text, [b.id for b in ai_blocks])
        except ProviderError as e:
            raise ComposeError(f"compose provider error: {e}") from e
        except Exception as e:
            last = e
            user += ("\n\n--- YOUR PREVIOUS OUTPUT WAS NOT VALID JSON ---\n"
                     "Return only a JSON object mapping block id to text.")
    raise ComposeError(f"compose failed: {last}")


def _parse_blocks(text: str, ids: list[str]) -> dict:
    cleaned = _FENCE_RE.sub("", text or "").strip()
    obj = None
    try:
        obj = json.loads(cleaned)
    except Exception:
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s != -1 and e > s:
            try:
                obj = json.loads(cleaned[s:e + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        raise ComposeError("compose response was not a JSON object of blocks")
    out = {}
    for bid in ids:
        v = obj.get(bid)
        if isinstance(v, dict):
            v = v.get("text") or v.get("body") or ""
        out[bid] = _clean(str(v or ""))
    return out


def _compose_gen_kwargs(st) -> dict:
    tb = getattr(st, "compose_thinking_budget", -999)
    return dict(model=(getattr(st, "compose_model", "") or None),
                thinking_budget=(None if tb == -999 else tb),
                thinking_level=getattr(st, "compose_thinking_level", ""),
                max_output_tokens=getattr(st, "compose_max_output_tokens", 0))


# ---------------------------------------------------------------------------
# Deterministic offline composition (stub provider / tests)
# ---------------------------------------------------------------------------

def mock_voice(voice, ai_blocks, spec: dict, tokens: dict, shortlist: list,
               followup: dict | None = None) -> dict:
    out = {}
    recent = spec.get("recent", {}) or {}
    for b in ai_blocks:
        facts = _scoped_facts(b.fact_scope, spec, shortlist)
        if followup and b.length == "body":
            # deterministic follow-up body obeying the floor: one new angle + single ask, tight
            anchor = ((spec.get("evidence") or [{}])[0].get("anchor")
                      or (shortlist[0]["anchor"] if shortlist else ""))
            read = (spec.get("situation_read", "") or "").rstrip(".")
            angle = read or anchor or "a specific reason it could be a fit"
            txt = (f"Since my note, one thing stood out: {angle}. "
                   "Would a short call in the next week or two be worth it?")
            out[b.id] = de.normalize(txt).strip()
        elif b.length == "body":
            company = spec.get("company", "the company")
            anchor = ((spec.get("evidence") or [{}])[0].get("anchor")
                      or (shortlist[0]["anchor"] if shortlist else ""))
            read = spec.get("situation_read", "")
            txt = f"I have been following {company}."
            if read:
                txt += f" {read.rstrip('.')}."
            if anchor:
                txt += f" {anchor}"
            txt += (" I would rather build inside a company than evaluate it from outside, and I "
                    "think I could take real work off your plate.")
            out[b.id] = de.normalize(txt).strip()
        elif "recent" in (b.fact_scope or []) and recent.get("present"):
            det = recent.get("detail", "").strip().rstrip(".")
            out[b.id] = de.normalize(f"Congratulations on {det}." if recent.get("kind") == "raise"
                                     else f"I saw {det}, which is what prompted me to write.").strip()
        elif "{relevant}" in (b.guidance or "") and shortlist:
            out[b.id] = de.normalize(shortlist[0]["anchor"]).strip()
        elif facts:
            out[b.id] = de.normalize(facts[0]).strip()
        else:
            out[b.id] = de.normalize(render(b.text, tokens) or f"About {spec.get('company', 'you')}.").strip()
    return out


# ---------------------------------------------------------------------------
# Assemble the whole email from the voice's block order
# ---------------------------------------------------------------------------

def _skip(block, spec: dict) -> bool:
    if not block.optional:
        return False
    scope = set(block.fact_scope or [])
    if scope == {"recent"} and not (spec.get("recent", {}) or {}).get("present"):
        return True
    return False


def produce_email(provider: Provider, voice, spec: dict, tokens: dict, shortlist: list,
                  followup: dict | None = None):
    """Return (email, parts_by_block_id, body_text). Fixed blocks are substituted; AI blocks come
    from one compose call; blocks are joined in the voice's order (skipping empties/optionals).
    When `followup` is set, AI blocks are composed as a follow-up (prior email + floor)."""
    ai_blocks = [b for b in voice.blocks if b.mode == "ai" and not _skip(b, spec)]
    parts = {}
    for b in voice.blocks:
        if b.mode == "fixed" and not _skip(b, spec):
            parts[b.id] = resolve_fixed(provider, b, tokens, shortlist, voice.style.notes, voice=voice)
    if ai_blocks:
        parts.update(compose_voice(provider, voice, ai_blocks, spec, tokens, shortlist,
                                   followup=followup))
    email = "\n\n".join(parts[b.id].strip() for b in voice.blocks
                        if parts.get(b.id, "").strip())
    body_block = next((b for b in voice.blocks if b.length == "body"), None)
    body_text = parts.get(body_block.id, "") if body_block else ""
    return email, parts, body_text

