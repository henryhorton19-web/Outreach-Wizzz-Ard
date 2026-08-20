"""FastAPI server: a small JSON API + static SPA. All engine calls and all model calls happen
here (the engine is imported; model calls go through the provider layer).

Security for a localhost server that holds the user's API key:
  * bind 127.0.0.1 only (set by the launcher);
  * every /api/* request must carry the per-launch session token (embedded in the served HTML);
  * the Host header must be localhost/127.0.0.1 (defence vs DNS rebinding);
  * no CORS; the key is never logged, never stored in state or audit records.
"""
from __future__ import annotations

import random
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, Body, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import settings as S
from . import keys
from . import ingest as ingest_mod
from . import store
from . import audit as audit_mod
from . import apollo as apollo_mod
from . import tracker as tracker_mod
from . import pipeline
from . import edit_ledger
from . import attachments as attach_mod
from . import followups as followups_mod
from . import outbox
from .models import (BatchState, CompanyState, State, DRAFT_SLOT_STATES, CustomVoice, CustomSourcingPrompt,
                     FACT_SCOPES, BLOCK_LENGTHS, BLOCK_MODES, FollowUpStatus,
                     SentItem, ReplyState, AddressCandidate)
from .sourcing import research_job as sourcing_job_mod
from . import suppression as suppression_mod
from . import voice_stats as voice_stats_mod
from . import voice_learning
from . import exemplars
from . import exemplar_voice
from . import exemplar_replay
from . import preflight
from . import voice_optimize
from . import pipeline_view
from . import outcomes as outcomes_mod
import config as C
from .providers.base import make_provider, ProviderError

import sys

_FROZEN_BASE = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
ROOT_DIR = _FROZEN_BASE or Path(__file__).resolve().parent.parent
UI_DIR = ROOT_DIR / "ui"

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    outbox.sync_historical_outbox()
    yield

app = FastAPI(title="Outreach Wizz-ard", lifespan=lifespan)

# The volatile server state.
# "voice": an optional CustomVoice ID that overrides situation-matching for all drafts this session.
_STATE: dict = {"voice": S.load_settings().last_session_voice or None, "batch": None, "tracker_path": None}


def _batch() -> BatchState | None:
    return _STATE["batch"]


# Loopback hostnames on ANY port. The previous check bound allowed hosts to
# S.load_settings().port, which caused two failures reported together:
#  1. IPv6 loopback ([::1]) was rejected because it was missing entirely.
#  2. A transient read failure in load_settings() rebuilt the list around port
#     8770 regardless of the port actually in use, returning 400 'bad host' on
#     every endpoint (Pipeline, Follow-ups, Performance, Triage) until reload.
_LOOPBACK_NAMES = {"127.0.0.1", "localhost", "[::1]", "::1", "0.0.0.0", "testserver"}


def _is_loopback_host(host_header: str) -> bool:
    if not host_header:
        return True
    hostname = host_header.lower()
    if hostname in _LOOPBACK_NAMES:
        return True
    # Strip port (e.g. 127.0.0.1:8775 -> 127.0.0.1, [::1]:8775 -> [::1])
    if "]:" in hostname:
        hostname = hostname.split("]:")[0] + "]"
    elif ":" in hostname:
        hostname = hostname.split(":")[0]
    return hostname in _LOOPBACK_NAMES


@app.middleware("http")
async def security(request: Request, call_next):
    host = request.headers.get("host") or ""
    if host and not _is_loopback_host(host):
        return JSONResponse({"detail": "bad host"}, status_code=400)
    if request.url.path.startswith("/api/"):
        token = request.headers.get("x-wizzard-token") or request.headers.get("x-paris-token")
        if token != S.SESSION_TOKEN:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    """Return the fault instead of a bare "Internal Server Error".

    87 routes had no generic handler, so any unhandled exception reached the UI as
    an opaque 500 with the traceback only on stderr. That is how a broken voice
    validator looked identical to a network failure. Localhost, single user, key
    never in a payload: naming the fault is strictly better than hiding it.
    """
    import traceback as _tb
    _tb.print_exc()
    return JSONResponse(
        {"detail": f"{type(exc).__name__}: {exc}", "path": request.url.path},
        status_code=500,
    )


@app.exception_handler(store.StorageError)
async def _storage_error_handler(request: Request, exc: store.StorageError):
    """A rejected list id is a bad request, not a server fault.

    store._queue_file validates list_id (it arrives from a query parameter), and
    without this handler that validation surfaced as an unhandled 500 traceback.
    """
    return JSONResponse({"detail": str(exc)}, status_code=400)


def _provider():
    st = S.load_settings()
    if st.provider == "stub":
        return make_provider("stub", None)
    key = keys.get_key(st.provider)
    if not key:
        raise HTTPException(status_code=400,
                            detail=f"no API key for provider '{st.provider}' — set it first")
    try:
        base_model = st.gemini_model if st.provider == "gemini" else st.anthropic_model
        return make_provider(st.provider, key, model=base_model)
    except ProviderError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _provider_optional():
    """A provider for background learning that NEVER raises: returns the stub when configured, a
    real provider when a key exists, else None (the learner then uses its deterministic offline
    path). Learning must not fail an approve just because no API key is set."""
    try:
        st = S.load_settings()
        if st.provider == "stub":
            return make_provider("stub", None)
        key = keys.get_key(st.provider)
        if not key:
            return None
        base_model = st.gemini_model if st.provider == "gemini" else st.anthropic_model
        return make_provider(st.provider, key, model=base_model)
    except Exception:
        return None


def _routing_reason(cs: CompanyState) -> str:
    """A quiet, human 'why this voice / why this contact' line (Phase 1d). Explains the auto-route
    so the operator can catch a mis-route before approving. Empty when nothing routed yet."""
    if not cs.voice:
        return ""
    v = store.get_custom_voice(cs.voice)
    vlabel = (v.display_name if v else cs.voice)
    bits = []
    if cs.role_exists is True:
        bits.append("role exists")
    elif cs.role_exists is False:
        bits.append("no named role")
    if cs.company_size:
        bits.append(f"{cs.company_size} company")
    contact = (cs.cache or {}).get("contact") or {}
    who = contact.get("title") or contact.get("name") or ""
    conf = (contact.get("email_confidence") or "").strip()
    parts = [f"Routed to {vlabel}"]
    if bits:
        parts.append(" — " + ", ".join(bits))
    if who:
        parts.append(f" · contact: {who}")
        if conf:
            parts.append(f" ({conf} confidence)")
    return "".join(parts)


def _cs_public(cs: CompanyState) -> dict:
    cache = cs.cache or {}
    contact = cache.get("contact") or {}
    spec = cs.spec or {}
    recent = cache.get("recent_point") or {}
    # proofs WITH staleness (Phase 1c) — dot+word rendered in the drawer; keep plain list too
    proofs_detailed = [
        {"fact": p.get("fact", ""), "staleness": (p.get("staleness") or "").strip().lower(),
         "source": p.get("source", "")}
        for p in (cache.get("proof_points") or []) if isinstance(p, dict) and p.get("fact")
    ]
    return {
        "slug": cs.slug,
        "name": cs.name,
        "website": cs.website,
        # Two distinct answers, published separately. `company_domain` is identity; `recipient_domain`
        # is delivery. The UI must render the first wherever it says "website".
        "company_domain": pipeline.company_domain(cs),
        "draft_confidence": dict(cs.draft_confidence or {}),
        # Plan 31 Stage 4: the UI must offer "add a contact" rather than "retry research" when a human
        # is the only thing that can unblock this target.
        "blockers": (preflight.blockers_detailed(cache) if cs.state == State.error else []),
        "recipient_domain": cs.recipient_domain or (cache.get("company") or {}).get("resolved_domain", ""),
        "domain_source": (cache.get("company") or {}).get("domain_source", "given" if cs.recipient_domain else "unresolved"),
        "ref": cs.ref,
        "state": cs.state.value,
        "error": cs.error,
        "voice": cs.voice or "",
        "why_voice": _routing_reason(cs),
        "role_exists": cs.role_exists,
        "company_size": cs.company_size,
        "subject": cs.subject,
        "machine_subject": cs.machine_subject,
        "cost": {"estimate": round(float(cs.cost_estimate or 0.0), 6),
                 "in": cs.tokens_in, "out": cs.tokens_out, "cached": cs.tokens_cached},
        "contact": {
            "name": contact.get("name", ""),
            "title": contact.get("title", ""),
            "email": spec.get("send_to", "") or contact.get("email", ""),
            "email_confidence": contact.get("email_confidence", ""),
            "email_method": contact.get("email_method", ""),
            "email_source_url": contact.get("email_source_url", ""),
        },
        "contact_unverified": cs.contact_unverified,
        "research_capped": cs.research_capped,
        "disqualified": cs.disqualified,
        "research_summary": {
            "what_they_do": (cache.get("company") or {}).get("what_they_do", ""),
            "role_title": (cache.get("company") or {}).get("role_title", ""),
            "role_source": (cache.get("company") or {}).get("role_source", ""),
            "company_size_evidence": (cache.get("company") or {}).get("company_size_evidence", ""),
            "thesis": cache.get("thesis") or {},
            "stated_plan": cache.get("stated_plan") or {},
            "earned_observation": cache.get("earned_observation") or {},
            "traction_signals": [p.get("signal", "") for p in (cache.get("traction_signals") or []) if isinstance(p, dict)],
            "proof_points": [p.get("fact", "") for p in (cache.get("proof_points") or []) if isinstance(p, dict)],
            "proofs_detailed": proofs_detailed,
            "recent_point": recent.get("detail", "") if recent.get("present") else "",
            "situation_read": cache.get("situation_read", ""),
            "evidence_tied": [e.get("name", "") for e in (spec.get("evidence") or [])],
            "research_failures": cache.get("research_failures") or [],
        },
        "links": cache.get("evidence_sources") or [],
        "machine_email": cs.machine_email or "",
        "final_email": cs.final_email or "",
        "edited_email": cs.edited_email or "",
        "was_edited": cs.was_edited(),
        "notes": [n.model_dump() for n in cs.notes],
        "status_pill": cs.status_pill,
        "approved_at": cs.approved_at,
        "updated_at": cs.updated_at,
        "attachments": cs.attachments,
    }


def _batch_public(batch: BatchState) -> dict:
    return {"batch_id": batch.batch_id, "voice": batch.voice,
            "companies": [_cs_public(cs) for cs in batch.ordered()]}


def _persist():
    if _batch():
        store.save_batch(_batch())


# ---- static UI -------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index():
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace('window.__WIZZARD_TOKEN__ = "__WIZZARD_TOKEN__";',
                        f'window.__WIZZARD_TOKEN__ = "{S.SESSION_TOKEN}";')
    html = html.replace('window.__PARIS_TOKEN__ = "__PARIS_TOKEN__";',
                        f'window.__PARIS_TOKEN__ = "{S.SESSION_TOKEN}";')
    return HTMLResponse(html)


