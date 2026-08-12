"""Per-target pipeline: research -> resolve voice -> gather facts -> (voice-parameterised) evidence
selection -> produce the email from the voice's blocks -> honesty-floor guard -> advisory notes.

The voice is the author. The engine supplies only facts (gather + evidence selection) and the
honesty floor; everything about structure, style, length, content emphasis, and vocabulary comes
from the voice. Runs one target end to end and mutates its TargetState in place.
"""
from __future__ import annotations

import re

from .engine_bridge import de, engine_config as EC
from .models import CompanyState, State
from .providers.base import Provider
from . import research as research_mod
from . import compose as compose_mod
from . import validate as validate_mod
from . import store
from . import settings as S
from . import cost as cost_mod


# ---------------------------------------------------------------------------
# voice selection (unchanged: override > situation tag > default_voice > any > re-seed)
# ---------------------------------------------------------------------------

def resolve_voice(cache: dict, override: str | None = None) -> str:
    """Voice selection is manual only: override -> default_voice -> first-available.

    Previously: override -> a voice whose `situations` matched a cache-derived tag
    (auto_route) -> a reply-rate bandit tiebreak among matches (_learned_pick) ->
    default_voice -> first-available -> re-seed. Deleted along with auto_route,
    select_voice (dead code, zero callers, duplicated auto_route) and _learned_pick,
    because a human now picks the voice for every draft regardless of what situation
    the target is in -- there is no automatic selection left to tie-break among.

    `cache` is kept as a parameter for call-site compatibility even though this
    function no longer reads it, so every existing caller works unchanged.
    """
    if override and store.get_custom_voice(override):
        return override
    dv = S.load_settings().default_voice
    if dv and store.get_custom_voice(dv):
        return dv
    voices = store.list_custom_voices()
    if voices:
        return voices[0].id
    S.ensure_seeded()
    voices = store.list_custom_voices()
    return voices[0].id if voices else S.VALID_VOICES[1]


def resolve_followup_voice(cache: dict, override: str | None = None) -> str:
    """Pick a FOLLOW-UP-kind voice manually: override -> first-available follow-up voice."""
    fu_voices = store.list_custom_voices(kind="followup")
    if override:
        ov = store.get_custom_voice(override)
        if ov is not None and (getattr(ov, "kind", "outreach") == "followup"):
            return override
    if fu_voices:
        return fu_voices[0].id
    S.ensure_seeded()
    fu_voices = store.list_custom_voices(kind="followup")
    return fu_voices[0].id if fu_voices else ""


# ---------------------------------------------------------------------------
# facts + allowed-fact assembly
# ---------------------------------------------------------------------------

def _experience_tokens_used(voice) -> set[str]:
    """Experience keys a voice references verbatim via {key} tokens in any fixed block text."""
    keys = set(EC.CANDIDATE_PROFILE.get("experiences", {}).keys())
    used = set()
    for b in voice.blocks:
        for m in re.findall(r"\{(\w+)\}", (b.text or "")):
            if m in keys:
                used.add(m)
    return used


def _build_allowed_facts(cache: dict, selected: list, shortlist: list, voice) -> list[dict]:
    """Everything an AI block may ground on: target proofs + recent + selected evidence + the
    {relevant} shortlist + experiences dropped in by {key} token + the voice's custom facts."""
    allowed: list[dict] = []
    for p in (cache.get("proof_points") or []):
        if isinstance(p, dict) and p.get("fact"):
            allowed.append({"text": p["fact"], "source": p.get("source", ""), "about": "target"})
    rec = cache.get("recent_point") or {}
    if rec.get("present") and rec.get("detail"):
        allowed.append({"text": rec["detail"], "source": rec.get("source", ""), "about": "target"})
    seen = set()
    for e in list(selected) + list(shortlist):
        a = e.get("anchor", "")
        if a and a not in seen:
            seen.add(a)
            allowed.append({"text": a, "source": "candidate_profile", "about": "candidate"})
    exps = EC.CANDIDATE_PROFILE.get("experiences", {})
    for k in _experience_tokens_used(voice):
        a = exps.get(k, {}).get("anchor", "")
        if a and a not in seen:
            seen.add(a)
            allowed.append({"text": a, "source": "candidate_profile", "about": "candidate"})
    for cf in (voice.evidence.custom_facts or []):
        if (cf or "").strip():
            allowed.append({"text": cf, "source": "voice_custom_fact", "about": "candidate"})
    return allowed


