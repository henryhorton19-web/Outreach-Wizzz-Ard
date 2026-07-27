"""Stage-1 research service.

For each target: one grounded model call whose job is to return a single JSON object that
validates against schema.json. The system prompt is enrichment_brief.md VERBATIM plus an output
contract and a JSON skeleton. Provenance is the product: every fact carries a source, and nothing
outside the cache (or the fixed candidate profile) can ever reach a draft.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema

from .providers.base import Provider, ProviderError
from . import settings as S

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"


def _engine_text(name: str) -> str:
    return (ENGINE_DIR / name).read_text(encoding="utf-8")


def _schema() -> dict:
    return json.loads(_engine_text("schema.json"))


def _brief_text() -> str:
    compact = ENGINE_DIR.parent / "app" / "prompts" / "enrichment_brief.compact.md"
    if compact.exists():
        return compact.read_text(encoding="utf-8")
    return _engine_text("enrichment_brief.md")


class ResearchError(RuntimeError):
    pass


_OUTPUT_CONTRACT = """
--- OUTPUT CONTRACT (appended by the app; obey exactly) ---
Return ONLY a single JSON object conforming to the schema. No prose, no markdown, no code fences
outside the JSON. Every fact you assert MUST carry a source string.

COMPANY NAME. Set company.name to the company's official name with its correct capitalisation as
shown on its OWN website (e.g. eGym, PPRO, WeTransfer, xAI) — not a reformatting of the input and
not an all-lowercase logo. If you cannot confirm official casing, use normal title case.

RIGHT COMPANY. Use the IDENTITY ANCHOR in the user message only to make sure you have the right
company of that name. Never copy it into the cache; verify every fact independently.

FILL, and do not leave any of these empty:
- company.what_they_do: one neutral routing sentence.
- company.role_exists: true or false (you MUST decide). If true, set company.role_title AND
  company.role_source. If false, invest in situation_read instead.
- company.company_size: exactly "small" (pre-Series B / <~80 headcount / early or boutique fund)
  or "large". You MUST also set company.company_size_evidence — a short, sourced justification
  (e.g. "Series A, ~45 staff on LinkedIn"). A size verdict with no evidence is not acceptable.
- company.work_mode: exactly one of paris_office | remote_english | disqualify.
- company.working_language: English, English-dominant, or the dominant language if it disqualifies.
  If presence is required outside Paris, or the role is French-dominant, set work_mode/language
  accordingly, disqualified=true, and a short disqualify_reason. Do not "rescue" a disqualified
  target by softening these.
- proof_points: TWO sourced facts about the target (one acceptable, never more than three). Each
  MUST carry proof_points[].staleness = exactly one of: fresh | aging | stale. Choose by the
  fact's date relative to {recency_floor}: "fresh" if on/after it (within ~12 months), "aging" if
  roughly 6-18 months before it, "stale" if older. Prefer and lead with fresh facts.
- recent_point: the single most recent sourced trigger within ~6 months of {today}
  (kind: raise | funding | launch | hire | expansion | other). Set present=false if none.
- contact: the right person (founder for a small company, partner for a fund, hiring manager for
  a large one). Set contact.status = "found" once you have a name; otherwise "to_research".
  role_basis: founder | partner | hiring_manager. Provide a best-guess contact.email (NEVER blank),
  with contact.email_confidence = high | medium | low. Set contact.contact_verified = true ONLY if
  the person is confirmed from a primary/recent source; otherwise false (still give a best guess).
- situation_read: one sentence naming the target's specific moment (most valuable when
  role_exists=false).
- overall_confidence: high | medium | low. Use "medium" once you have a contact and two proof
  points; "low" ONLY if you cannot establish the situation or identify a person at all.

For any OPTIONAL field you cannot fill, OMIT the key or use "" — NEVER null.