if UI_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


def _attachments_public() -> dict:
    st = S.load_settings()
    return {
        "attachments": attach_mod.list_attachments(),
        "default_attachments": st.sanitized()["default_attachments"],
        "attach_by_default": bool(st.attach_by_default),
    }


@app.get("/api/attachments")
def get_attachments():
    return _attachments_public()


@app.post("/api/attachments")
async def upload_attachment(file: UploadFile = File(...)):
    data = await file.read()
    try:
        attach_mod.save_upload(data, file.filename or "")
    except attach_mod.AttachmentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _attachments_public()


@app.delete("/api/attachments/{name}")
def delete_attachment(name: str):
    ok = attach_mod.delete_attachment(name)
    st = S.load_settings()
    if name in st.default_attachments:               # drop it if it was the default
        st.default_attachments = [n for n in st.default_attachments if n != name]
        S.save_settings(st)
    if not ok:
        raise HTTPException(status_code=404, detail="attachment not found")
    return _attachments_public()


# ---- keys + settings -------------------------------------------------------

@app.get("/api/status")
def status():
    st = S.load_settings()
    provider = st.provider
    return {
        "provider": provider,
        "provider_key_present": provider == "stub" or keys.has_key(provider),
        "gemini_key_present": keys.has_key("gemini"),
        "anthropic_key_present": keys.has_key("anthropic"),
        "apollo_key_present": keys.has_key("apollo"),
        "keyring_backend": keys.backend_available(),
        "voice": _STATE["voice"],
        "has_batch": _batch() is not None,
        "active_list": store.active_list_id(),
        "degraded": getattr(store, "DEGRADED", None),
        "tracker_path": _STATE["tracker_path"],
        "settings": st.sanitized(),
        "models": {"gemini": st.gemini_model, "anthropic": st.anthropic_model},
    }


@app.post("/api/keys")
def set_key(payload: dict = Body(...)):
    provider = payload.get("provider")
    key = payload.get("key", "")
    remember = bool(payload.get("remember", True))
    try:
        keys.set_key(provider, key, remember=remember)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "provider": provider, "persisted": keys.backend_available() and remember}


@app.delete("/api/keys/{provider}")
def del_key(provider: str):
    try:
        keys.clear_key(provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.post("/api/settings")
def update_settings(payload: dict = Body(...)):
    st = S.load_settings()
    for k in ("provider", "default_voice", "gemini_model", "anthropic_model", "compose_model",
              "research_temperature", "compose_temperature", "max_web_searches",
              "research_concurrency", "default_attachments", "attach_by_default", "eml_dir",
              "follow_up_enabled", "follow_up_max_steps", "follow_up_delay_days",
              # new-phase settings (MUST be allowlisted or they silently no-op — the plan invariant)
              "cost_prices", "pipeline_stale_days", "voice_stats_min_n",
              "imap_enabled", "imap_host", "imap_port", "imap_username", "imap_ssl",
              "imap_mailboxes", "imap_poll_minutes", "imap_confirm_replies",
              "max_bounce_retries", "send_window_advisory",
              "voice_learning_routing", "voice_explore_epsilon",
              # Layer 4: continuous voice-content learning
              "voice_learning_mode", "voice_learning_min_edits", "voice_learning_max_examples",
              "voice_learning_cooldown_hours", "voice_learning_promote",
              "voice_learning_reflection_model",
              # Sourcing settings ("Find new targets")
              "sourcing_enabled", "sourcing_target_n", "sourcing_max_candidates",
              "sourcing_max_web_per_candidate", "sourcing_budget_usd",
              "sourcing_recency_days", "sourcing_sources", "sourcing_reject_expiry_days",
              # Stage E & G settings
              "exclusion_enabled", "allow_org_voice_learning",
              "exemplar_enabled", "exemplar_corpus_cap", "exemplar_min_for_induction",
              "exemplar_holes_k", "exemplar_support_promote", "exemplar_freeze_window",
              "exemplar_novelty_max", "exemplar_recalibrate_every"):
        if k in payload and payload[k] is not None:
            setattr(st, k, payload[k])
    if st.provider not in S.VALID_PROVIDERS:
        raise HTTPException(status_code=400, detail="invalid provider")
    S.save_settings(st)
    return {"ok": True, "settings": st.sanitized()}


@app.post("/api/tracker_path")
def set_tracker_path(payload: dict = Body(...)):
    """Point the app at the Outreach_Tracker workbook for write-back on approve."""
    p = (payload.get("path") or "").strip()
    _STATE["tracker_path"] = p or None
    return {"ok": True, "tracker_path": _STATE["tracker_path"]}


# ---- session identity ------------------------------------------------------

@app.post("/api/session")
def set_session(payload: dict = Body(...)):
    voice = (payload.get("voice") or "").lower()
    if voice and voice not in S.VALID_VOICES and not store.get_custom_voice(voice):
        raise HTTPException(status_code=400, detail="unknown voice")
    _STATE["voice"] = voice or None
    st = S.load_settings()
    st.last_session_voice = voice or ""
    S.save_settings(st)
    if _batch():
        _batch().voice = voice or None
        _persist()
    return {"ok": True, "voice": voice or None}


# ---- voices ----------------------------------------------------------------

def _validate_voice(v: CustomVoice) -> None:
    if not (v.display_name or "").strip():
        raise HTTPException(status_code=400, detail="voice needs a display name")
    if not v.blocks:
        raise HTTPException(status_code=400, detail="a voice needs at least one block")
    ids: set[str] = set()
    for b in v.blocks:
        if not (b.id or "").strip():
            raise HTTPException(status_code=400, detail="every block needs an id")
        if b.id in ids:
            raise HTTPException(status_code=400, detail=f"duplicate block id '{b.id}'")
        ids.add(b.id)
        if b.mode not in BLOCK_MODES:
            raise HTTPException(status_code=400,
                                detail=f"block '{b.id}' has an invalid mode '{b.mode}' (use fixed or ai)")
        if b.length not in BLOCK_LENGTHS:
            raise HTTPException(status_code=400,
                                detail=f"block '{b.id}' has an invalid length '{b.length}'")
        # Backward-compat: accept old vocabulary during on-disk migration period (Task H3)
        _COMPAT = {"candidate_evidence", "candidate_spine"}
        bad_scope = [s for s in (b.fact_scope or []) if s not in FACT_SCOPES and s not in _COMPAT]
        if bad_scope:
            raise HTTPException(status_code=400,
                                detail=f"block '{b.id}' has unknown fact scope: {', '.join(bad_scope)}")
        if b.mode == "ai" and not (b.guidance or "").strip() and not (b.text or "").strip():
            raise HTTPException(status_code=400,
                                detail=f"the AI block '{b.label or b.id}' needs guidance (or seed text)")
    # NOTE: a per-block `owns_sci_po` flag was removed from Block. This function still
    # read it, so `AttributeError: 'Block' object has no attribute 'owns_sci_po'` made
    # BOTH POST /api/voices and PUT /api/voices/{id} return 500 for every voice with
    # blocks — i.e. the entire Voices editor could not save. The flag has no consumer
    # anywhere in compose/assemble/engine, so the constraint it guarded is meaningless.
    # situations was a routing key when auto_route()/select_voice() existed (deleted in Task A4)
    # -- a value outside VALID_VOICES would silently never match anything and the voice would
    # never be auto-selected. Now that selection is manual only, situations is a descriptive
    # label for the voice picker's browse/filter UI (Part 1's "an optional label" framing) and
    # any string is a legitimate label -- a sector name, a use-case name, anything a human finds
    # useful when scanning the picker. Length-cap only, to keep the label itself sane.
    bad = [s for s in (v.situations or []) if len(s) > 60]
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"situation label(s) too long (max 60 chars): {', '.join(bad)}")
    if int(v.length_min) > int(v.length_max):
        raise HTTPException(status_code=400, detail="length_min cannot exceed length_max")


@app.get("/api/voices")
def get_voices(kind: str = "outreach"):
    """Voices of one kind. kind='outreach' (default) for the initial-email set, kind='followup' for
    the follow-up set, kind='all' for everything. The two sets are edited with the same editor."""
    k = None if kind == "all" else kind
    voices = store.list_custom_voices(kind=k)
    return {"voices": [v.model_dump() for v in voices], "kind": kind}


@app.post("/api/voices/import")
def import_voices(payload: dict = Body(...)):
    """Import and migrate a batch of custom voice dictionaries."""
    from .voice_import import migrate_voice_dict
    raw_list = payload.get("voices")
    if not isinstance(raw_list, list):
        raise HTTPException(status_code=400, detail="expected 'voices' array in payload")
    imported = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        migrated = migrate_voice_dict(item)
        try:
            v = CustomVoice.model_validate(migrated)
            _validate_voice(v)
            store.save_custom_voice(v)
            imported.append(v.id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"failed to import voice '{item.get('id')}': {e}")
    return {"imported": imported, "count": len(imported)}


@app.get("/api/meta")
def get_meta():
    """Editor metadata derived from the live profile + schema: the per-experience tokens, the full
    token palette, the fact scopes, block lengths, and situations. Keeps the UI in sync with code."""
    from .engine_bridge import engine_config as EC
    exps = EC.CANDIDATE_PROFILE.get("experiences", {})
    experiences = [{"key": k, "anchor": e.get("anchor", ""), "optional": bool(e.get("optional"))}
                   for k, e in exps.items()]
    # Derived from the engine rather than hardcoded. This list previously held
    # fourteen entries while derive_tokens emitted twenty-five, so eleven real
    # tokens (observation, link_strength, shared_subject, why, recent_kind and
    # others) worked if typed by hand but were never offered in the editor.
    from .compose import derive_tokens, TOKEN_HELP
    try:
        research_tokens = sorted(derive_tokens({"company": "", "contact_first": ""}).keys())
    except Exception:
        research_tokens = ["contact_first", "contact_full", "company", "role", "role_or_company",
                           "what_they_do", "situation_read", "recent", "recent_short", "proof_1",
                           "proof_2", "city", "candidate_name", "candidate_first"]
    tokens = ([{"token": t, "kind": "research", "help": TOKEN_HELP.get(t, "")} for t in research_tokens]
              + [{"token": e["key"], "kind": "experience", "anchor": e["anchor"], "help": f"your experience anchor for {e['key']}"} for e in experiences]
              + [{"token": "relevant", "kind": "relevant",
                  "anchor": "the model picks the best-fitting experience for the point",
                  "help": "dynamically selected experience anchor"}])
    return {
        "experiences": experiences,
        "tokens": tokens,
        "fact_scopes": list(FACT_SCOPES),
        "block_lengths": list(BLOCK_LENGTHS),
        "block_modes": list(BLOCK_MODES),
        "situations": list(S.VALID_VOICES),
        "candidate_first": (EC.CANDIDATE_PROFILE.get("name", "").split(" ")[0]
                            if EC.CANDIDATE_PROFILE.get("name") else ""),
    }