def _is_unusable_cache(cache: dict) -> bool:
    if not isinstance(cache, dict):
        return True
    pts = [p for p in (cache.get("proof_points") or []) if isinstance(p, dict) and p.get("fact")]
    if pts:
        return False
    return not (cache.get("situation_read") or "").strip()


# ---------------------------------------------------------------------------
# draft one target
# ---------------------------------------------------------------------------

def draft_one(provider: Provider, cs: CompanyState, voice_override: str | None = None,
              *, reuse_cache: bool = True) -> CompanyState:
    cost_mod.set_slug(cs.slug)
    try:
        # 1. research (reuse a cached result so edits/re-runs never re-search)
        cache = None
        if reuse_cache:
            cache = store.load_cache(cs.slug)
            if cache is not None:
                cache = research_mod._sanitize_cache(cache)
                # A cache whose facts are salvage placeholders cannot produce an
                # email, and reusing it on a retry reproduces the same empty draft.
                # Discard it here so the branch below re-researches instead.
                from .cache_health import is_degraded
                if is_degraded(cache):
                    cache = None
                    cs.notes = ((cs.notes + "\n") if cs.notes else "") + \
                        "Previous research was incomplete, so it was run again."
        if cache is None:
            given_site = cs.recipient_domain or cs.website
            cache = research_mod.research_company(provider, cs.name, given_site, None)
            store.save_cache(cs.slug, cache)
        cs.cache = cache

        company = cache.get("company") or {}
        if company.get("name"):
            cs.name = company["name"]
        if company.get("resolved_domain"):
            cs.recipient_domain = company["resolved_domain"]
        cs.role_exists = company.get("role_exists")
        cs.company_size = company.get("company_size")
        cs.state = State.researched

        # Fit is advisory, never a refusal. The cache was already saved above, so
        # the research is paid for either way -- raising here only threw away work
        # already bought, and did it using one person's commute radius as the test
        # even for organisation-audience voices.
        _vdef = store.get_custom_voice(cs.voice) if cs.voice else None
        _audience = getattr(_vdef, "audience", "self") if _vdef else "self"
        _notes = validate_mod.fit_notes(cache, audience=_audience)
        if _notes:
            cs.disqualified = False
            cs.status_pill = f"Check fit: {_notes[0]}"
            cs.notes = ((cs.notes + "\n") if cs.notes else "") + "\n".join(_notes)
        if _is_unusable_cache(cache):
            raise RuntimeError("research incomplete — could not gather enough to draft "
                               "(likely a rate limit or search cutoff); review and re-run this target")

        # 2. resolve the voice
        voice_id = resolve_voice(cache, voice_override)
        vdef = store.get_custom_voice(voice_id)
        if vdef is None:
            S.ensure_seeded()
            vdef = store.get_custom_voice(voice_id) or (store.list_custom_voices() or [None])[0]
        if vdef is None:
            raise RuntimeError("no voice available to draft with")
        cs.voice = vdef.id

        # 3. fact pool (engine) + voice-parameterised evidence + {relevant} shortlist
        spec = de.prepare(cache)
        spec["allow_dashes"] = bool(getattr(vdef, "allow_dashes", False))
        spec["what_they_do"] = company.get("what_they_do", "")
        spec["city"] = company.get("city", "") or company.get("location", "")
        ev = vdef.evidence
        prefs = dict(prefer=ev.prefer, pin=ev.pin, exclude=ev.exclude, weights=ev.category_weights)
        selected = de.select_evidence(cache, count=ev.count, **prefs)
        ranked = de.rank_evidence(cache, **prefs)
        shortlist = [e for e in ranked if e.get("_score", 0) > 0 or e.get("_pinned")][:5] or ranked[:3]
        spec["evidence"] = selected
        spec["evidence_shortlist"] = [e["anchor"] for e in shortlist]
        spec["custom_facts"] = list(ev.custom_facts or [])
        spec["allowed_facts"] = _build_allowed_facts(cache, selected, shortlist, vdef)
        spec["send_to"] = (cache.get("contact") or {}).get("email", "")
        cs.spec = spec

        # 4. produce the email from the voice's blocks (one compose call for AI blocks)
        tokens = compose_mod.derive_tokens(spec, vdef.variables)
        email, parts, body_text = compose_mod.produce_email(provider, vdef, spec, tokens, shortlist)

        cs.subject = compose_mod.render(vdef.subject, tokens)
        cs.machine_subject = cs.subject
        cs.machine_body = body_text or email
        cs.machine_email = email
        cs.final_email = email
        cs.edited_email = None
        cs.edited_body = None

        # 5. honesty-floor guard (advisory) + status
        cs.notes = validate_mod.floor_notes(spec, email, parts, vdef)
        cs.contact_unverified = validate_mod.contact_unverified(cache)
        cs.research_capped = validate_mod.research_capped(cache)
        rep = de.critique(body_text, "", spec)
        cs.status_pill = validate_mod.status_pill(rep, cache)

        cs.state = State.drafted
        cs.error = None
    except Exception as e:
        cs.state = State.error
        cs.error = f"{type(e).__name__}: {e}"
    # fold this draft's accumulated token cost onto the record (Phase 1e), then clear context
    try:
        acc = cost_mod.take_draft(cs.slug)
        cs.tokens_in = acc.get("in", 0)
        cs.tokens_out = acc.get("out", 0)
        cs.tokens_cached = acc.get("cached", 0)
        cs.cost_estimate = acc.get("cost", 0.0)
        if cs.state == State.drafted:
            cost_mod.bump_drafts(1)
    except Exception:
        pass
    finally:
        cost_mod.set_slug(None)
    store.upsert_draft(cs)
    return cs