ECONOMY. You have at most {max_web} web searches; fewer is better. STOP as soon as you have: the
size verdict with evidence, the role decision, two proof points, a contact, and the work-mode /
language read. Do NOT spend searches confirming things you already have or chasing marginal proof
points. If you approach the limit before finishing, STOP and return the best schema-valid JSON you
have with a short research_failures note. NEVER return prose, an apology, or an empty response
because searches ran out.
"""

_SKELETON = {
    "company": {"name": "", "website": "", "what_they_do": "", "role_exists": False,
                "role_title": "", "role_source": "", "company_size": "small",
                "company_size_evidence": "", "work_mode": "remote_english",
                "working_language": "English", "disqualified": False},
    "thesis": {"market_shift": "", "market_shift_source": "", "company_positioning": "", "positioning_source": ""},
    "proof_points": [{"fact": "", "source": "", "kind": "product", "staleness": "fresh"}],
    "traction_signals": [],
    "stated_plan": {"detail": "", "short": "", "label": "", "source": ""},
    "recent_point": {"present": False, "kind": "other", "detail": "", "source": ""},
    "earned_observation": {"present": False},
    "contact": {"status": "found", "name": "", "title": "", "role_basis": "founder",
                "email": "", "email_confidence": "low", "contact_verified": False},
    "contacts_alt": [],
    "situation_read": "",
    "evidence_sources": [],
    "research_failures": [],
    "overall_confidence": "medium",
}


def _identity_anchor(name: str, website: str | None, contact_hint: str | None) -> str:
    """SOFT disambiguation block appended to the USER message only. Its sole job is to make sure
    the model researches the RIGHT company of a given name — NOT to supply facts. Everything must
    still be verified independently from primary/recent web sources; nothing here enters the cache.
    Paris carries no Dealroom identity metadata, so the anchor is whatever the operator gave us:
    the website domain and/or a contact hint."""
    if not (website or contact_hint):
        return ""
    lines = []
    if website:
        lines.append(f"Official site given by the operator: {website}")
    if contact_hint:
        lines.append(f"Contact hint given by the operator: {contact_hint}")
    return (
        "\n\n--- IDENTITY ANCHOR (to confirm you are researching the RIGHT company; do NOT treat "
        "as verified facts, do NOT cite, do NOT copy into the cache — verify everything "
        "independently) ---\n" + "\n".join(lines) +
        f"\nThe target is the company named \"{name}\" that this site/brand belongs to. If the "
        "company you find on the web does not match this site's domain or brand, you have likely "
        "found a DIFFERENT company of the same name — search again for the right one. If it "
        "matches but has clearly moved on since (e.g. a newer round), trust the fresher sources."
    )

def build_research_prompt(name: str, website: str | None, contact_hint: str | None,
                          max_web: int, recency_floor: str, today: str) -> tuple[str, str]:
    contract = (_OUTPUT_CONTRACT
                .replace("{max_web}", str(max_web))
                .replace("{recency_floor}", recency_floor)
                .replace("{today}", today))
    system = "\n".join([
        _brief_text(), contract,
        "Return ONE JSON object with this shape (fill and extend it; omit optional fields you cannot fill):",
        json.dumps(_SKELETON, indent=2),
    ])
    user = f"Target: {name}"
    if website:
        user += f"\nWebsite: {website}"
    if contact_hint:
        user += f"\nKnown contact hint (verify, do not trust blindly): {contact_hint}"
    user += _identity_anchor(name, website, contact_hint)
    return system, user


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> dict:
    if not text:
        raise ResearchError("empty research response")
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ResearchError("no JSON object found in research response")
    return json.loads(cleaned[start:end + 1])


def _validate(cache: dict) -> None:
    jsonschema.validate(cache, _schema())


def _prune_nulls(obj):
    if isinstance(obj, dict):
        return {k: _prune_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_prune_nulls(v) for v in obj if v is not None]
    return obj


def _coerce_fact_list(items, key: str) -> list[dict]:
    out: list[dict] = []
    for it in (items or []):
        if isinstance(it, dict):
            out.append(it)
        elif isinstance(it, str) and it.strip():
            out.append({key: it.strip()})
    return out


def _sanitize_cache(cache: dict) -> dict:
    """Make a cache engine-safe. Idempotent. Only reshapes malformed containers; never invents."""
    if not isinstance(cache, dict):
        return cache
    if "proof_points" in cache:
        cache["proof_points"] = _coerce_fact_list(cache.get("proof_points"), "fact")[:3]
    if "contacts_alt" in cache:
        primary = ((cache.get("contact") or {}).get("name") or "").strip().lower()
        alts = []
        for a in (cache.get("contacts_alt") or []):
            if not isinstance(a, dict):
                continue
            nm = (a.get("name") or "").strip()
            if not nm or nm.lower() == primary:
                continue  # blank or the same person as the primary contact is not a backup
            alts.append(a)
        cache["contacts_alt"] = alts[:2]
    return cache


_CONFIG_ERR = ("api key", "api_key", "x-api-key", "authentication", "unauthorized", "permission",
               "invalid key", "no gemini api key", "no anthropic api key", "401", "403",
               "is not installed")
_SEARCH_LIMIT = ("max_uses", "max uses", "web search", "web_search", "search limit", "grounding",
                 "quota", "resource_exhausted", "429", "rate")


def _is_config_error(msg):
    m = (msg or "").lower()
    return any(k in m for k in _CONFIG_ERR)


def _looks_like_search_limit(msg):
    m = (msg or "").lower()
    return any(k in m for k in _SEARCH_LIMIT)


def _completeness_gaps(cache: dict) -> list[str]:
    """Names of routing-critical fields the model left empty on an otherwise schema-valid cache.
    Empty list == complete enough to draft AND to show. This is the check the schema cannot make:
    the schema accepts an empty company_size_evidence or a null role decision, but such a cache
    renders as a blank card and gives the reviewer nothing to verify."""
    c = cache or {}
    company = c.get("company") or {}
    contact = c.get("contact") or {}
    gaps: list[str] = []

    if (company.get("company_size") or "").lower() not in ("small", "large"):
        gaps.append("company_size (small|large)")
    if not str(company.get("company_size_evidence") or "").strip():
        gaps.append("company_size_evidence")

    if company.get("role_exists") is None:
        gaps.append("role_exists (true|false)")
    elif company.get("role_exists") is True and not str(company.get("role_source") or "").strip():
        gaps.append("role_source (a role was claimed but not sourced)")

    has_contact = bool(str(contact.get("name") or "").strip())
    has_read = bool(str(c.get("situation_read") or "").strip())
    if not has_contact and not has_read:
        gaps.append("contact.name or situation_read")

    pts = [p for p in (c.get("proof_points") or [])
           if isinstance(p, dict) and str(p.get("fact") or "").strip()]
    if not pts:
        gaps.append("proof_points (>=1 sourced fact)")

    return gaps


def salvage_partial_cache(name, website, source_urls, raw_text, reason) -> dict:
    """Build a schema-valid cache from an INCOMPLETE run so the target still drafts. Keep what the
    model DID find; default only required fields; floor confidence to medium; record a visible
    failure note. Never itself hard-errors a row."""
    partial = {}
    if raw_text:
        try:
            partial = extract_json(raw_text)
        except Exception:
            partial = {}
    if not isinstance(partial, dict):
        partial = {}

    company = dict(partial.get("company") or {})
    llm_name = company.get("name") or ""
    if not llm_name or name.lower() not in llm_name.lower():
        company["name"] = name
    if website and not company.get("website"):
        company["website"] = website
    company.setdefault("role_exists", False)
    company.setdefault("company_size", "small")

    ev = []
    for item in list(partial.get("evidence_sources") or []):
        if isinstance(item, str) and item:
            ev.append(item)
        elif isinstance(item, dict):
            u = item.get("url") or item.get("href") or item.get("link") or ""
            if u:
                ev.append(u)
    for u in (source_urls or []):
        if u and u not in ev:
            ev.append(u)

    failures = list(partial.get("research_failures") or [])
    note = (f"Research stopped early ({reason}); this cache is partial. "
            "Verify the target facts and the contact before sending.")
    if note not in failures:
        failures.append(note)

    contact = dict(partial.get("contact") or {})
    if contact.get("status") not in ("found", "to_research"):
        contact["status"] = "found" if contact.get("email") else "to_research"
    contact.setdefault("contact_verified", False)

    thesis = dict(partial.get("thesis") or {})
    if not str(thesis.get("market_shift") or "").strip():
        thesis["market_shift"] = "Market context was not fully established before research stopped."
    if not str(thesis.get("company_positioning") or "").strip():
        thesis["company_positioning"] = f"{name} (company details were not fully verified before research stopped)."

    cache = {
        "company": company,
        "thesis": thesis,
        "proof_points": _coerce_fact_list(partial.get("proof_points"), "fact")[:3],
        "traction_signals": _coerce_fact_list(partial.get("traction_signals"), "signal"),
        "recent_point": partial.get("recent_point") or {"present": False},
        "stated_plan": partial.get("stated_plan") or {},
        "contact": contact,
        "situation_read": partial.get("situation_read", ""),
        "evidence_sources": ev,
        "research_failures": failures,
        "overall_confidence": "medium",
    }
    for k in ("earned_observation",):
        v = partial.get(k)
        if isinstance(v, dict) and v.get("present") and v.get("read") and v.get("basis"):
            cache[k] = v
            
    cache = _prune_nulls(cache)
    try:
        _validate(cache)
        return cache
    except Exception:
        minimal = {
            "company": {"name": company.get("name") or name,
                        "role_exists": company.get("role_exists", False),
                        "role_title": company.get("role_title", ""),
                        "role_source": company.get("role_source", ""),
                        "company_size": company.get("company_size", "small"),
                        "company_size_evidence": company.get("company_size_evidence", ""),
                        "what_they_do": company.get("what_they_do", ""),
                        "website": company.get("website", "")},
            "thesis": {
                "market_shift": thesis.get("market_shift") or "Market context was not established before research stopped.",
                "company_positioning": thesis.get("company_positioning") or f"{name} (company details were not verified before research stopped).",
            },
            "proof_points": _coerce_fact_list(partial.get("proof_points"), "fact") or [{"fact": f"{name}: research incomplete before facts were confirmed."}],
            "traction_signals": _coerce_fact_list(partial.get("traction_signals"), "signal"),
            "stated_plan": partial.get("stated_plan") or {},
            "recent_point": {"present": False},
            "contact": {
                "status": contact.get("status") if contact.get("status") in ("to_research", "found") else "to_research",
                "name": contact.get("name", ""),
                "title": contact.get("title", ""),
                "email": contact.get("email", ""),
                "role_basis": contact.get("role_basis", "founder"),
                "email_confidence": contact.get("email_confidence", "low"),
                "contact_verified": contact.get("contact_verified", False)
            },
            "situation_read": partial.get("situation_read", ""),
            "evidence_sources": ev,
            "research_failures": failures + ["Salvage fell back to a minimal cache; verify everything."],
            "overall_confidence": "medium",
        }
        for k in ("earned_observation",):
            v = partial.get(k)
            if isinstance(v, dict) and v.get("present") and v.get("read"):
                minimal[k] = v
        return _prune_nulls(minimal)


def _post_process(cache: dict, name: str, website: str | None, source_urls: list[str]) -> dict:
    cache = _sanitize_cache(cache)
    from .ingest import _display_name          # local import: avoids a top-level ingest<->research cycle
    cache.setdefault("company", {})
    llm_name = str(cache["company"].get("name") or "").strip()
    if not llm_name or name.lower() not in llm_name.lower():
        cache["company"]["name"] = _display_name(name)      # guard: model drifted to a different name
    else:
        cache["company"]["name"] = _display_name(llm_name)  # keep model's name, normalise casing
    if website and not cache["company"].get("website"):
        cache["company"]["website"] = website

    ev = []
    for item in list(cache.get("evidence_sources") or []):
        if isinstance(item, str) and item:
            ev.append(item)
        elif isinstance(item, dict):
            u = item.get("url") or item.get("href") or item.get("link") or ""
            if u:
                ev.append(u)
    for u in source_urls:
        if u and u not in ev:
            ev.append(u)
    cache["evidence_sources"] = ev

    # engine-compat: prepare drafts regardless of confidence; the app surfaces quality separately.
    if cache.get("overall_confidence") == "low":
        cache["overall_confidence"] = "medium"

    _STALE_RANK = {"fresh": 0, "aging": 1, "stale": 2}
    def _rank(p):
        return _STALE_RANK.get(p.get("staleness"), 3) if isinstance(p, dict) else 3
    pts = cache.get("proof_points")
    if isinstance(pts, list) and pts:
        cache["proof_points"] = sorted(pts, key=_rank)

    return _prune_nulls(cache)


def _stub_cache(name: str, website: str | None) -> dict:
    """Deterministic offline cache for the stub provider and tests."""
    return {
        "company": {"name": name, "website": website or "", "what_they_do": "B2B software.",
                    "role_exists": True, "role_title": "Operations Analyst",
                    "company_size": "small", "work_mode": "remote_english",
                    "working_language": "English", "disqualified": False},
        "proof_points": [
            {"fact": f"{name} recently expanded its product into a second market.",
             "source": "https://example.com/a", "kind": "market", "staleness": "fresh"},
            {"fact": f"{name} serves mid-market SaaS teams with a data-heavy platform.",
             "source": "https://example.com/b", "kind": "product", "staleness": "aging"},
        ],
        "recent_point": {"present": True, "kind": "raise",
                         "detail": f"{name}'s recent seed round", "source": "https://example.com/c",
                         "staleness": "fresh"},
        "contact": {"status": "found", "name": "Alex Founder", "title": "CEO",
                    "role_basis": "founder", "email": "alex@example.com",
                    "email_confidence": "medium", "contact_verified": True},
        "contacts_alt": [{"name": "Robin Second", "title": "COO", "role_basis": "founder",
                          "email": "robin@example.com", "email_confidence": "low"}],
        "situation_read": f"{name} looks like it is about to need more hands on the commercial side",
        "evidence_sources": ["https://example.com/a", "https://example.com/b", "https://example.com/c"],
        "research_failures": [],
        "overall_confidence": "medium",
    }


def research_company(provider: Provider, name: str, website: str | None,
                     contact_hint: str | None = None) -> dict:
    if getattr(provider, "is_stub", False):
        cache = _stub_cache(name, website)
        _validate(cache)
        return cache

    st = S.load_settings()
    max_web = st.max_web_searches
    import datetime
    _today = datetime.date.today()
    today = _today.isoformat()
    recency_floor = (_today - datetime.timedelta(days=365)).isoformat()  # ~12-month proof-staleness floor
    system, user = build_research_prompt(name, website, contact_hint, max_web, recency_floor, today)

    last_text, last_urls, last_err, feedback = "", [], None, ""
    for attempt in range(2):            # attempt 0 + one retry; the retry carries targeted feedback
        try:
            res = provider.generate(
                system=system, user=user + feedback, use_web=True, max_web=max_web,
                temperature=st.research_temperature, timeout_s=st.request_timeout_s,
                max_retries=st.max_retries,
                model=(st.gemini_model if provider.name == "gemini" else st.anthropic_model),
                thinking_budget=st.research_thinking_budget,
                thinking_level=st.research_thinking_level,
                max_output_tokens=st.research_max_output_tokens,
            )
            last_text, last_urls = res.text or "", res.source_urls or []
            try:
                from . import cost as _cost
                _cost.record(st.gemini_model if provider.name == "gemini" else st.anthropic_model,
                             res, slug=_cost.current_slug())
            except Exception:
                pass
        except ProviderError as e:
            if _is_config_error(str(e)):                       # bad key / auth -> surface, do not mask
                raise ResearchError(f"provider/config error: {e}") from e
            # capacity / search-limit: salvage whatever partial JSON + URLs we already have
            return salvage_partial_cache(name, website, last_urls, last_text, reason=str(e)[:120])

        try:
            cache = extract_json(last_text)
            cache = _post_process(cache, name, website, last_urls)
            _validate(cache)
        except Exception as e:                                 # parse/schema failure -> retry with the error
            last_err = e
            feedback = ("\n\n--- YOUR PREVIOUS OUTPUT FAILED VALIDATION ---\n"
                        f"{type(e).__name__}: {e}\nReturn corrected JSON only, same schema.")
            continue

        # Valid JSON. Completeness gate: is it actually draftable AND showable?
        gaps = _completeness_gaps(cache)
        if gaps and attempt == 0:                              # one targeted retry naming the gaps
            feedback = ("\n\n--- YOUR PREVIOUS OUTPUT WAS INCOMPLETE ---\n"
                        "It parsed but left these routing-critical fields empty: "
                        + "; ".join(gaps) + ".\n"
                        "Do targeted searches to fill them. If a field is genuinely not public "
                        "after a real attempt, leave it empty and add ONE research_failures line "
                        "explaining why. Return the full JSON again.")
            continue
        if gaps:                                               # still thin after the retry: keep, but FLAG
            fails = list(cache.get("research_failures") or [])
            note = ("Research incomplete: could not confirm " + ", ".join(gaps)
                    + ". Cache is thin — verify before sending.")
            if note not in fails:
                fails.append(note)
            cache["research_failures"] = fails                 # word "incomplete" trips research_capped()
        return cache

    # both attempts hard-failed to parse/validate
    return salvage_partial_cache(name, website, last_urls, last_text,
                                 reason=f"response could not be parsed ({type(last_err).__name__})")