@app.post("/api/voices")
def create_voice(vdef: CustomVoice = Body(...)):
    _validate_voice(vdef)
    store.save_custom_voice(vdef)
    return {"ok": True, "voice": vdef.model_dump()}


@app.put("/api/voices/{voice_id}")
def update_voice(voice_id: str, vdef: CustomVoice = Body(...)):
    if voice_id != vdef.id:
        raise HTTPException(status_code=400, detail="ID mismatch")
    _validate_voice(vdef)
    store.save_custom_voice(vdef)
    return {"ok": True, "voice": vdef.model_dump()}


@app.delete("/api/voices/{voice_id}")
def delete_voice(voice_id: str):
    # Any voice can be deleted — nothing is protected. Routing stays safe because resolve_voice
    # falls back to default_voice (and re-seeds an empty store), so a situation is never orphaned.
    store.delete_custom_voice(voice_id)
    if _STATE["voice"] == voice_id:
        _STATE["voice"] = None
    return {"ok": True}


@app.get("/api/default_voice")
def get_default_voice():
    return {"default_voice": S.load_settings().default_voice}


@app.post("/api/default_voice")
def set_default_voice(payload: dict = Body(...)):
    vid = (payload.get("voice") or "").strip()
    if vid and not store.get_custom_voice(vid):
        raise HTTPException(status_code=400, detail="unknown voice")
    st = S.load_settings()
    st.default_voice = vid
    S.save_settings(st)
    return {"ok": True, "default_voice": st.default_voice}


# ---- Layer 4: continuous voice learning -----------------------------------
# Suggest/apply/roll-back learned voice changes. All behind the session-token middleware; every
# handler tolerates missing data and never mutates a voice without first snapshotting it.

@app.get("/api/voices/{voice_id}/learning")
def get_voice_learning(voice_id: str):
    """Status for the Voices editor's Learning panel: mode, edits-since, pending proposal(s),
    version history, and any live A/B challenger with both reply rates."""
    return voice_learning.learning_status(voice_id)


# ---- Plan 26: exemplar voice management endpoints -----------------------

@app.get("/api/voices/{voice_id}/exemplar/status")
def get_exemplar_status(voice_id: str):
    """Status for an exemplar voice (exemplar count, induced blocks, freeze state, convergence)."""
    return exemplar_voice.status(voice_id)


@app.get("/api/voices/{voice_id}/exemplar/preview")
def preview_exemplar_template(voice_id: str):
    """Preview induced template blocks without mutating the voice."""
    return exemplar_voice.preview(voice_id)


@app.post("/api/voices/{voice_id}/exemplar/induct")
def apply_exemplar_template(voice_id: str):
    """Run template induction and apply the induced blocks to the voice definition (versioned)."""
    res = exemplar_voice.apply_template(voice_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "induction failed"))
    return res


@app.post("/api/voices/{voice_id}/exemplar/unfreeze")
def unfreeze_exemplar_voice(voice_id: str):
    """Unfreeze an exemplar voice so template induction can run again."""
    res = exemplar_voice.unfreeze(voice_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "unfreeze failed"))
    return res


@app.get("/api/voices/{voice_id}/exemplar/replay")
def replay_exemplar_voice(voice_id: str):
    """Run an offline replay over stored exemplars to measure effort reduction."""
    res = exemplar_replay.run_replay(voice_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "replay failed"))
    return res


@app.post("/api/voices/{voice_id}/learn")
def learn_voice_now(voice_id: str):
    """Manually run a learning cycle now and return a proposal (does NOT apply it). Works offline
    (deterministic heuristic) and online (reflection call)."""
    if not store.get_custom_voice(voice_id):
        raise HTTPException(status_code=404, detail="unknown voice")
    prop = voice_learning.build_proposal(_provider_optional(), voice_id)
    if not prop:
        return {"ok": True, "proposal": None,
                "message": "Not enough edits yet, or nothing consistent to learn."}
    return {"ok": True, "proposal": prop}


@app.post("/api/voices/{voice_id}/proposals/{proposal_id}/apply")
def apply_voice_proposal(voice_id: str, proposal_id: str):
    res = voice_learning.apply_proposal(proposal_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "apply failed"))
    return res


@app.post("/api/voices/{voice_id}/proposals/{proposal_id}/reject")
def reject_voice_proposal(voice_id: str, proposal_id: str):
    return {"ok": voice_learning.reject_proposal(proposal_id)}


@app.get("/api/voices/{voice_id}/history")
def get_voice_history(voice_id: str):
    return {"voice_id": voice_id, "versions": store.list_voice_versions(voice_id)}


# ---- candidate profile endpoints -------------------------------------------

@app.get("/api/profiles")
def get_profiles():
    return {
        "active": C.ProfileStore.active_profile_id(),
        "profiles": C.ProfileStore.list_profiles()
    }


@app.post("/api/profiles")
def create_profile_endpoint(payload: dict = Body(...)):
    pid = payload.get("id") or ""
    name = payload.get("name") or pid
    copy_from = payload.get("copy_from")
    try:
        prof = C.ProfileStore.create_profile(pid, name, copy_from=copy_from)
        return {"ok": True, "active": C.ProfileStore.active_profile_id(), "profiles": C.ProfileStore.list_profiles()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/profiles/{id}/activate")
def activate_profile_endpoint(id: str):
    ok = C.ProfileStore.set_active_profile(id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"profile '{id}' not found")
    return {"ok": True, "active": C.ProfileStore.active_profile_id(), "profile": C.ProfileStore.load()}


@app.delete("/api/profiles/{id}")
def delete_profile_endpoint(id: str):
    ok = C.ProfileStore.delete_profile(id)
    if not ok:
        raise HTTPException(status_code=400, detail=f"cannot delete profile '{id}' (active or default)")
    return {"ok": True, "active": C.ProfileStore.active_profile_id(), "profiles": C.ProfileStore.list_profiles()}


@app.get("/api/profile")
def get_candidate_profile(id: str | None = None):
    return C.ProfileStore.load(profile_id=id)


@app.post("/api/profile")
async def save_candidate_profile(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid profile payload")

    # Merge against current store so partial payloads (e.g. from the modal) don't wipe out experiences/facts
    cur = C.ProfileStore.load()
    merged = dict(cur)
    merged.update(data)

    name = (merged.get("name") or "").strip()
    one_line = (merged.get("one_line") or "").strip()
    spine = (merged.get("spine") or "").strip()
    experiences = merged.get("experiences")
    missing = []
    if not name: missing.append("name")
    if not one_line: missing.append("one_line")
    if not spine: missing.append("spine")
    if not experiences or not isinstance(experiences, dict): missing.append("experiences")

    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Profile validation failed. Required non-empty fields: {', '.join(missing)}"
        )

    C.ProfileStore.save(merged)
    return {"ok": True, "profile": C.ProfileStore.load()}


@app.get("/api/profile/export_resume")
def export_candidate_resume():
    prof = C.ProfileStore.load()
    exps = prof.get("experiences", {})
    work = []
    for k, exp in exps.items():
        facts = exp.get("facts", [])
        xyz = exp.get("xyz", {
            "action": facts[0] if facts else exp.get("anchor", ""),
            "metric": facts[1] if len(facts) > 1 else "",
            "method": ", ".join(exp.get("bridges", []))
        })
        work.append({
            "id": k,
            "name": exp.get("name", k),
            "position": exp.get("title", "Role"),
            "startDate": exp.get("when", "").split("-")[0].strip() if exp.get("when") else "",
            "endDate": exp.get("when", "").split("-")[1].strip() if exp.get("when") and "-" in exp.get("when") else "present",
            "summary": exp.get("anchor", ""),
            "highlights": facts,
            "xyz": xyz,
            "bridges": exp.get("bridges", [])
        })
    return {
        "$schema": "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json",
        "basics": {
            "name": prof.get("name", ""),
            "label": prof.get("one_line", ""),
            "email": prof.get("email", ""),
            "phone": prof.get("phone", ""),
            "url": prof.get("linkedin", ""),
            "summary": prof.get("spine", "")
        },
        "work": work,
        "standing_key": prof.get("standing_key", "anchor_co"),
        "fallback_key": prof.get("fallback_key", "fund_co")
    }


@app.post("/api/profile/import_resume")
async def import_candidate_resume(request: Request):
    resume = await request.json()
    if not isinstance(resume, dict):
        raise HTTPException(status_code=400, detail="invalid resume payload")

    basics = resume.get("basics", {})
    work_list = resume.get("work", [])
    cur_prof = C.ProfileStore.load()
    experiences = {}

    for idx, item in enumerate(work_list):
        k = item.get("id") or re.sub(r"[^a-z0-9]", "_", (item.get("name") or f"exp_{idx}").lower())
        facts = item.get("highlights") or []
        when_str = f"{item.get('startDate', '')} - {item.get('endDate', 'present')}".strip(" -")
        xyz = item.get("xyz") or {
            "action": facts[0] if facts else item.get("summary", ""),
            "metric": facts[1] if len(facts) > 1 else "",
            "method": ", ".join(item.get("bridges", []))
        }
        experiences[k] = {
            "name": item.get("name") or "Company",
            "title": item.get("position") or item.get("title") or "Role",
            "when": when_str or "2026 - present",
            "tense": "present" if item.get("endDate", "").lower() == "present" else "past",
            "anchor": item.get("summary") or item.get("anchor") or f"{xyz.get('action', '')} {xyz.get('metric', '')}".strip(),
            "facts": facts if facts else [xyz.get("action", "Shipped core features")],
            "bridges": item.get("bridges") or ([b.strip() for b in xyz.get("method", "").split(",") if b.strip()] if isinstance(xyz.get("method"), str) else ["builds"]),
            "xyz": xyz
        }

    updated = {
        **cur_prof,
        "name": basics.get("name") or cur_prof.get("name", "Candidate"),
        "email": basics.get("email") or cur_prof.get("email", ""),
        "phone": basics.get("phone") or cur_prof.get("phone", ""),
        "linkedin": basics.get("url") or cur_prof.get("linkedin", ""),
        "one_line": basics.get("label") or cur_prof.get("one_line", ""),
        "spine": basics.get("summary") or cur_prof.get("spine", ""),
        "standing_key": resume.get("standing_key") or (list(experiences.keys())[0] if experiences else "anchor_co"),
        "fallback_key": resume.get("fallback_key") or (list(experiences.keys())[1] if len(experiences) > 1 else "fund_co"),
        "experiences": experiences if experiences else cur_prof.get("experiences", {})
    }

    C.ProfileStore.save(updated)
    return {"ok": True, "profile": C.ProfileStore.load()}


@app.post("/api/profile/reset")
def reset_candidate_profile():
    prof = C.ProfileStore.reset_to_default()
    return {"ok": True, "profile": prof}


@app.post("/api/voices/{voice_id}/rollback")
def rollback_voice(voice_id: str, payload: dict = Body(...)):
    ts = (payload.get("ts") or "").strip()
    if not ts:
        raise HTTPException(status_code=400, detail="a snapshot ts is required")
    v = store.restore_voice_version(voice_id, ts)
    if v is None:
        raise HTTPException(status_code=404, detail="unknown snapshot")
    return {"ok": True, "voice": v.model_dump()}


@app.post("/api/voices/{voice_id}/optimize")
def optimize_voice(voice_id: str):
    """Phase C: offline batch optimise over the voice's full edit corpus; spawns the best candidate
    as an A/B challenger (never a blind overwrite)."""
    res = voice_optimize.optimize(_provider_optional(), voice_id)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "optimize failed"))
    return res