def apply_edit(cs: CompanyState, *, subject: str | None, email: str) -> CompanyState:
    if subject is not None:
        cs.subject = subject
    cs.edited_email = email
    cs.final_email = email
    cs.state = State.edited
    store.upsert_draft(cs)
    return cs


def _readdress_greeting(text: str, old_name: str, new_name: str) -> str:
    """Best-effort salutation swap for a DIFFERENT-person retry: replace the old recipient's name in
    the FIRST LINE only (the greeting) with the new recipient's name, leaving the rest of the user's
    approved text untouched. Deterministic, no model call; the retry is reviewed before sending
    (approve-first), so a missed edge case is caught by the human. No-op if names are absent/equal."""
    if not text or not (new_name or "").strip():
        return text
    parts = text.split("\n", 1)
    first, rest = parts[0], (parts[1] if len(parts) > 1 else None)
    of = (old_name or "").split()[0] if (old_name or "").split() else ""
    nf = new_name.split()[0] if new_name.split() else new_name
    if old_name and old_name in first:
        first = first.replace(old_name, new_name, 1)          # full name in the greeting
    elif of and of != nf and of in first:
        first = first.replace(of, nf, 1)                      # first name in the greeting
    return first if rest is None else first + "\n" + rest


def draft_retarget(provider: Provider, sent_item, new_email: str, *, bounce_n: int = 1,
                   new_person: dict | None = None):
    """Bounce re-draft (Phase 6b): re-stage the email you ALREADY APPROVED to the next address.

    It reuses your exact approved copy verbatim — the edited version you signed off, stored on the
    SentItem as `approved_body`/`approved_subject` — and does NOT recompose from the model, so your
    edits are never lost. Only the recipient changes; when the next rung belongs to a DIFFERENT
    person (`new_person`), the salutation is re-addressed to them (first line only) and the working
    cache contact is overridden so downstream addressing/"To:" is right. No model call, no cost.

    Legacy fallback: a send approved before `approved_body` existed has no stored copy, so it
    regenerates from the parent cache + original voice (the old behaviour). Lands as a normal
    approvable draft keyed "{parent_slug}__b{n}" in state=drafted. Never auto-sends; errors -> error.
    """
    parent_slug = sent_item.slug
    slug = f"{parent_slug}__b{bounce_n}"
    cs = store.get_draft(slug) or CompanyState(slug=slug, name=sent_item.name,
                                               ref=f"bounce retry #{bounce_n}")
    cs.ref = f"bounce retry #{bounce_n}"
    cost_mod.set_slug(slug)
    try:
        cache = store.load_cache(parent_slug)
        if cache is not None:
            cache = research_mod._sanitize_cache(cache)
        cache = cache or {}
        if new_person and (new_person.get("name") or "").strip():
            # WORKING-COPY override only (never save_cache): re-address to the different person so
            # de.prepare derives their contact_first/contact_name/send_to for greeting + tokens.
            cache = dict(cache)
            cache["contact"] = {**(cache.get("contact") or {}),
                                "name": new_person["name"],
                                "title": new_person.get("title", ""),
                                "email": new_email,
                                "email_confidence": new_person.get("confidence", "low")}
        cs.cache = cache
        company = cache.get("company") or {}
        cs.role_exists = company.get("role_exists")
        cs.company_size = company.get("company_size")

        approved_body = (getattr(sent_item, "approved_body", "") or "").strip()

        if approved_body:
            # --- REUSE the approved (edited) copy verbatim; only re-address it. No model call. ---
            cs.voice = sent_item.voice or resolve_voice(cache, None)
            spec = de.prepare(cache)              # deterministic; gives contact_first/name/send_to
            v_obj = store.get_custom_voice(cs.voice) if cs.voice else None
            spec["allow_dashes"] = bool(getattr(v_obj, "allow_dashes", False))
            spec["send_to"] = new_email
            cs.spec = spec
            body = getattr(sent_item, "approved_body", "") or ""
            if new_person and (new_person.get("name") or "").strip():
                body = _readdress_greeting(body, getattr(sent_item, "to_name", ""),
                                           new_person["name"])
            cs.subject = getattr(sent_item, "approved_subject", "") or sent_item.subject
            cs.machine_subject = cs.subject
            cs.machine_body = body
            cs.machine_email = body
            cs.final_email = body
            cs.edited_email = None
            cs.edited_body = None
            cs.notes = []
            cs.contact_unverified = False
            cs.research_capped = False
            _who = f" ({new_person['name']})" if (new_person and new_person.get("name")) else ""
            cs.status_pill = f"bounced → resending your approved copy to {new_email}{_who}"
            cs.state = State.drafted
            cs.error = None
        else:
            # --- Legacy fallback: no stored approved copy -> regenerate (old behaviour). ---
            voice_id = sent_item.voice or resolve_voice(cache, None)
            vdef = store.get_custom_voice(voice_id)
            if vdef is None:
                S.ensure_seeded()
                vdef = store.get_custom_voice(voice_id) or (store.list_custom_voices() or [None])[0]
            if vdef is None:
                raise RuntimeError("no voice available to re-target with")
            cs.voice = vdef.id

            spec = de.prepare(cache)
            spec["allow_dashes"] = bool(getattr(vdef, "allow_dashes", False))
            spec["what_they_do"] = company.get("what_they_do", "")
            spec["city"] = company.get("city", "") or company.get("location", "")
            ev = vdef.evidence
            prefs = dict(prefer=ev.prefer, pin=ev.pin, exclude=ev.exclude, weights=ev.category_weights)
            selected = de.select_evidence(cache, count=ev.count, **prefs)
            ranked = de.rank_evidence(cache, **prefs)
            shortlist = [e for e in ranked if e.get("_score", 0) > 0 or e.get("_pinned")][:5] or ranked[:3]
            spec["evidence"] = selected
            spec["evidence_shortlist"] = [e["anchor"] for e in shortlist]
            spec["custom_facts"] = list(ev.custom_facts or [])
            spec["allowed_facts"] = _build_allowed_facts(cache, selected, shortlist, vdef)
            spec["send_to"] = new_email                       # the next rung on the ladder
            cs.spec = spec

            tokens = compose_mod.derive_tokens(spec, vdef.variables)
            email, parts, body_text = compose_mod.produce_email(provider, vdef, spec, tokens, shortlist)

            cs.subject = compose_mod.render(vdef.subject, tokens) or sent_item.subject
            cs.machine_subject = cs.subject
            cs.machine_body = body_text or email
            cs.machine_email = email
            cs.final_email = email
            cs.edited_email = None
            cs.edited_body = None
            cs.notes = validate_mod.floor_notes(spec, email, parts, vdef)
            cs.contact_unverified = validate_mod.contact_unverified(cache) if cache else False
            cs.research_capped = False
            _who = f" ({new_person['name']})" if (new_person and new_person.get("name")) else ""
            cs.status_pill = f"bounced → retry to {new_email}{_who}"
            cs.state = State.drafted
            cs.error = None
    except Exception as e:
        cs.state = State.error
        cs.error = f"{type(e).__name__}: {e}"
    try:
        acc = cost_mod.take_draft(slug)
        cs.tokens_in = acc.get("in", 0); cs.tokens_out = acc.get("out", 0)
        cs.tokens_cached = acc.get("cached", 0); cs.cost_estimate = acc.get("cost", 0.0)
    except Exception:
        pass
    finally:
        cost_mod.set_slug(None)
    store.upsert_draft(cs)
    return cs


