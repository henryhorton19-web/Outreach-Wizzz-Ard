"""Continuous voice learning (Layer 4) — distil your (machine draft -> approved edit) diffs into
STRUCTURED updates to a voice, so drafts drift toward how you actually write, without your input.

This is the layer that was missing above the three you already had: the evidence layer
(`voice_stats`), the adaptive few-shot layer (`edit_ledger`, which injects raw before/after pairs
into the compose prompt), and the routing bandit (`pipeline.resolve_voice`). It closes the loop by
writing learned preferences back into the voice record itself — the sliders, `style.notes`,
`style.examples`, and per-block `guidance` that `compose.build_voice_system` already compiles into
the system prompt. Because *the voice IS the prompt* here, updating the voice updates the prompt with
no change to the compose path.

Design (grounded in PRELUDE/CIPHER — learn an interpretable, editable preference description that
minimises future editing effort — and GEPA — reflect on traces + feedback, propose a BOUNDED,
incremental mutation, never a wholesale rewrite):

  gather   -> recent (before, after, effort, outcome) triples for a voice; bounces EXCLUDED
              (a bounce is a dead address, not a comment on the writing — mirrors voice_stats).
  reflect  -> ONE small model call (or a deterministic offline heuristic under the stub) returns a
              JSON voice-patch: style-slider deltas (±1), notes add/remove, promoted examples,
              per-block guidance. Justified from >= 2 independent edits so one odd edit can't move it.
  clamp    -> enforce the bounds and run every promoted example/note through the honesty floor.
  apply    -> snapshot the voice (rollback safety) then write the patch.

Modes (setting `voice_learning_mode`): off = today; suggest = store a proposal you accept in the UI;
auto = apply automatically (versioned, gated on min-edits + cooldown). Phase C (`voice_learning_promote`)
A/Bs a learned change as a *challenger* voice arbitrated by the reply-rate bandit before it wins.

App layer only. Every public function swallows its own errors: learning must NEVER break an approve
or a draft. Stub-safe (offline demo + tests get a deterministic patch, never a real model call).
"""
from __future__ import annotations

import json
import re
import datetime
from pathlib import Path

from . import store
from . import settings as S
from . import edit_ledger
from .models import CustomVoice

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

PROPOSALS_FILE = S.DATA_DIR / "voice_proposals.json"

# Allowed categorical style values (mirror models.Style) — a patch may only nudge within these.
_CATS = {
    "sentence_length": ("short", "medium", "flowing"),
    "hedging": ("hedged", "neutral", "assertive"),
    "humor": ("none", "dry", "light"),
    "person_focus": ("recipient_first", "sender_first", "balanced"),
    "proof_density": ("single", "few", "several"),
}
_SLIDERS = ("formality", "warmth", "directness")

# Outcome weights: a reply is strong evidence the approved text worked; awaiting is a real edit but
# unproven; a bounce is excluded entirely (dead address, not a writing signal).
_OUTCOME_WEIGHT = {"replied": 2.0, "awaiting": 1.0}
_EXCLUDE_OUTCOMES = {"bounced", "bounced_exhausted"}


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _settings():
    return S.load_settings()


# ---------------------------------------------------------------------------
# gather: weighted (before, after, effort, outcome) triples for a voice
# ---------------------------------------------------------------------------

def _outcome_by_sent_id() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for si in store.load_sent_items():
            rs = si.reply_state
            out[si.id] = rs.value if hasattr(rs, "value") else str(rs)
    except Exception:
        pass
    return out


def gather(voice_id: str, k: int = 20) -> list[dict]:
    """Recent learning triples for `voice_id`, outcome-joined and weighted. Bounces dropped. Each
    item: {before, after, effort, outcome, weight}. Never raises."""
    try:
        raw = edit_ledger.triples_for_learning(voice_id, k=k)
        outcomes = _outcome_by_sent_id()
        triples: list[dict] = []
        for r in raw:
            outcome = outcomes.get(r.get("sent_id", ""), "awaiting")
            if outcome in _EXCLUDE_OUTCOMES:
                continue
            triples.append({
                "before": r["before"],
                "after": r["after"],
                "effort": float(r.get("effort", 0.0) or 0.0),
                "outcome": outcome,
                "weight": _OUTCOME_WEIGHT.get(outcome, 1.0),
            })
        return triples
    except Exception:
        return []