@app.post("/api/voices/arbitrate")
def arbitrate_voices():
    """Phase C: resolve any live A/B — promote a challenger whose reply rate separates above its
    champion, retire one that clearly loses, else keep testing. Uses the existing reply-rate bandit."""
    return {"ok": True, "decisions": voice_learning.arbitrate()}


# ---- ingest ----------------------------------------------------------------

def _exclusion_blocked(slug: str) -> bool:
    """Return True iff this slug is in the permanent exclusion set and the toggle is enabled."""
    st = S.load_settings()
    if not getattr(st, "exclusion_enabled", True):
        return False
    return store.is_excluded(slug)


def _ingest_to_queue(rows: list[dict], list_id: str = "default") -> dict:
    existing = store.queue_slugs(list_id=list_id) | {cs.slug for cs in store.load_drafts()}
    contacted_domains = suppression_mod.already_contacted_domains()
    already, added, over_cap = [], [], []
    suppressed, contacted, excluded_blocked = [], [], []   # error-prevention surfacing
    current = store.queue_count(list_id=list_id)
    to_add = []
    for r in rows:
        slug = r["slug"]
        if slug in existing:
            already.append(r["name"])
            continue
        # permanent exclusion check (Stage E): already-approved entities are permanently blocked
        if _exclusion_blocked(slug):
            excluded_blocked.append(r["name"])
            continue
        # suppression / do-not-contact check (by any email/domain the ingest row carries)
        ref_email = (r.get("email") or "").strip()
        sup_hit, sup_reason = (suppression_mod.is_suppressed(ref_email) if ref_email else (False, ""))
        if sup_hit:
            suppressed.append({"name": r["name"], "reason": sup_reason})
            continue
        # archive-aware dedup: warn if we've already emailed this domain (skip, allow "add anyway")
        dom = ""
        if ref_email and "@" in ref_email:
            dom = ref_email.split("@", 1)[1].lower().removeprefix("www.")
        elif r.get("website"):
            # A sheet of names + websites carries no email, so without this the archive dedup never
            # fires on an upload. Host only: strip scheme, path, and a leading www.
            _w = str(r["website"]).split("://", 1)[-1]
            dom = _w.split("/", 1)[0].lower().removeprefix("www.")
        if dom and dom in contacted_domains:
            contacted.append(r["name"])
            continue
        if current >= store.QUEUE_CAP:
            over_cap.append(r["name"])
            continue
        to_add.append(r)
        existing.add(slug)
        current += 1
        added.append(r["name"])
    if to_add:
        store.upsert_queue_batch(to_add, list_id=list_id)
    return {"queue": store.load_queue(list_id=list_id), "added": len(added),
            "skipped_duplicates": already, "over_cap": over_cap,
            "suppressed": suppressed, "already_contacted": contacted,
            "excluded_blocked": excluded_blocked}


@app.post("/api/ingest")
def ingest(payload: dict = Body(...)):
    rows = ingest_mod.parse_names(payload.get("text", ""))
    list_id = payload.get("list_id", "default")
    if not rows:
        raise HTTPException(status_code=400, detail="no target names found")
    return _ingest_to_queue(rows, list_id=list_id)


@app.post("/api/ingest_file")
async def ingest_file(file: UploadFile = File(...), list_id: str = ""):
    data = await file.read()
    fn = (file.filename or "").lower()
    if fn.endswith(".xlsx"):
        rows = ingest_mod.parse_xlsx_bytes(data)
    else:
        rows = ingest_mod.parse_csv_bytes(data)
    if not rows:
        raise HTTPException(status_code=400, detail="no target names found in file")
    return _ingest_to_queue(rows, list_id=list_id or store.active_list_id())


# ---- Custom Sourcing Prompts -----------------------------------------------

@app.get("/api/sourcing_prompts")
def get_sourcing_prompts():
    return {"prompts": [sp.model_dump() for sp in store.list_custom_sourcing_prompts()]}


@app.post("/api/sourcing_prompts")
def create_sourcing_prompt(pdef: CustomSourcingPrompt = Body(...)):
    if not (pdef.display_name or "").strip():
        raise HTTPException(status_code=400, detail="prompt needs a display name")
    store.save_custom_sourcing_prompt(pdef)
    return {"ok": True, "prompt": pdef.model_dump()}


@app.put("/api/sourcing_prompts/{prompt_id}")
def update_sourcing_prompt(prompt_id: str, pdef: CustomSourcingPrompt = Body(...)):
    if prompt_id != pdef.id:
        raise HTTPException(status_code=400, detail="ID mismatch")
    if not (pdef.display_name or "").strip():
        raise HTTPException(status_code=400, detail="prompt needs a display name")
    store.save_custom_sourcing_prompt(pdef)
    return {"ok": True, "prompt": pdef.model_dump()}


@app.delete("/api/sourcing_prompts/{prompt_id}")
def delete_sourcing_prompt(prompt_id: str):
    store.delete_custom_sourcing_prompt(prompt_id)
    return {"ok": True}


@app.post("/api/sourcing_prompts/{prompt_id}/duplicate")
def duplicate_sourcing_prompt(prompt_id: str):
    from app.sourcing.normalize import canonicalize_name
    existing = store.get_custom_sourcing_prompt(prompt_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Preset not found")
    new_name = f"{existing.display_name} (copy)"
    base_slug = canonicalize_name(new_name).replace("-", "_")
    new_id = base_slug
    idx = 1
    while store.get_custom_sourcing_prompt(new_id) is not None:
        new_id = f"{base_slug}_{idx}"
        idx += 1

    dup_data = existing.model_dump()
    dup_data.update({
        "id": new_id,
        "display_name": new_name,
        "seeded_from": existing.seeded_from or existing.id,
        "last_run_at": "",
        "total_candidates_seen": 0,
    })
    dup = CustomSourcingPrompt(**dup_data)
    store.save_custom_sourcing_prompt(dup)
    return {"ok": True, "prompt": dup.model_dump()}


@app.post("/api/sourcing_prompts/{prompt_id}/reset")
def reset_sourcing_prompt(prompt_id: str):
    import json
    existing = store.get_custom_sourcing_prompt(prompt_id)
    if not existing or not existing.seeded_from:
        raise HTTPException(status_code=404, detail="Preset not found or not seeded")
    seed_file = Path(__file__).resolve().parent / "seed_sourcing_prompts" / f"{existing.seeded_from}.json"
    if not seed_file.exists():
        raise HTTPException(status_code=404, detail="Seed prompt file not found")
    seed_raw = json.loads(seed_file.read_text(encoding="utf-8"))
    seed_raw["id"] = existing.id
    reset_preset = CustomSourcingPrompt(**seed_raw)
    store.save_custom_sourcing_prompt(reset_preset)
    return {"ok": True, "prompt": reset_preset.model_dump()}


@app.get("/api/sourcing/sources")
def get_sourcing_sources():
    labels = {
        "grounded_search": "Grounded Web Search",
        "techeu_funding_feed": "Tech.eu Funding Feed",
        "franceinvest_directory": "France Invest Directory",
    }
    from app.sourcing.harvest import AVAILABLE_HARVESTERS
    return {"sources": [{"id": k, "label": labels.get(k, k)} for k in AVAILABLE_HARVESTERS.keys()]}


@app.post("/api/sourcing/preview_query")
def preview_sourcing_query(pdef: CustomSourcingPrompt = Body(...)):
    from app.sourcing.harvest.grounded_search import GroundedSearchHarvester
    h = GroundedSearchHarvester()
    query = h.build_query(pdef, recency_days=pdef.recency_days)
    return {"query": query}


@app.post("/api/source/research/dry_run")
def dry_run_sourcing_research(payload: dict = Body(...)):
    from app.sourcing.harvest.grounded_search import GroundedSearchHarvester
    from app.sourcing.harvest import AVAILABLE_HARVESTERS
    from app.sourcing.verify import verify_candidate

    st = S.load_settings()
    prompt_id = payload.get("sourcing_prompt_id")
    custom_prompt = store.get_custom_sourcing_prompt(prompt_id) if prompt_id else None
    recency_days = payload.get("recency_days") or (custom_prompt.recency_days if custom_prompt else st.sourcing_recency_days)
    sources = payload.get("sources") or (custom_prompt.sources if custom_prompt else st.sourcing_sources)

    h = GroundedSearchHarvester()
    query = h.build_query(custom_prompt, recency_days)

    harvested_raw = []
    for s_id in sources:
        adapter = AVAILABLE_HARVESTERS.get(s_id)
        if adapter:
            raw_items = adapter.harvest(recency_days=recency_days, max_items=10, custom_prompt=custom_prompt)
            harvested_raw.extend(raw_items)

    survived = []
    for raw in harvested_raw:
        verified = verify_candidate(raw, custom_prompt=custom_prompt)
        if verified.get("verdict") != "reject":
            survived.append(verified["name"])

    return {
        "dry_run": True,
        "query": query,
        "sources": sources,
        "raw_harvested_count": len(harvested_raw),
        "survived_count": len(survived),
        "sample_candidate_names": survived[:3],
    }


@app.get("/api/sourcing_prompts/export")
def export_sourcing_prompts():
    prompts = store.list_custom_sourcing_prompts()
    return {"prompts": [p.model_dump() for p in prompts]}


@app.post("/api/sourcing_prompts/import")
def import_sourcing_prompts(payload: dict = Body(...)):
    items = payload.get("prompts") or []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="Invalid import payload: 'prompts' array expected")

    imported_count = 0
    duplicate_count = 0
    invalid_count = 0

    for raw in items:
        if not isinstance(raw, dict):
            invalid_count += 1
            continue
        pid = raw.get("id")
        display_name = raw.get("display_name")
        if not pid or not display_name or not (display_name or "").strip():
            invalid_count += 1
            continue
        if store.get_custom_sourcing_prompt(pid) is not None:
            duplicate_count += 1
            continue
        try:
            sp = CustomSourcingPrompt(**raw)
            store.save_custom_sourcing_prompt(sp)
            imported_count += 1
        except Exception:
            invalid_count += 1

    return {
        "ok": True,
        "imported": imported_count,
        "duplicates": duplicate_count,
        "invalid": invalid_count,
    }

