"""Advisory validation, run ONCE on the machine draft, surfaced as plain English.

de.critique returns report.hard / report.soft as short gate strings. We translate each into a
reviewer-facing note. NOTHING blocks: notes are advisory, the reviewer's edits are the source of
truth, and Approve has no lint requirement. We also compute the two narrow signals the product
surfaces: contact-unverified, and the hard work-mode/language disqualifier.
"""
from __future__ import annotations

from .models import Note

_MAP: list[tuple[str, str]] = [
    ("em dash", "Contains a dash. House style uses commas or full stops."),
    ("en dash", "Contains a dash. House style uses commas or full stops."),
    ("forbidden: ", "Contains a phrase we avoid ({v}). Consider rephrasing."),
    ("sign-off in body/ask: ", "Looks like a sign-off ({v}). The mail client adds the signature, so remove it."),
    ("presumptuous opener: ", "Opens by telling them their problem ({v}). Lead with their move or a plain read."),
    ("ask echoes the body", "The closing line repeats a point already made in the body."),
    ("word count", "Draft is longer or shorter than the 70-120 word target."),
]


def _translate(gate: str) -> str:
    g = gate.strip()
    if g.endswith("-word sentence"):
        n = g.split("-", 1)[0]
        return f"Long sentence ({n} words). Consider splitting for readability."
    for prefix, template in _MAP:
        starts = g.startswith(prefix)
        contains_only = prefix in g and prefix in ("ask echoes the body", "word count")
        if starts or contains_only:
            tail = g[len(prefix):].strip() if starts else ""
            val = f'"{tail}"' if tail else ""
            return template.replace("{v}", val).replace("  ", " ").strip()
    return f"Note: {g}"


def notes_from_report(report) -> list[Note]:
    out: list[Note] = []
    for g in getattr(report, "hard", []) or []:
        out.append(Note(severity="hard", text=_translate(g)))
    for g in getattr(report, "soft", []) or []:
        out.append(Note(severity="soft", text=_translate(g)))
    return out


def research_capped(cache: dict) -> bool:
    for f in (cache or {}).get("research_failures") or []:
        fl = f.lower()
        if "web-search limit" in fl or "stopped early" in fl or "incomplete" in fl:
            return True
    return False


def contact_unverified(cache: dict) -> bool:
    """True when research could not confidently pin the named contact. A best-guess email is
    always present regardless."""
    c = (cache or {}).get("contact") or {}
    if c.get("contact_verified") is False:
        return True
    if c.get("status") != "found":
        return True
    if not (c.get("name") or "").strip():
        return True
    return False


def fit_notes(cache: dict, audience: str = "self", allowed_locations: list[str] | None = None) -> list[str]:
    """Advisory notes about how well a target fits. NEVER blocks a draft."""
    company = (cache or {}).get("company") or {}
    notes: list[str] = []
    if audience != "self":
        return notes

    allowed = allowed_locations or (cache or {}).get("_allowed_locations") or []
    if allowed:
        if company.get("disqualified") or company.get("work_mode") == "disqualify":
            reason = company.get("disqualify_reason") or "location or working-mode mismatch"
            notes.append(f"Location/mode: {reason}")

    lang = (company.get("working_language") or "").strip()
    if lang and not any(k in lang.lower() for k in ("english", "en-", "bilingual")):
        notes.append(f"Working language appears to be {lang}, not English-dominant")
    return notes


def is_disqualified(cache: dict) -> tuple[bool, str]:
    """The hard work-mode / language mismatch. Returns (disqualified, reason)."""
    company = (cache or {}).get("company") or {}
    if company.get("disqualified"):
        return True, company.get("disqualify_reason", "work mode or language mismatch")
    if company.get("work_mode") == "disqualify":
        return True, company.get("disqualify_reason", "presence required outside Paris")
    lang = (company.get("working_language") or "").lower()
    if lang and not any(k in lang for k in ("english", "en-", "bilingual")):
        return True, f"working language is {company.get('working_language')} (not English-dominant)"
    return False, ""