# ---------------------------------------------------------------------------
# reflect: triples + current voice -> JSON voice-patch
# ---------------------------------------------------------------------------

def _reflection_system() -> str:
    return (
        "You improve an email VOICE by learning from how its drafts were edited before sending. "
        "You are given the voice's current style and a set of BEFORE (machine draft) -> AFTER "
        "(human-approved) revisions, some of which received a reply (higher weight). Infer the "
        "consistent preferences the edits reveal and return a SMALL, BOUNDED patch that would make "
        "future drafts need less editing.\n"
        "Rules: change little. At most ONE step (+1 or -1) on any slider. Add at most 2 short notes "
        "and remove at most 2. Promote at most 2 approved bodies as style examples (prefer ones that "
        "got a reply and needed little editing). Only assert a change you can justify from at least "
        "TWO independent revisions; if the edits disagree, change nothing there. Never invent facts, "
        "numbers, or names. Return ONLY a JSON object with keys: style_deltas (object of slider->+/-1), "
        "categorical (object, subset of sentence_length|hedging|humor|person_focus|proof_density), "
        "notes_add (array of strings), notes_remove (array of strings), promote_examples (array of "
        "strings), block_guidance (object block_id->string), evidence (object with prefer_add, "
        "exclude_add arrays), rationale (string), n_edits (int), n_replied (int)."
    )


def _reflection_user(voice: CustomVoice, triples: list[dict]) -> str:
    cur = {
        "display_name": voice.display_name,
        "style": voice.style.model_dump(),
        "block_ids": [b.id for b in voice.blocks if b.mode == "ai"],
        "length_min": voice.length_min, "length_max": voice.length_max,
        "allow_dashes": voice.allow_dashes, "mention_sci_po": voice.mention_sci_po,
    }
    revs = [{"before": t["before"], "after": t["after"],
             "replied": t["outcome"] == "replied", "edit_effort": round(t["effort"], 2)}
            for t in triples]
    return json.dumps({"current_voice": cur, "revisions": revs}, ensure_ascii=False, indent=2)


def _empty_patch() -> dict:
    return {"style_deltas": {}, "categorical": {}, "notes_add": [], "notes_remove": [],
            "promote_examples": [], "block_guidance": {}, "evidence": {}, "rationale": "",
            "n_edits": 0, "n_replied": 0}