@app.post("/api/source/research")
def start_sourcing_research(payload: dict = Body(...)):
    st = S.load_settings()
    prompt_id = payload.get("sourcing_prompt_id")
    custom_prompt = store.get_custom_sourcing_prompt(prompt_id) if prompt_id else None
    preset_target = getattr(custom_prompt, "target_n", 0) if custom_prompt else 0
    target_n = payload.get("target_n") or preset_target or st.sourcing_target_n
    max_candidates = payload.get("max_candidates", st.sourcing_max_candidates)
    recency_days = payload.get("recency_days", st.sourcing_recency_days)
    sources = payload.get("sources") or st.sourcing_sources

    job = sourcing_job_mod.start_sourcing_job(
        settings=st,
        target_n=target_n,
        max_candidates=max_candidates,
        recency_days=recency_days,
        sources=sources,
        sourcing_prompt_id=prompt_id,
    )
    return {"ok": True, "job": job}


@app.get("/api/source/research/last")
def get_last_sourcing_research():
    last = sourcing_job_mod.get_last_run()
    return {"last_run": last}


@app.get("/api/source/research/{job_id}")
def get_sourcing_research_job(job_id: str):
    job = sourcing_job_mod.get_active_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="sourcing job not found")
    return {"job": job}


@app.post("/api/source/research/{job_id}/cancel")
def cancel_sourcing_research_job(job_id: str):
    ok = sourcing_job_mod.cancel_job(job_id)
    return {"ok": ok}


@app.post("/api/source/research/{job_id}/add")
def add_held_sourcing_candidates(job_id: str, payload: dict = Body(...)):
    slugs = payload.get("slugs") or []
    job = sourcing_job_mod.get_active_job(job_id) or sourcing_job_mod.get_last_run()
    if not job:
        raise HTTPException(status_code=404, detail="sourcing job not found")

    to_add = [c for c in job.get("candidates", []) if c.get("canon_slug") in slugs]
    ingest_rows = [{
        "slug": c["canon_slug"],
        "name": c["name"],
        "ref": c.get("website", ""),
        "meta": {"source_id": c.get("discovery", {}).get("source_id", "sourcing")},
    } for c in to_add]

    if ingest_rows:
        list_id = store.active_list_id()
        res = _ingest_to_queue(ingest_rows, list_id=list_id)
        # Record WHERE the rows went so undo reverses the right list even after a switch.
        job.setdefault("added_list_id", list_id)
        job["added_slugs"] = sorted(set(job.get("added_slugs") or []) | {r["slug"] for r in ingest_rows})
        return {"ok": True, "added": res.get("added", 0), "list_id": list_id}
    return {"ok": True, "added": 0}


@app.post("/api/source/research/{job_id}/undo")
def undo_sourcing_research_job(job_id: str):
    res = sourcing_job_mod.undo_sourcing_job(job_id)
    return {"ok": True, "undo": res}


# ---- lists & queue ---------------------------------------------------------

@app.get("/api/lists")
def get_lists():
    return {"active": store.active_list_id(), "lists": store.load_lists()}


@app.post("/api/lists/active")
def set_active_list(payload: dict = Body(...)):
    lid = (payload.get("id") or payload.get("list_id") or "").strip()
    if not any(l["id"] == lid for l in store.load_lists()):
        raise HTTPException(status_code=404, detail="List not found")
    store.set_active_list(lid)
    return {"ok": True, "active": store.active_list_id(), "lists": store.load_lists()}


@app.post("/api/lists")
def create_list(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="List name is required")
    lst = store.create_list(name)
    store.set_active_list(lst["id"])
    return {"ok": True, "list": lst, "active": store.active_list_id(), "lists": store.load_lists()}


@app.delete("/api/lists/{list_id}")
def delete_list(list_id: str):
    if list_id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete default list")
    if not store.delete_list(list_id):
        raise HTTPException(status_code=404, detail="List not found")
    return {"ok": True, "active": store.active_list_id(), "lists": store.load_lists()}


@app.get("/api/queue")
def get_queue(list_id: str = ""):
    # An omitted list_id must degrade to the ACTIVE list, not the literal "default".
    list_id = list_id or store.active_list_id()
    return {"queue": store.load_queue(list_id=list_id)}


@app.post("/api/queue/{slug}/draft")
def queue_to_draft(slug: str, list_id: str = ""):
    # An omitted list_id must degrade to the ACTIVE list, not the literal "default".
    list_id = list_id or store.active_list_id()
    items = store.load_queue(list_id=list_id)
    record = next((r for r in items if r["slug"] == slug), None)
    if not record:
        raise HTTPException(status_code=404, detail="target not in queue")
    active = store.load_drafts()
    active_count = sum(1 for cs in active if cs.state in DRAFT_SLOT_STATES)
    if active_count >= store.DRAFTS_CAP:
        raise HTTPException(status_code=409,
                            detail=f"Drafts full ({store.DRAFTS_CAP} active). Approve or clear one first.")
    store.remove_from_queue(slug, list_id=list_id)
    meta = record.get("meta") or {}
    src_list = record.get("source_list_id") or meta.get("source_list_id") or list_id
    cs = CompanyState(slug=record["slug"], name=record["name"], source_list_id=src_list,
                      ref=record.get("crm_id") or record.get("ref") or None, state=State.input,
                      website=record.get("website") or None)
    store.upsert_draft(cs)
    if not _batch():
        _STATE["batch"] = BatchState(batch_id=uuid.uuid4().hex[:12], voice=_STATE.get("voice"))
    _batch().companies[slug] = cs
    _persist()
    return {"ok": True, "company": _cs_public(cs), "queue": store.load_queue(list_id=list_id)}


@app.put("/api/queue/{slug}/website")
def set_queue_website(slug: str, payload: dict = Body(...), list_id: str = ""):
    """Correct the identity anchor before drafting. A non-URL value is a 400 rather than a silent
    discard: the bulk CSV path drops junk quietly because it is bulk, but a deliberate single edit
    that vanishes would leave the operator believing research is anchored when it is not."""
    list_id = list_id or store.active_list_id()
    raw = str(payload.get("website") or "").strip()
    site = ingest_mod._clean_website(raw) if raw else ""
    if raw and not site:
        raise HTTPException(status_code=400,
                            detail=f"not a website: {raw}. Use a domain or a full URL.")
    if not store.set_queue_website(slug, site, list_id=list_id):
        raise HTTPException(status_code=404, detail="target not in queue")
    return {"ok": True, "website": site, "queue": store.load_queue(list_id=list_id)}


@app.put("/api/companies/{slug}/contact")
def set_company_contact(slug: str, payload: dict = Body(...)):
    """Supply the contact by hand when research could not find one.

    Deliberately usable BEFORE a draft exists, unlike `/email`, which requires `machine_email` to be
    set. Preflight refuses to compose without a contact, so requiring a draft first would make the
    refusal unrecoverable -- which is what the operator hit on a stealth company.

    A name alone is enough to unblock: the letter needs somebody to address, and the address can be
    corrected later. An address on a different domain is accepted and flagged rather than refused, since
    a founder using a personal address is common and the operator can see the flag.
    """
    import re as _re
    cs = store.get_draft(slug)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")

    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    if not name and not email:
        raise HTTPException(status_code=400, detail="give a name, an address, or both")
    if name and name.lower() in preflight.PLACEHOLDER_NAMES:
        raise HTTPException(status_code=400,
                            detail=f"{name!r} is a placeholder, not a person; give a real name")
    if email and not _re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", email, _re.IGNORECASE):
        raise HTTPException(status_code=400, detail=f"not a valid email address: {email}")

    cache = dict(cs.cache or {})
    contact = dict(cache.get("contact") or {})
    if name:
        contact["name"] = name
        if payload.get("title"):
            contact["title"] = str(payload["title"]).strip()
    mismatch = False
    if email:
        contact["email"] = email
        contact["email_method"] = "manual"
        contact["email_confidence"] = "high"
        contact["contact_verified"] = True
        contact["email_source_url"] = ""
        cs.recipient_domain = email.rsplit("@", 1)[1]
        anchor = pipeline.company_domain(cs)
        mismatch = bool(anchor and cs.recipient_domain != anchor
                        and not cs.recipient_domain.endswith("." + anchor))
    cache["contact"] = contact
    cs.cache = cache
    cs.contact_unverified = not bool(email)

    remaining = preflight.blockers(cache)
    if not remaining and cs.state == State.error:
        cs.state = State.researched
        cs.error = None
        cs.status_pill = "ready to draft"
    store.upsert_draft(cs)
    _persist()
    return {"ok": True, "domain_mismatch": mismatch, "blockers": remaining,
            "company": _cs_public(cs)}


@app.put("/api/companies/{slug}/website")
def set_company_website(slug: str, payload: dict = Body(...)):
    """Correct the identity anchor at any point before approval.

    This used to 409 once research had run, on the grounds that the cache was built around the old
    site. That was right while nothing could invalidate the cache; it is wrong now. Correcting the
    website IS the moment the stale cache should go, and refusing here left the operator with no
    recovery from a name collision except deleting the target. Blocked only from `approved` onward,
    where an approved copy already exists and re-researching would silently change what was signed
    off."""
    cs = store.get_draft(slug)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    if cs.state in (State.approved, State.ready):
        raise HTTPException(status_code=409,
                            detail="this target is already approved; reset it before changing its site")
    raw = str(payload.get("website") or "").strip()
    site = ingest_mod._clean_website(raw) if raw else ""
    if raw and not site:
        raise HTTPException(status_code=400, detail=f"not a website: {raw}")
    cs.website = site or None
    # Setting a site that contradicts the cached research is the operator saying "you profiled the
    # wrong company". Drop the cache and the machine-resolved domain so the next draft re-researches
    # against the new anchor instead of silently reusing the old profile.
    invalidated = False
    if site and pipeline.cache_contradicts_website(cs, cs.cache or store.load_cache(slug) or {}):
        try:
            store.clear_cache(slug)
        except Exception:
            pass
        cs.cache = None
        cs.recipient_domain = ""
        cs.state = State.input
        invalidated = True
    store.upsert_draft(cs)
    _persist()
    return {"ok": True, "invalidated": invalidated, "company": _cs_public(cs)}


@app.delete("/api/queue/{slug}")
def remove_from_queue(slug: str, list_id: str = ""):
    # An omitted list_id must degrade to the ACTIVE list, not the literal "default".
    list_id = list_id or store.active_list_id()
    if not store.remove_from_queue(slug, list_id=list_id):
        raise HTTPException(status_code=404, detail="not in queue")
    return {"ok": True, "queue": store.load_queue(list_id=list_id)}


@app.post("/api/queue/clear")
def clear_queue(list_id: str = ""):
    # An omitted list_id must degrade to the ACTIVE list, not the literal "default".
    list_id = list_id or store.active_list_id()
    return {"ok": True, "cleared": store.clear_queue(list_id=list_id)}