def is_thin_cache(cache: dict) -> bool:
    """Flag when there is no plausibly-leadable proof point."""
    import re
    pts = (cache or {}).get("proof_points") or []
    if not pts:
        return True
    for p in pts:
        fact = p.get("fact", "") if isinstance(p, dict) else ""
        if not fact:
            continue
        if re.search(r"\d", fact):
            return False
        if re.search(r"[A-Z]", fact[1:]):
            return False
    return True


def status_pill(report, cache: dict) -> str:
    bits: list[str] = []
    dq, reason = is_disqualified(cache)
    if dq:
        return f"Disqualified: {reason}"
    if contact_unverified(cache):
        bits.append("contact unverified")
    if is_thin_cache(cache):
        bits.append("no strong lead fact")
    n = len(getattr(report, "hard", []) or []) + len(getattr(report, "soft", []) or [])
    if n:
        bits.append(f"{n} note{'s' if n != 1 else ''}")
    return " · ".join(bits) if bits else "Ready to review"


# ---------------------------------------------------------------------------
# Frame-block linting. When a frame block (opening / positioning / close) is model-written it is
# NOT clean by construction, so run the same honesty checks the body gets. Reuses the engine's
# building blocks; app-layer so the engine core stays otherwise untouched.
# ---------------------------------------------------------------------------

def lint_frame_blocks(blocks: dict, spec: dict) -> list[Note]:
    """`blocks` maps a human label -> the block text (pass only model-written blocks; fixed text is
    trusted). Returns advisory Notes for dashes, avoided phrases, and sign-offs found in those blocks."""
    from .engine_bridge import de, engine_config as C
    out: list[Note] = []
    for label, text in (blocks or {}).items():
        t = (text or "").strip()
        if not t:
            continue
        low = t.lower()
        if any(d in t for d in de._DASHES):
            out.append(Note(severity="hard",
                            text=f"The AI-written {label} contains a dash. House style uses commas or full stops."))
        for ph in C.FORBIDDEN_PHRASES:
            if ph in low:
                out.append(Note(severity="hard",
                                text=f'The AI-written {label} uses a phrase we avoid ("{ph}"). Consider rephrasing.'))
        for mk in C.SIGNOFF_MARKERS:
            if mk in low:
                out.append(Note(severity="hard",
                                text=f'The AI-written {label} looks like it has a sign-off ("{mk.strip()}"). Remove it.'))
    return out


# ---------------------------------------------------------------------------
# The honesty floor as a single voice-aware guard (unifies critique + frame lint,
# with word count measured against the voice's own length target, and dashes honoured per voice).
# ---------------------------------------------------------------------------

def floor_notes(spec: dict, email: str, parts: dict, voice) -> list[Note]:
    from .engine_bridge import de
    notes: list[Note] = []
    body_block = next((b for b in voice.blocks if b.length == "body"), None)
    body_text = parts.get(body_block.id, "") if body_block else ""

    # 1. deep checks on the narrative via critique — but ignore its fixed 70-120 word note (the
    #    voice sets its own length, checked below) and drop dash notes when the voice allows dashes.
    if (body_text or "").strip():
        rep = de.critique(body_text, "", spec)
        for n in notes_from_report(rep):
            low = n.text.lower()
            if "word count" in low or "word target" in low or "70-120" in n.text:
                continue
            if voice.allow_dashes and "dash" in low:
                continue
            notes.append(n)

    # 2. lint the other AI-written blocks (opening / positioning / close etc.)
    other = {(b.label or b.id): parts.get(b.id, "")
             for b in voice.blocks
             if b.mode == "ai" and (body_block is None or b.id != body_block.id)}
    for n in lint_frame_blocks(other, spec):
        if voice.allow_dashes and "dash" in n.text.lower():
            continue
        notes.append(n)

    # 3. voice-aware word count on the narrative
    if (body_text or "").strip():
        wc = len(body_text.split())
        if wc < voice.length_min or wc > voice.length_max:
            notes.append(Note(severity="soft",
                              text=f"Body is {wc} words; this voice targets {voice.length_min}-{voice.length_max}."))

    return notes