def reset_edit(cs: CompanyState) -> CompanyState:
    cs.edited_email = None
    cs.subject = cs.machine_subject
    cs.final_email = cs.machine_email
    cs.state = State.drafted
    store.upsert_draft(cs)
    return cs


# ---------------------------------------------------------------------------
# follow-up draft
# ---------------------------------------------------------------------------

def _followup_subject(original_subject: str) -> str:
    subj = (original_subject or "").strip()
    if not subj:
        return "Re:"
    return subj if subj.lower().startswith("re:") else f"Re: {subj}"


def draft_followup(provider: Provider, fu, *, reuse_cache: bool = True,
                   voice_override: str | None = None) -> CompanyState:
    """Materialise a follow-up email as a normal CompanyState so it flows through the SAME
    draft -> approve -> stage(.eml) path as an initial email. It reuses the parent's research cache
    (never re-searches) and resolves a FOLLOW-UP-kind voice (routed by the same situation), then
    runs that voice through the ordinary block machinery with follow-up context (the original email
    + step + follow-up floor). So the follow-up voices are fully editable in the Voices UI and their
    blocks/style/evidence take effect exactly like outreach voices.

    Keyed by fu.draft_slug = f"{parent_slug}__f{step}"; errors land in State.error like draft_one.
    """
    slug = fu.draft_slug or f"{fu.parent_slug}__f{fu.step}"
    cs = store.get_draft(slug) or CompanyState(slug=slug, name=fu.name, website=fu.website,
                                               ref=f"follow-up #{fu.step}")
    cs.ref = f"follow-up #{fu.step}"
    try:
        # 1. reuse the parent's research cache (no re-search); tolerate its absence
        cache = None
        if reuse_cache:
            cache = store.load_cache(fu.parent_slug)
            if cache is not None:
                cache = research_mod._sanitize_cache(cache)
        cache = cache or {}
        cs.cache = cache
        company = cache.get("company") or {}
        cs.role_exists = company.get("role_exists")
        cs.company_size = company.get("company_size")

        # 2. resolve a FOLLOW-UP voice (own set), routed by the same situation as the original
        voice_id = resolve_followup_voice(cache, voice_override or fu.voice)
        vdef = store.get_custom_voice(voice_id) if voice_id else None
        if vdef is None:
            S.ensure_seeded()
            vdef = (store.get_custom_voice(voice_id) if voice_id else None) \
                or (store.list_custom_voices(kind="followup") or [None])[0]
        if vdef is None:
            raise RuntimeError("no follow-up voice available to draft with")
        cs.voice = vdef.id

        # 3. fact pool + evidence (for the ONE new angle) — same engine path as draft_one
        spec = de.prepare(cache)
        spec["allow_dashes"] = bool(getattr(vdef, "allow_dashes", False))
        spec["what_they_do"] = company.get("what_they_do", "")
        spec["city"] = company.get("city", "") or company.get("location", "")
        ev = vdef.evidence
        prefs = dict(prefer=ev.prefer, pin=ev.pin, exclude=ev.exclude, weights=ev.category_weights)
        selected = de.select_evidence(cache, count=ev.count, **prefs)
        ranked = de.rank_evidence(cache, **prefs)
        shortlist = [e for e in ranked if e.get("_score", 0) > 0 or e.get("_pinned")][:5] or ranked[:3]
        spec["evidence"] = selected
        spec["evidence_shortlist"] = [e["anchor"] for e in shortlist]
        spec["custom_facts"] = list(ev.custom_facts or [])
        spec["allowed_facts"] = _build_allowed_facts(cache, selected, shortlist, vdef)
        spec["send_to"] = fu.contact_email or (cache.get("contact") or {}).get("email", "")
        cs.spec = spec

        # 4. compose through the block machinery WITH follow-up context
        followup_ctx = {"original_subject": fu.original_subject,
                        "original_body": fu.original_body, "step": fu.step}
        tokens = compose_mod.derive_tokens(spec, vdef.variables)
        email, parts, body_text = compose_mod.produce_email(
            provider, vdef, spec, tokens, shortlist, followup=followup_ctx)

        # 5. subject: the follow-up voice may set one; default to same-thread "Re:" (best practice)
        voiced_subject = compose_mod.render(vdef.subject, tokens).strip()
        cs.subject = voiced_subject or _followup_subject(fu.original_subject)
        cs.machine_subject = cs.subject
        cs.machine_body = body_text or email
        cs.machine_email = email
        cs.final_email = email
        cs.edited_email = None
        cs.edited_body = None

        cs.notes = validate_mod.floor_notes(spec, email, parts, vdef)
        cs.contact_unverified = validate_mod.contact_unverified(cache) if cache else False
        cs.research_capped = False
        cs.status_pill = f"follow-up #{fu.step}"
        cs.state = State.drafted
        cs.error = None
    except Exception as e:
        cs.state = State.error
        cs.error = f"{type(e).__name__}: {e}"
    store.upsert_draft(cs)
    return cs