# ---- drafts + archive ------------------------------------------------------

@app.get("/api/drafts")
def get_drafts():
    return {"drafts": [_cs_public(cs) for cs in store.load_drafts()]}


@app.post("/api/drafts/clear")
def clear_drafts():
    return {"ok": True, "cleared": store.clear_drafts()}


@app.get("/api/archive")
def get_archive():
    # Join each Sent card to its SentItem so the card can show + change the outcome inline.
    # Prefer the exact stored sent_id; fall back to the newest SentItem for that slug (covers
    # archive records saved before sent_id existed). Never mutates the stored archive.
    sents = store.load_sent_items()
    by_id = {s.id: s for s in sents}
    newest_by_slug: dict = {}
    for s in sorted(sents, key=lambda x: (x.approved_at or x.created_at or "")):
        newest_by_slug[s.slug] = s          # ascending sort => last write is the newest
    out = []
    for rec in store.load_archive():
        r = dict(rec)                        # copy: do not mutate the stored record
        si = by_id.get(rec.get("sent_id")) or newest_by_slug.get(rec.get("slug"))
        if si is not None:
            r["sent_id"] = si.id
            r["reply_state"] = si.reply_state.value if hasattr(si.reply_state, "value") else si.reply_state
            r["pipeline_flag"] = si.pipeline_flag
            r["outcome_source"] = getattr(si, "outcome_source", "auto")
        else:
            r["sent_id"] = ""                # no send record found -> no inline controls
        out.append(r)
    return {"archive": out}


@app.post("/api/archive/clear")
def clear_archive():
    return {"ok": True, "cleared": store.clear_archive()}


# ---- export (Phase 1b) -----------------------------------------------------

import datetime as _dt


@app.get("/api/export")
def export(fmt: str = "csv", scope: str = "drafts"):
    fmt = (fmt or "csv").lower()
    scope = scope if scope in ("drafts", "archive") else "drafts"
    date = _dt.date.today().isoformat()
    fname = f"paris_outreach_{scope}_{date}"
    if fmt == "xlsx":
        data = store.export_xlsx_bytes(_batch(), scope=scope)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        fname += ".xlsx"
    else:
        data = store.export_csv_bytes(_batch(), scope=scope)
        media = "text/csv"
        fname += ".csv"
    return Response(content=data, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@app.get("/api/export/count")
def export_count(scope: str = "drafts"):
    scope = scope if scope in ("drafts", "archive") else "drafts"
    return {"scope": scope, "count": store.export_count(scope)}


# ---- cost meter (Phase 1e) -------------------------------------------------

@app.get("/api/cost")
def get_cost():
    from .cost_metrics import compute_metrics
    stats = store.load_session_stats()
    try:
        approved = sum(1 for s in store.load_sent_items() if getattr(s, "approved_at", None))
    except Exception:
        approved = 0
    return compute_metrics(stats, approved=approved)


@app.post("/api/cost/reset")
def reset_cost():
    store.reset_session_stats()
    return {"ok": True}


# ---- pipeline board (Phase 2) ----------------------------------------------

@app.get("/api/pipeline")
def get_pipeline(list_id: str = ""):
    return pipeline_view.assemble(list_id=list_id)


@app.post("/api/pipeline/{slug}/mark")
def mark_pipeline(slug: str, payload: dict = Body(...)):
    flag = (payload.get("flag") or "").strip()
    if flag not in ("no_response", "reopen"):
        raise HTTPException(status_code=400, detail="flag must be no_response or reopen")
    ok = pipeline_view.mark(slug, flag)
    if not ok:
        raise HTTPException(status_code=404, detail="no send found for that target")
    return {"ok": True}


# ---- voice performance (Phase 3) -------------------------------------------

@app.get("/api/voice_stats")
def get_voice_stats(kind: str = "outreach"):
    k = None if kind == "all" else kind
    buckets = voice_stats_mod.rebuild_all(kind=k)
    st = S.load_settings()
    rows = sorted(buckets.values(),
                  key=lambda b: (b.get("reply_rate") if b.get("enough_data") else -1, b["sent"]),
                  reverse=True)
    best = next((b for b in rows if b.get("enough_data")), None)
    return {"voices": rows, "min_n": int(getattr(st, "voice_stats_min_n", 15)),
            "best": best, "kind": kind}


# ---- Stage E: exclusion management endpoints ------------------------------

@app.get("/api/exclusion")
def get_exclusion():
    from app.sourcing.exclude import exclusion_info
    return exclusion_info()


@app.post("/api/exclusion")
def add_exclusion(payload: dict = Body(...)):
    slugs = payload.get("slugs") or []
    if isinstance(slugs, str):
        slugs = [slugs]
    for s in slugs:
        store.add_to_exclusion_set(str(s).strip())
    return {"added": len(slugs), "total": len(store.excluded_slugs())}


# ---- Stage F: bulk export ingestion and screened import --------------------

@app.post("/api/source/import")
def source_import(payload: dict = Body(...)):
    """Import raw pre-parsed rows directly into the queue (no gate screening)."""
    rows = payload.get("rows") or []
    list_id = payload.get("list_id", "default")
    if not rows:
        raise HTTPException(status_code=400, detail="expected 'rows' array")
    queue_rows = []
    from app.slugs import slug as make_slug
    for r in rows:
        name = (r.get("name") or r.get("company_name") or "").strip()
        if not name:
            continue
        queue_rows.append({"slug": make_slug(name), "name": name,
                           "ref": r.get("ref") or r.get("website") or "",
                           "meta": r})
    return _ingest_to_queue(queue_rows, list_id=list_id)


@app.post("/api/source/import_screened")
def source_import_screened(payload: dict = Body(...)):
    """Import rows after applying local gate screening before adding to the queue."""
    from app.sourcing.bulk_export import evaluate_local_gates
    from app.slugs import slug as make_slug
    rows = payload.get("rows") or []
    gates = payload.get("gates") or {}
    list_id = payload.get("list_id", "default")
    if not rows:
        raise HTTPException(status_code=400, detail="expected 'rows' array")
    passed, rejected = evaluate_local_gates(rows, gates)
    queue_rows = []
    for r in passed:
        name = (r.get("name") or r.get("company_name") or "").strip()
        if not name:
            continue
        queue_rows.append({"slug": make_slug(name), "name": name,
                           "ref": r.get("ref") or r.get("website") or "",
                           "meta": r})
    result = _ingest_to_queue(queue_rows, list_id=list_id)
    result["screened_out"] = len(rejected)
    result["reject_reasons"] = [r.get("__reject_reason") for r in rejected]
    return result


# ---- suppression / do-not-contact (Phase 4a) -------------------------------

@app.get("/api/suppressions")
def get_suppressions():
    return {"suppressions": store.load_suppressions()}


@app.post("/api/suppressions")
def add_suppression(payload: dict = Body(...)):
    value = (payload.get("value") or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="value required")
    reason = payload.get("reason") or "manual"
    row = suppression_mod.add(value, reason=reason, source="manual")
    return {"ok": True, "row": row, "suppressions": store.load_suppressions()}


@app.delete("/api/suppressions")
def remove_suppression(payload: dict = Body(...)):
    value = (payload.get("value") or "").strip()
    ok = suppression_mod.remove(value)
    if not ok:
        raise HTTPException(status_code=404, detail="not on the list")
    return {"ok": True, "suppressions": store.load_suppressions()}


@app.post("/api/suppressions/clear")
def clear_suppressions():
    return {"ok": True, "cleared": store.clear_suppressions()}


# ---- snippets (Phase 4b) ---------------------------------------------------

@app.get("/api/snippets")
def get_snippets():
    return {"snippets": store.load_snippets()}


@app.post("/api/snippets")
def save_snippet(payload: dict = Body(...)):
    name = (payload.get("name") or "").strip()
    text = payload.get("text") or ""
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    items = store.load_snippets()
    sid = payload.get("id") or uuid.uuid4().hex[:8]
    items = [s for s in items if s.get("id") != sid]
    items.append({"id": sid, "name": name, "text": text})
    store.save_snippets(items)
    return {"ok": True, "snippets": items}


@app.delete("/api/snippets/{sid}")
def delete_snippet(sid: str):
    items = [s for s in store.load_snippets() if s.get("id") != sid]
    store.save_snippets(items)
    return {"ok": True, "snippets": items}





# ---- manual outcome control + retarget (manual detection) ------------------

@app.post("/api/sent/{sent_id}/outcome")
async def set_sent_outcome(sent_id: str, payload: dict = Body(...)):
    """Hand-mark a send's outcome. Fires the SAME effects the automated sweep fires (pause the
    follow-up on reply; suppress + stage a bounce retry on bounce; board flags for no-response /
    reopen; reset lifts a bounce-added suppression). Approve-first: a bounce stages, never sends."""
    outcome = (payload.get("outcome") or "").strip()
    if outcome not in ("replied", "bounced", "no_response", "reopen", "awaiting"):
        raise HTTPException(status_code=400, detail="outcome must be one of "
                            "replied | bounced | no_response | reopen | awaiting")
    provider = None
    if outcome == "bounced":                       # only a bounce needs a provider (to stage a retry)
        try:
            provider = _provider()
        except HTTPException:
            provider = None                        # still marks bounced + suppresses without one
    res = outcomes_mod.set_outcome(sent_id, outcome, provider=provider, source="manual")
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("error", "could not mark"))
    return res


@app.post("/api/sent/{sent_id}/retarget")
def retarget_send(sent_id: str, payload: dict = Body(default={})):
    """Stage a bounce re-draft to a DIFFERENT address/person. With {email} (+ optional name/title)
    it targets exactly that person — the backstop when the ladder has no known alternate. Without a
    body it auto-picks the next ladder rung (same as the bounce path). Never sends."""
    si = store.get_sent_item(sent_id)
    if not si:
        raise HTTPException(status_code=404, detail="unknown send")
    try:
        provider = _provider()
    except HTTPException:
        raise HTTPException(status_code=400, detail="a model provider/key is required to draft a retarget")
    email = (payload.get("email") or "").strip()
    if email:
        if "@" not in email:
            raise HTTPException(status_code=400, detail="a valid email is required")
        person = {"name": (payload.get("name") or "").strip(),
                  "title": (payload.get("title") or "").strip(), "confidence": "low"}
        n = si.bounce_retry_count + 1
        if si.sent_to:
            suppression_mod.add(si.sent_to, reason="bounced", source="manual")
        cs = pipeline.draft_retarget(provider, si, email.lower(), bounce_n=n,
                                     new_person=person if person["name"] else None)
        si.bounce_retry_count = n
        store.upsert_sent_item(si)
        return {"ok": True, "slug": cs.slug, "email": email.lower(), "person": person["name"],
                "state": cs.state.value if hasattr(cs.state, "value") else cs.state}
    retry = outcomes_mod.retarget_after_bounce(provider, si, si.sent_to)
    return {"ok": retry is not None, "retry": retry, "exhausted": retry is None}