def _offline_patch(voice: CustomVoice, triples: list[dict]) -> dict:
    """Deterministic patch for the stub provider / offline demo / tests. A small, honest heuristic
    (a 'phrasing miner' in miniature): if edits consistently SHORTEN and TIGHTEN the draft, nudge
    toward short/direct and promote the best-performing short approved body as an example."""
    patch = _empty_patch()
    if len(triples) < 2:
        return patch
    shorter = sum(1 for t in triples if len(t["after"]) < len(t["before"]) * 0.9)
    n = len(triples)
    patch["n_edits"] = n
    patch["n_replied"] = sum(1 for t in triples if t["outcome"] == "replied")
    if shorter >= max(2, n // 2):
        if voice.style.directness < 4:
            patch["style_deltas"]["directness"] = 1
        patch["categorical"]["sentence_length"] = "short"
        patch["rationale"] = (f"{shorter}/{n} edits shortened the draft; nudged directness up and "
                              "sentence length short.")
    # promote the replied (else lowest-effort) approved body as a gold example
    ranked = sorted(triples, key=lambda t: (0 if t["outcome"] == "replied" else 1, t["effort"]))
    if ranked:
        patch["promote_examples"] = [ranked[0]["after"]]
    return patch


def reflect(provider, voice: CustomVoice, triples: list[dict]) -> dict:
    """Return a raw (unclamped) voice-patch. Stub/offline -> deterministic heuristic. Never raises."""
    if not triples:
        return _empty_patch()
    if provider is None or getattr(provider, "is_stub", False):
        return _offline_patch(voice, triples)
    try:
        from .providers.base import Provider  # noqa: F401
        st = _settings()
        model = (st.voice_learning_reflection_model or getattr(st, "helper_model", "") or None)
        res = provider.generate(
            system=_reflection_system(),
            user=_reflection_user(voice, triples),
            use_web=False, temperature=0.2, timeout_s=st.request_timeout_s, max_retries=1,
            model=model, thinking_budget=0,
            max_output_tokens=getattr(st, "helper_max_output_tokens", 256) or 512)
        try:
            from . import cost as _cost
            _cost.record(model or "", res, slug="voice_learning")
        except Exception:
            pass
        cleaned = _FENCE_RE.sub("", res.text or "").strip()
        s, e = cleaned.find("{"), cleaned.rfind("}")
        obj = json.loads(cleaned[s:e + 1]) if s != -1 and e > s else {}
        base = _empty_patch()
        if isinstance(obj, dict):
            base.update({k: obj.get(k, base[k]) for k in base})
        return base
    except Exception:
        return _empty_patch()


# ---------------------------------------------------------------------------
# clamp + honesty-floor lint
# ---------------------------------------------------------------------------

_SIGNOFF_RE = re.compile(r"\b(best|regards|sincerely|cheers|thanks|thank you|warmly|yours)\b[,\s]*$",
                         re.IGNORECASE)


def example_is_clean(text: str, voice: CustomVoice) -> bool:
    """Advisory honesty-floor lint for a promoted example, using the voice's own knobs. Rejects
    rather than store anything that would teach a floor violation. Conservative; never raises."""
    try:
        t = (text or "").strip()
        if len(t) < 20:
            return False
        low = t.lower()
        if not voice.allow_dashes and ("\u2014" in t or "\u2013" in t or " - " in t):
            return False
        sp = low.count("sciences po")
        if voice.mention_sci_po and sp > 1:
            return False
        if not voice.mention_sci_po and sp > 0:
            return False
        if _SIGNOFF_RE.search(t):
            return False
        words = len(t.split())
        # a promoted example far outside the voice's own length band is a poor exemplar
        if words > int(voice.length_max) * 2 or words < 5:
            return False
        return True
    except Exception:
        return False


def clamp_patch(patch: dict, voice: CustomVoice) -> dict:
    """Enforce every bound the reflection prompt asked for, defensively (never trust the model):
    ±1 sliders within [0,4]; categoricals from the allowed sets; <=2 notes each way; examples capped
    and floor-linted; only known AI block ids. Returns a sanitised, ready-to-apply patch."""
    st = _settings()
    out = _empty_patch()
    p = patch if isinstance(patch, dict) else {}

    sd = p.get("style_deltas") or {}
    for k in _SLIDERS:
        try:
            d = int(sd.get(k, 0))
        except (TypeError, ValueError):
            d = 0
        if d:
            d = max(-1, min(1, d))
            cur = int(getattr(voice.style, k))
            if 0 <= cur + d <= 4:
                out["style_deltas"][k] = d

    cat = p.get("categorical") or {}
    for k, allowed in _CATS.items():
        v = cat.get(k)
        if isinstance(v, str) and v in allowed and v != getattr(voice.style, k):
            out["categorical"][k] = v

    def _strs(x):
        return [s.strip() for s in x if isinstance(s, str) and s.strip()] if isinstance(x, list) else []

    out["notes_add"] = _strs(p.get("notes_add"))[:2]
    out["notes_remove"] = _strs(p.get("notes_remove"))[:2]

    max_ex = int(getattr(st, "voice_learning_max_examples", 5) or 5)
    have = {e.strip() for e in (voice.style.examples or [])}
    promoted = []
    for ex in _strs(p.get("promote_examples"))[:2]:
        if ex not in have and example_is_clean(ex, voice):
            promoted.append(ex)
    out["promote_examples"] = promoted
    out["_max_examples"] = max_ex

    bg = p.get("block_guidance") or {}
    ai_ids = {b.id for b in voice.blocks if b.mode == "ai"}
    if isinstance(bg, dict):
        out["block_guidance"] = {k: v.strip() for k, v in bg.items()
                                 if k in ai_ids and isinstance(v, str) and v.strip()}

    ev = p.get("evidence") or {}
    if isinstance(ev, dict):
        out["evidence"] = {"prefer_add": _strs(ev.get("prefer_add"))[:3],
                           "exclude_add": _strs(ev.get("exclude_add"))[:3]}

    out["rationale"] = str(p.get("rationale") or "").strip()[:500]
    try:
        out["n_edits"] = int(p.get("n_edits", 0))
        out["n_replied"] = int(p.get("n_replied", 0))
    except (TypeError, ValueError):
        out["n_edits"] = out["n_replied"] = 0
    return out


def patch_is_empty(patch: dict) -> bool:
    p = patch or {}
    return not any([p.get("style_deltas"), p.get("categorical"), p.get("notes_add"),
                    p.get("notes_remove"), p.get("promote_examples"), p.get("block_guidance"),
                    (p.get("evidence") or {}).get("prefer_add"),
                    (p.get("evidence") or {}).get("exclude_add")])


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _apply_to_voice(voice: CustomVoice, patch: dict) -> CustomVoice:
    """Mutate `voice` in place per a (clamped) patch. Pure — no I/O."""
    st = _settings()
    for k, d in (patch.get("style_deltas") or {}).items():
        cur = int(getattr(voice.style, k))
        setattr(voice.style, k, max(0, min(4, cur + int(d))))
    for k, v in (patch.get("categorical") or {}).items():
        setattr(voice.style, k, v)

    notes = (voice.style.notes or "").strip()
    for rm in patch.get("notes_remove") or []:
        notes = notes.replace(rm, "").strip()
    add = [a for a in (patch.get("notes_add") or []) if a and a not in notes]
    if add:
        joined = " ".join(a if a.endswith((".", "!", "?")) else a + "." for a in add)
        notes = (notes + " " + joined).strip() if notes else joined
    voice.style.notes = re.sub(r"\s{2,}", " ", notes).strip()

    max_ex = int(patch.get("_max_examples", getattr(st, "voice_learning_max_examples", 5)) or 5)
    ex = list(voice.style.examples or [])
    for e in patch.get("promote_examples") or []:
        if e not in ex:
            ex.append(e)
    if max_ex >= 0 and len(ex) > max_ex:
        ex = ex[-max_ex:]                       # rotate: keep the most recent, evict oldest
    voice.style.examples = ex

    bg = patch.get("block_guidance") or {}
    for b in voice.blocks:
        if b.id in bg:
            b.guidance = bg[b.id]

    ev = patch.get("evidence") or {}
    if ev.get("prefer_add"):
        voice.evidence.prefer = list(dict.fromkeys((voice.evidence.prefer or []) + ev["prefer_add"]))
    if ev.get("exclude_add"):
        voice.evidence.exclude = list(dict.fromkeys((voice.evidence.exclude or []) + ev["exclude_add"]))
    return voice


def apply_patch(voice_id: str, patch: dict, *, note: str = "learned", origin: str = "learned") -> dict:
    """Snapshot the current voice (rollback safety), then write the patched voice. Returns a summary.
    Aborts if the snapshot fails (never mutate a voice we can't roll back). Never raises."""
    try:
        voice = store.get_custom_voice(voice_id)
        if voice is None:
            return {"ok": False, "error": "unknown voice"}
        clamped = clamp_patch(patch, voice)
        if patch_is_empty(clamped):
            return {"ok": False, "error": "empty patch (nothing confidently learned)"}
        snap = store.save_voice_version(voice, note=note)
        if not snap:
            return {"ok": False, "error": "could not snapshot voice; aborting to stay reversible"}
        _apply_to_voice(voice, clamped)
        if origin and voice.origin == "user":
            pass  # keep human-authored provenance; a learned patch on a user voice stays 'user'
        meta = dict(voice.learning_meta or {})
        meta["last_cycle_at"] = _now()
        meta["applied_count"] = int(meta.get("applied_count", 0)) + 1
        meta["edits_since"] = 0
        voice.learning_meta = meta
        store.save_custom_voice(voice)
        return {"ok": True, "voice_id": voice_id, "snapshot_ts": snap, "patch": clamped}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------------------
# proposals (suggest mode)
# ---------------------------------------------------------------------------

def _load_proposals() -> list[dict]:
    try:
        if PROPOSALS_FILE.exists():
            return json.loads(PROPOSALS_FILE.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        pass
    return []


def _save_proposals(items: list[dict]) -> None:
    try:
        store.safe_write_text(PROPOSALS_FILE,
                              json.dumps({"items": items[-100:]}, indent=2, ensure_ascii=False))
    except Exception:
        pass


def proposals_for(voice_id: str) -> list[dict]:
    return [p for p in _load_proposals() if p.get("voice_id") == voice_id]


def build_proposal(provider, voice_id: str, *, store_it: bool = True) -> dict | None:
    """Gather -> reflect -> clamp; return a proposal (patch + rationale + evidence) WITHOUT applying.
    Used by suggest mode and the manual 'Learn now' button. Stores it for later accept. Never raises."""
    try:
        voice = store.get_custom_voice(voice_id)
        if voice is None:
            return None
        triples = gather(voice_id)
        if len(triples) < 2:
            return None
        clamped = clamp_patch(reflect(provider, voice, triples), voice)
        if patch_is_empty(clamped):
            return None
        prop = {
            "id": f"{voice_id}@{_now()}",
            "voice_id": voice_id,
            "created_at": _now(),
            "n_edits": len(triples),
            "n_replied": sum(1 for t in triples if t["outcome"] == "replied"),
            "patch": clamped,
            "rationale": clamped.get("rationale", ""),
        }
        if store_it:
            items = [p for p in _load_proposals() if p.get("voice_id") != voice_id]  # one live per voice
            items.append(prop)
            _save_proposals(items)
        return prop
    except Exception:
        return None


def apply_proposal(proposal_id: str) -> dict:
    """Accept a stored proposal (versioned apply), then clear it. Never raises."""
    try:
        items = _load_proposals()
        prop = next((p for p in items if p.get("id") == proposal_id), None)
        if not prop:
            return {"ok": False, "error": "unknown proposal"}
        res = apply_patch(prop["voice_id"], prop["patch"], note="accepted suggestion")
        if res.get("ok"):
            _save_proposals([p for p in items if p.get("id") != proposal_id])
        return res
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def reject_proposal(proposal_id: str) -> bool:
    try:
        items = _load_proposals()
        kept = [p for p in items if p.get("id") != proposal_id]
        _save_proposals(kept)
        return len(kept) != len(items)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# the approve-time hook
# ---------------------------------------------------------------------------

def _cooldown_ok(voice: CustomVoice) -> bool:
    st = _settings()
    hours = int(getattr(st, "voice_learning_cooldown_hours", 12) or 0)
    if hours <= 0:
        return True
    last = (voice.learning_meta or {}).get("last_cycle_at")
    if not last:
        return True
    try:
        t = datetime.datetime.fromisoformat(last)
        return (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() >= hours * 3600
    except Exception:
        return True


def note_edit(voice_id: str) -> None:
    """Bump a voice's edits-since-last-cycle counter (called at approve when a real edit landed)."""
    try:
        v = store.get_custom_voice(voice_id)
        if v is None:
            return
        meta = dict(v.learning_meta or {})
        meta["edits_since"] = int(meta.get("edits_since", 0)) + 1
        v.learning_meta = meta
        store.save_custom_voice(v)
    except Exception:
        pass


def maybe_run(voice_id: str, provider=None) -> dict | None:
    """Called after approvals. Fires a learning cycle when the mode + thresholds allow. In suggest
    mode it stores a proposal; in auto it applies (versioned) and, if promotion is on, spawns a
    challenger for A/B instead of touching the champion. Never raises; returns a summary or None."""
    try:
        st = _settings()
        mode = getattr(st, "voice_learning_mode", "off")
        if mode == "off":
            return None
        voice = store.get_custom_voice(voice_id)
        if voice is None or (getattr(voice, "challenger_of", "") or ""):
            return None                              # don't learn on a challenger clone
        edits_since = int((voice.learning_meta or {}).get("edits_since", 0))
        if edits_since < int(getattr(st, "voice_learning_min_edits", 5) or 5):
            return None
        if not _cooldown_ok(voice):
            return None

        if mode == "suggest":
            prop = build_proposal(provider, voice_id)
            # reset the counter so we don't rebuild a proposal every approve
            v = store.get_custom_voice(voice_id)
            if v is not None:
                meta = dict(v.learning_meta or {}); meta["edits_since"] = 0
                meta["last_cycle_at"] = _now(); v.learning_meta = meta
                store.save_custom_voice(v)
            return {"mode": "suggest", "proposal": prop}

        # auto
        triples = gather(voice_id)
        if len(triples) < 2:
            return None
        clamped = clamp_patch(reflect(provider, voice, triples), voice)
        if patch_is_empty(clamped):
            v = store.get_custom_voice(voice_id)
            if v is not None:
                meta = dict(v.learning_meta or {}); meta["edits_since"] = 0; v.learning_meta = meta
                store.save_custom_voice(v)
            return {"mode": "auto", "applied": False, "reason": "nothing confidently learned"}

        if getattr(st, "voice_learning_promote", False):
            ch = spawn_challenger(voice, clamped)
            v = store.get_custom_voice(voice_id)
            if v is not None:
                meta = dict(v.learning_meta or {}); meta["edits_since"] = 0
                meta["last_cycle_at"] = _now(); v.learning_meta = meta
                store.save_custom_voice(v)
            return {"mode": "auto", "applied": False, "challenger": ch}
        res = apply_patch(voice_id, clamped, note="auto-applied")
        return {"mode": "auto", "applied": bool(res.get("ok")), "result": res}
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase C: champion / challenger A/B, arbitrated by the existing reply-rate bandit
# ---------------------------------------------------------------------------

def _next_challenger_id(parent_id: str) -> str:
    n = 0
    for v in store.list_custom_voices(kind=None, include_challengers=True):
        if getattr(v, "challenger_of", "") == parent_id:
            n += 1
    return f"{parent_id}__c{n + 1}"


def spawn_challenger(champion: CustomVoice, patch: dict) -> dict | None:
    """Create a challenger = the champion with `patch` applied, carrying the SAME situations so the
    router's reply-rate bandit routes some live sends to it (exploration). The champion is left
    untouched until the challenger proves itself. Returns {champion, challenger} ids. Never raises."""
    try:
        cid = _next_challenger_id(champion.id)
        ch = champion.model_copy(deep=True)
        ch.id = cid
        ch.display_name = f"{champion.display_name} (learning A/B)"
        ch.challenger_of = champion.id
        ch.origin = "challenger"
        ch.created_at = _now()
        ch.learning_meta = {}
        clamped = clamp_patch(patch, ch)
        _apply_to_voice(ch, clamped)
        store.save_custom_voice(ch)
        return {"champion": champion.id, "challenger": cid, "patch": clamped}
    except Exception:
        return None


def _promote_challenger(champion_id: str, challenger: CustomVoice) -> bool:
    """The challenger won: copy its learned CONTENT into the champion (versioned) and delete it."""
    try:
        champ = store.get_custom_voice(champion_id)
        if champ is None:
            return False
        store.save_voice_version(champ, note=f"pre-promote (challenger {challenger.id} won)")
        champ.style = challenger.style
        for b in champ.blocks:
            cb = next((x for x in challenger.blocks if x.id == b.id), None)
            if cb is not None:
                b.guidance = cb.guidance
        champ.evidence = challenger.evidence
        meta = dict(champ.learning_meta or {})
        meta["applied_count"] = int(meta.get("applied_count", 0)) + 1
        meta["last_cycle_at"] = _now()
        champ.learning_meta = meta
        store.save_custom_voice(champ)
        store.delete_custom_voice(challenger.id)
        return True
    except Exception:
        return False


def arbitrate(min_sep: bool = True) -> list[dict]:
    """For every champion that has a live challenger, ask the reply-rate bandit who is winning and
    resolve it: promote the challenger if its Wilson interval separates ABOVE the champion's; retire
    it if the champion separates above. Otherwise keep A/B testing. Reuses voice_stats + the exact
    separation test from pipeline._learned_pick. Returns a list of decisions. Never raises."""
    decisions: list[dict] = []
    try:
        from . import voice_stats as vs
        buckets = vs.rebuild_all()
        challengers = [v for v in store.list_custom_voices(kind=None, include_challengers=True)
                       if getattr(v, "challenger_of", "")]
        for ch in challengers:
            champ_id = ch.challenger_of
            bc, bch = buckets.get(champ_id), buckets.get(ch.id)
            if not (bc and bch and bc.get("enough_data") and bch.get("enough_data")
                    and bc.get("reply_ci") and bch.get("reply_ci")):
                decisions.append({"challenger": ch.id, "champion": champ_id, "decision": "keep_testing"})
                continue
            ci_c, ci_ch = bc["reply_ci"], bch["reply_ci"]
            if ci_ch[0] > ci_c[1]:                         # challenger clearly better
                ok = _promote_challenger(champ_id, ch)
                decisions.append({"challenger": ch.id, "champion": champ_id,
                                  "decision": "promoted", "ok": ok})
            elif ci_c[0] > ci_ch[1]:                       # champion clearly better
                store.delete_custom_voice(ch.id)
                decisions.append({"challenger": ch.id, "champion": champ_id, "decision": "retired"})
            else:
                decisions.append({"challenger": ch.id, "champion": champ_id, "decision": "keep_testing"})
    except Exception:
        pass
    return decisions


# ---------------------------------------------------------------------------
# status (for the UI panel)
# ---------------------------------------------------------------------------

def learning_status(voice_id: str) -> dict:
    """Everything the Voices editor's Learning panel needs. Never raises."""
    try:
        st = _settings()
        voice = store.get_custom_voice(voice_id)
        if voice is None:
            return {"ok": False}
        triples = gather(voice_id)
        challenger = next((v for v in store.list_custom_voices(kind=None, include_challengers=True)
                           if getattr(v, "challenger_of", "") == voice_id), None)
        ab = None
        if challenger is not None:
            try:
                from . import voice_stats as vs
                b = vs.rebuild_all()
                ab = {"challenger_id": challenger.id,
                      "champion_stats": b.get(voice_id),
                      "challenger_stats": b.get(challenger.id)}
            except Exception:
                ab = {"challenger_id": challenger.id}
        return {
            "ok": True,
            "mode": getattr(st, "voice_learning_mode", "off"),
            "promote": bool(getattr(st, "voice_learning_promote", False)),
            "min_edits": int(getattr(st, "voice_learning_min_edits", 5)),
            "edits_since": int((voice.learning_meta or {}).get("edits_since", 0)),
            "applied_count": int((voice.learning_meta or {}).get("applied_count", 0)),
            "last_cycle_at": (voice.learning_meta or {}).get("last_cycle_at", ""),
            "pending_triples": len(triples),
            "proposals": proposals_for(voice_id),
            "versions": store.list_voice_versions(voice_id),
            "ab": ab,
            "origin": getattr(voice, "origin", "user"),
        }
    except Exception:
        return {"ok": False}