# ---- send-window advisory (Phase 6c) ---------------------------------------

@app.get("/api/send_window")
def send_window():
    """Non-blocking advisory: is now a good time to stage? Suggests waiting for a weekday morning.
    The client shows this as a dismissible hint in the approve dialog; it never blocks."""
    st = S.load_settings()
    if not getattr(st, "send_window_advisory", True):
        return {"advise": False}
    from datetime import datetime
    now = datetime.now()
    dow = now.weekday()   # 0=Mon .. 6=Sun
    hour = now.hour
    weekend = dow >= 5
    off_hours = hour < 7 or hour >= 18
    if weekend or off_hours:
        when = "Monday morning" if weekend else "a weekday morning"
        return {"advise": True,
                "message": f"It's {now.strftime('%a %-I%p').lower()} — consider staging {when} for a better open rate."}
    return {"advise": False}


# ---- follow-ups ------------------------------------------------------------

@app.get("/api/followups")
def get_followups():
    """The Work Queue: pending/drafted follow-ups, sorted oldest original-approval first."""
    return {"followups": followups_mod.list_public()}


@app.post("/api/followups/{fid}/draft")
def draft_followup_row(fid: str, payload: dict = Body(default={})):
    """Lazily generate the follow-up email as a normal CompanyState draft, then link it to the
    FollowUp. The generated draft appears in Drafts and is approved via the existing approve path."""
    fu = store.get_followup(fid)
    if not fu:
        raise HTTPException(status_code=404, detail="unknown follow-up")
    provider = _provider()
    reuse = bool(payload.get("reuse_cache", True))
    cs = pipeline.draft_followup(provider, fu, reuse_cache=reuse,
                                 voice_override=payload.get("voice"))
    fu.draft_slug = cs.slug
    if cs.state != State.error:
        fu.status = FollowUpStatus.drafted
    store.upsert_followup(fu)
    return {"followup": followups_mod.public(fu), "company": _cs_public(cs)}


@app.post("/api/followups/{fid}/dismiss")
def dismiss_followup(fid: str):
    fu = store.get_followup(fid)
    if not fu:
        raise HTTPException(status_code=404, detail="unknown follow-up")
    fu.status = FollowUpStatus.dismissed
    store.upsert_followup(fu)
    # if a draft was generated for it but not approved, clear it out of the drafts column
    if fu.draft_slug:
        d = store.get_draft(fu.draft_slug)
        if d and d.state != State.ready:
            store.remove_draft(fu.draft_slug)
    return {"ok": True, "id": fid}


@app.post("/api/followups/clear")
def clear_followups():
    return {"ok": True, "cleared": store.clear_followups()}


# ---- drafting --------------------------------------------------------------

@app.post("/api/draft/{slug}")
def draft_one_row(slug: str, payload: dict = Body(default={})):
    batch = _batch()
    if not batch or slug not in batch.companies:
        raise HTTPException(status_code=404, detail="unknown target")
    provider = _provider()
    # A retry after a failure defaults to fresh research. Reusing the cache is right
    # for an ordinary redraft, where the research is fine and only the wording is
    # being changed, but it makes a retry identical to the attempt that just failed.
    cs = batch.companies[slug]
    was_error = getattr(cs, "state", None) == State.error
    reuse = bool(payload.get("reuse_cache", not was_error))
    cs = pipeline.draft_one(provider, cs,
                            voice_override=_STATE.get("voice"), reuse_cache=reuse)
    batch.companies[slug] = cs
    _persist()
    return _cs_public(cs)


_DRAFT_JOBS: dict[str, dict] = {}


def _run_draft_job(job_id: str, provider: Any, todo_slugs: list[str], reuse: bool, voice_override: str | None, settings_snapshot: Any):
    job = _DRAFT_JOBS.get(job_id) or store.load_draft_jobs().get(job_id)
    if not job:
        return
    workers = max(1, min(settings_snapshot.research_concurrency, len(todo_slugs) or 1))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for slug in todo_slugs:
            if job.get("cancelled"):
                job["state"] = "cancelled"
                break
            cs = store.get_draft(slug) or (_batch().companies.get(slug) if _batch() else None)
            if not cs:
                continue
            job["current_slug"] = slug
            attempts = 0
            last_err = None
            while attempts < 3:
                try:
                    pipeline.draft_one(provider, cs, voice_override=voice_override, reuse_cache=reuse)
                    _persist()
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    attempts += 1
                    if attempts < 3:
                        # Exponential backoff with jitter: 1s, 2s (+/- up to 0.5s).
                        # draft_one is idempotent -- redrafting overwrites -- so a
                        # retry is always safe.
                        time.sleep((2 ** (attempts - 1)) + random.random() * 0.5)
            if last_err is not None:
                job.setdefault("errors", []).append({"slug": slug, "error": str(last_err),
                                                     "attempts": attempts})
            job["done"] += 1
            # Checkpoint after every company. A draft costs seconds and real provider
            # tokens, so one small JSON write per company is negligible against
            # re-running even one draft after a crash.
            job.setdefault("completed", []).append(slug)
            try:
                jobs = store.load_draft_jobs()
                jobs[job_id] = job
                store.save_draft_jobs(jobs)
            except Exception:
                pass          # a checkpoint failure must never abort the run itself

    if job.get("state") != "cancelled":
        job["state"] = "done"
    try:
        jobs = store.load_draft_jobs()
        jobs[job_id] = job
        store.save_draft_jobs(jobs)
    except Exception:
        pass


@app.post("/api/draft")
def draft_all(payload: dict = Body(default={})):
    batch = _batch()
    if not batch:
        raise HTTPException(status_code=400, detail="ingest target names first")
    provider = _provider()
    st = S.load_settings()
    reuse = bool(payload.get("reuse_cache", True))
    # An explicit slugs list scopes the run to exactly those companies. Without it,
    # fall back to "every undrafted company in the batch" -- which is what Draft all
    # wants, and what Draft 5 was accidentally getting: it promoted 5 specific slugs
    # then called this endpoint with no reference to them, so a run of 5 swept in
    # every other undrafted company too (reported as 0/9 for 5 requested).
    requested = payload.get("slugs")
    skipped: list[str] = []
    if requested:
        by_slug = {cs.slug: cs for cs in batch.ordered()}
        todo = []
        for slug in requested:
            cs = by_slug.get(slug)
            if cs is None:
                skipped.append(slug)
            else:
                todo.append(cs)
    else:
        todo = [cs for cs in batch.ordered()
                if cs.state in (State.input, State.error) or not cs.machine_email]

    job_id = str(uuid.uuid4())
    job = {
        "job_id": job_id,
        "done": 0,
        "total": len(todo),
        "current_slug": "",
        "errors": [],
        "state": "running",
        "cancelled": False,
        "all_slugs": [cs.slug for cs in todo],
        "completed": [],
    }
    _DRAFT_JOBS[job_id] = job
    try:
        jobs = store.load_draft_jobs()
        jobs[job_id] = job
        store.save_draft_jobs(jobs)
    except Exception:
        pass

    if not todo:
        job["state"] = "done"
        try:
            jobs = store.load_draft_jobs()
            jobs[job_id] = job
            store.save_draft_jobs(jobs)
        except Exception:
            pass
        return {"job_id": job_id, "total": 0, "state": "done", "done": 0, "skipped": skipped}

    _ov = _STATE.get("voice")
    todo_slugs = [cs.slug for cs in todo]
    t = threading.Thread(
        target=_run_draft_job,
        args=(job_id, provider, todo_slugs, reuse, _ov, st),
        daemon=True
    )
    t.start()
    return {"job_id": job_id, "total": len(todo), "state": "running", "done": 0, "skipped": skipped}


@app.get("/api/draft/job/{job_id}")
def get_draft_job(job_id: str):
    job = _DRAFT_JOBS.get(job_id) or store.load_draft_jobs().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/api/draft/job/{job_id}/cancel")
def cancel_draft_job(job_id: str):
    job = _DRAFT_JOBS.get(job_id) or store.load_draft_jobs().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    job["cancelled"] = True
    job["state"] = "cancelled"
    _DRAFT_JOBS[job_id] = job
    try:
        jobs = store.load_draft_jobs()
        jobs[job_id] = job
        store.save_draft_jobs(jobs)
    except Exception:
        pass
    return {"ok": True, "job": job}


@app.post("/api/draft/job/{job_id}/resume")
def resume_draft_job(job_id: str):
    """Restart from the last checkpoint, skipping companies already done.

    A run that died at company 40 of 60 resumes at 41 rather than re-running --
    and re-paying for -- the first 40.
    """
    job = _DRAFT_JOBS.get(job_id) or store.load_draft_jobs().get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")
    completed = set(job.get("completed") or [])
    remaining = [s for s in (job.get("all_slugs") or []) if s not in completed]
    if not remaining:
        return {"job_id": job_id, "remaining": 0, "state": job.get("state", "done")}
    provider = _provider()
    st = S.load_settings()
    job["state"] = "running"
    job["cancelled"] = False
    _DRAFT_JOBS[job_id] = job
    try:
        jobs = store.load_draft_jobs()
        jobs[job_id] = job
        store.save_draft_jobs(jobs)
    except Exception:
        pass
    t = threading.Thread(target=_run_draft_job,
                         args=(job_id, provider, remaining, True, _STATE.get("voice"), st),
                         daemon=True)
    t.start()
    return {"job_id": job_id, "remaining": len(remaining), "state": "running"}


# ---- edit ------------------------------------------------------------------

@app.put("/api/companies/{slug}/email")
def edit_email(slug: str, payload: dict = Body(...)):
    cs = store.get_draft(slug) or (_batch().companies.get(slug) if _batch() else None)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    # Plan 26: `None` means never drafted; `""` means the blank-box authored path, where the machine
    # deliberately contributed nothing and the user is about to type the whole email. Only None is a
    # 400 -- rejecting "" would make turn 0 of a self-learning voice unwritable.
    if cs.machine_email is None:
        raise HTTPException(status_code=400, detail="draft this target first")

    # A manually-corrected recipient must persist. Previously this endpoint read only
    # subject and email (the body), so an edited address was discarded -- and because
    # approve derives send_to from cs.cache.contact.email, the .eml went to the
    # ORIGINAL address while the drawer showed the edited one.
    new_to = str(payload.get("contact_email") or "").strip()
    if new_to:
        import re as _re
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", new_to, _re.IGNORECASE):
            raise HTTPException(status_code=400, detail=f"not a valid email address: {new_to}")
        cache = cs.cache or {}
        contact = dict(cache.get("contact") or {})
        contact["email"] = new_to.lower()
        # A human typing an address outranks anything research inferred.
        contact["email_confidence"] = "high"
        contact["contact_verified"] = True
        contact["email_method"] = "manual"
        contact["email_source_url"] = ""
        cache["contact"] = contact
        cs.cache = cache
        if cs.spec is not None and isinstance(cs.spec, dict):
            cs.spec["send_to"] = new_to.lower()
        # Plan 31 (A5): this sets the MAILBOX domain only. It must never touch the company's identity
        # anchor -- `cs.website` -- or typing an address silently redefines which company this is.
        cs.recipient_domain = new_to.rsplit("@", 1)[1].lower()

    pipeline.apply_edit(cs, subject=payload.get("subject"), email=payload.get("email", ""))
    _persist()
    return _cs_public(cs)


@app.post("/api/companies/{slug}/blank")
def blank_one_row(slug: str, payload: dict = Body(default={})):
    """Open an empty draft for the user to author (Plan 26, turn 0). Makes no model call."""
    batch = _batch()
    cs = store.get_draft(slug) or (batch.companies.get(slug) if batch else None)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    vid = str(payload.get("voice") or _STATE.get("voice") or "")
    cs = pipeline.author_blank(cs, voice_id=vid)
    if batch and slug in batch.companies:
        batch.companies[slug] = cs
    _persist()
    return _cs_public(cs)


@app.post("/api/companies/{slug}/reset")
def reset_email(slug: str):
    cs = store.get_draft(slug) or (_batch().companies.get(slug) if _batch() else None)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    pipeline.reset_edit(cs)
    _persist()
    return _cs_public(cs)


@app.put("/api/companies/{slug}/attachments")
def set_company_attachments(slug: str, payload: dict = Body(...)):
    cs = store.get_draft(slug) or (_batch().companies.get(slug) if _batch() else None)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    names = payload.get("names") or []
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="names must be a list")
    cs.attachments = [n for n in names if attach_mod.resolve_paths([n])]  # keep only resolvable
    store.upsert_draft(cs)
    if _batch() and slug in _batch().companies:
        _batch().companies[slug] = cs
    _persist()
    return _cs_public(cs)


@app.delete("/api/companies/{slug}")
def delete_draft(slug: str):
    cs = store.get_draft(slug) or (_batch().companies.get(slug) if _batch() else None)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    store.remove_draft(slug)
    b = _batch()
    if b and slug in b.companies:
        del b.companies[slug]
    _persist()
    return {"ok": True, "slug": slug}


@app.post("/api/companies/{slug}/redraft")
async def redraft_company(slug: str, payload: dict = Body(...)):
    cs = store.get_draft(slug) or (_batch().companies.get(slug) if _batch() else None)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    if cs.state not in (State.drafted, State.edited, State.input, State.error, State.researched):
        raise HTTPException(status_code=400, detail="Target cannot be redrafted in its current state.")
    new_voice = payload.get("voice")
    reuse_cache = bool(payload.get("reuse_cache", True))
    provider = _provider()
    pipeline.draft_one(provider, cs, voice_override=new_voice, reuse_cache=reuse_cache)
    pipeline.reset_edit(cs)
    _persist()
    return {"company": _cs_public(cs), "warning": None}


# ---- approval --------------------------------------------------------------

def _approve_rows(rows: list[CompanyState], batch: BatchState | None) -> dict:
    apollo_voice = (rows[0].voice if rows else None) or (batch.voice if batch else "")
    receipt = apollo_mod.apollo_verify(rows, apollo_voice, keys.get_key("apollo"))

    # Map each approved row -> the Message-ID the .eml carried (the reply/bounce match key).
    mid_by_name: dict[str, str] = {}
    for r in (receipt.get("results") or []):
        if r.get("message_id"):
            mid_by_name.setdefault(r.get("name", ""), r["message_id"])

    tracker_path = _STATE.get("tracker_path")

    # 2) audit + archive + remove from drafts
    suppressed_skips = []
    edited_voices: set[str] = set()   # voices whose draft was meaningfully edited (Layer 4 trigger)
    for cs in rows:
        if not cs.final_email:
            continue
        # pre-approve suppression guard (Phase 4a): never stage a do-not-contact address
        _send_to = (cs.spec or {}).get("send_to") or ((cs.cache or {}).get("contact") or {}).get("email") or ""
        _hit, _reason = suppression_mod.is_suppressed(_send_to) if _send_to else (False, "")
        if _hit:
            suppressed_skips.append({"name": cs.name, "reason": _reason})
            continue
        v = cs.voice or (batch.voice if batch else "") or ""
        b_id = batch.batch_id if batch else ""

        if tracker_path:
            contact_name = ((cs.cache or {}).get("contact") or {}).get("name", "")
            send_to = (cs.spec or {}).get("send_to") or ((cs.cache or {}).get("contact") or {}).get("email") or ""
            tracker_mod.write_reach_row(
                Path(tracker_path), company=cs.name, contact_name=contact_name,
                email=send_to, subject=cs.subject or "")

        record = audit_mod.build_record(cs, v, b_id)
        audit_mod.write_record(record)
        cs.approved_at = record["approved_at"]
        cs.approver_voice = v
        cs.approver_os_user = record["approver_os_user"]
        cs.state = State.ready

        # Precompute the SentItem id so the edit-ledger pair can link to its outcome (Layer 4).
        _sent_id = store.next_sent_item_id(cs.slug)
        if edit_ledger.record_edit(v, cs.machine_email or "", cs.machine_body or "",
                                   cs.final_email or "", sent_id=_sent_id):
            edited_voices.add(v)
            voice_learning.note_edit(v)

        # Plan 26: the exemplar corpus records EVERY approval under a self-learning voice, including
        # the ones edit_ledger.record_edit refuses (unedited approvals, frame edits, blank-box
        # authored emails). Unconditional and error-swallowing by design; never gates an approve.
        try:
            _vd = store.get_custom_voice(v)
            if _vd is not None and getattr(_vd, "learning", "patch") == "exemplar":
                exemplars.record(
                    voice=v, slug=cs.slug,
                    provenance=("authored" if not (cs.machine_email or "").strip() else "tolerated"),
                    machine_email=cs.machine_email or "",
                    machine_blocks=dict(getattr(cs, "machine_blocks", {}) or {}),
                    final_email=cs.final_email or "",
                    features=exemplars.features_from_spec(cs.spec, cs.cache))
                exemplar_voice.maybe_freeze(v)
        except Exception:
            pass

        # --- Phase 0: record a SentItem (the join point for reply/bounce/pipeline/stats) ---
        mid = mid_by_name.get(cs.name, "")
        sent_id_for_archive = ""
        try:
            send_to = (cs.spec or {}).get("send_to") or ((cs.cache or {}).get("contact") or {}).get("email") or ""
            ladder = apollo_mod.rank_address_candidates(cs.cache or {})
            step = 0
            kind = "outreach"
            if "__f" in cs.slug:
                kind = "followup"
                try:
                    step = int(cs.slug.rpartition("__f")[2])
                except ValueError:
                    step = 1
            elif "__b" in cs.slug:
                kind = "bounce_retry"
            si = SentItem(
                id=_sent_id,
                slug=cs.slug, name=cs.name, voice=v, kind=kind, step=step,
                message_id=mid, sent_to=send_to,
                to_name=((cs.cache or {}).get("contact") or {}).get("name", ""),
                address_candidates=[AddressCandidate(**c) for c in ladder],
                recipient_domain=(send_to.split("@", 1)[1] if "@" in send_to else ""),
                subject=cs.subject or "", approved_at=cs.approved_at,
                approved_subject=cs.subject or "",
                approved_body=cs.final_email or "",   # the exact edited/approved text — reused on a retry
                source_list_id=getattr(cs, "source_list_id", ""),
                reply_state=ReplyState.awaiting,
                cost_estimate=float(getattr(cs, "cost_estimate", 0.0) or 0.0),
            )
            store.upsert_sent_item(si)
            sent_id_for_archive = si.id
        except Exception:
            pass

        store.append_archive({
            "slug": cs.slug, "name": cs.name, "ref": cs.ref or "", "voice": v,
            "sent_id": sent_id_for_archive,
            "subject": cs.subject or "",
            "contact": {
                "name": (cs.cache or {}).get("contact", {}).get("name", ""),
                "title": (cs.cache or {}).get("contact", {}).get("title", ""),
                "email": (cs.spec or {}).get("send_to") or (cs.cache or {}).get("contact", {}).get("email", ""),
                "email_confidence": (cs.cache or {}).get("contact", {}).get("email_confidence", ""),
            },
            "final_email": cs.final_email or "", "machine_email": cs.machine_email or "",
            "contact_unverified": cs.contact_unverified,
            "approved_at": cs.approved_at, "approver_os_user": cs.approver_os_user,
            "notes": [n.model_dump() for n in cs.notes],
        })
        try:
            outbox.save_to_outbox(cs, sent_id=_sent_id, message_id=mid)
        except Exception as e:
            print(f"[outbox] Warning: failed to save to outbox for {cs.name}: {e}", file=sys.stderr)
        store.remove_draft(cs.slug)
        # Stage E: unconditionally add to exclusion set (toggle does not gate writes)
        try:
            from app.sourcing.normalize import canonicalize_name
            base_slug = cs.slug.split("__")[0]   # strip __f1 / __b1 suffixes
            store.add_to_exclusion_set(base_slug)
        except Exception:
            pass

        # CRM follow-up hook (never let it break an approval):
        #  - if the email just approved was itself a follow-up draft, close out its FollowUp record;
        #  - then enrol the next follow-up in the sequence (if enabled and under the cap).
        try:
            if "__f" in cs.slug:
                fu = store.get_followup(cs.slug)
                if fu is not None:
                    fu.status = FollowUpStatus.approved
                    store.upsert_followup(fu)
            followups_mod.enroll_from_approval(cs, origin_message_id=mid)
        except Exception:
            pass
    # --- Layer 4: continuous voice learning. Fires per edited voice, gated by mode + thresholds
    # inside maybe_run. Off = today; suggest stores a proposal; auto applies (versioned) or A/Bs a
    # challenger. Never raises, never blocks the approve. ---
    if edited_voices:
        _lp = _provider_optional()
        for _v in edited_voices:
            try:
                voice_learning.maybe_run(_v, _lp)
            except Exception:
                pass

    _persist()
    return {"approved": len([r for r in rows if r.approved_at]), "apollo": receipt,
            "suppressed_skips": suppressed_skips}


@app.post("/api/companies/{slug}/approve")
def approve_one(slug: str):
    cs = store.get_draft(slug) or (_batch().companies.get(slug) if _batch() else None)
    if not cs:
        raise HTTPException(status_code=404, detail="unknown target")
    res = _approve_rows([cs], _batch())
    return {"ok": True, **res, "company": _cs_public(cs)}


# ---- batch -----------------------------------------------------------------

@app.get("/api/batch")
def get_batch():
    b = _batch()
    if not b:
        return {"batch_id": None, "voice": _STATE["voice"], "companies": []}
    return _batch_public(b)
