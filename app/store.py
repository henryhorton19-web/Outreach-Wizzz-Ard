"""Persistence. JSON is canonical: the whole batch (research cache -> spec -> machine draft +
report -> edited final -> state) is stored as one JSON file, plus a per-company cache file so
re-runs/edits never re-search. CSV/XLSX are EXPORT-ONLY (flattened, read-only): email bodies
contain newlines and commas that would corrupt a CSV store.

Queue vs Drafts — completely separate stores:
  queue.json   : lightweight {slug, name, crm_id, queued_at} records, cap 100, engine never touches.
  drafts.json  : full CompanyState pipeline artefacts, cap 15, engine owns these.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from . import settings as S
from .models import BatchState, CompanyState, CustomVoice, CustomSourcingPrompt, FollowUp, SentItem


def batch_path(batch_id: str) -> Path:
    return S.BATCH_DIR / f"{batch_id}.json"


def cache_path(slug: str) -> Path:
    return S.CACHE_DIR / f"cache_{slug}.json"


DRAFTS_FILE = S.DATA_DIR / "drafts.json"
ARCHIVE_FILE = S.DATA_DIR / "archive.json"
QUEUE_FILE  = S.DATA_DIR / "queue.json"
FOLLOWUPS_FILE = S.DATA_DIR / "follow_ups.json"
SENT_ITEMS_FILE = S.DATA_DIR / "sent_items.json"
SUPPRESSIONS_FILE = S.DATA_DIR / "suppressions.json"
SNIPPETS_FILE = S.DATA_DIR / "snippets.json"
SESSION_STATS_FILE = S.DATA_DIR / "session_stats.json"

QUEUE_CAP  = 500
DRAFTS_CAP = 15


class StorageError(RuntimeError):
    pass


def safe_write_text(path: Path, text: str) -> None:
    # Atomic (temp + fsync + os.replace) so a concurrent sync/commit or a crash mid-write
    # never leaves a torn JSON file. Implementation lives in settings to avoid a cycle.
    try:
        S.atomic_write_text(path, text)
    except OSError as e:
        raise StorageError(f"Permission or disk error writing to {path}: {e}. "
                           "If on macOS, check ownership via 'sudo chown -R $(whoami) ~/.paris_outreach'") from e


def _trim_keep_recent(items: list[dict], key: str, n: int = 15) -> list[dict]:
    return sorted(items, key=lambda x: x.get(key) or "", reverse=True)[:n]


# ---- queue -----------------------------------------------------------------

def load_queue() -> list[dict]:
    """Return list of lightweight queue records: {slug, name, crm_id, queued_at, [meta]}."""
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def save_queue(items: list[dict]) -> None:
    safe_write_text(QUEUE_FILE, json.dumps({"items": items}, indent=2, ensure_ascii=False))


def queue_slugs() -> set[str]:
    return {r["slug"] for r in load_queue()}


def upsert_queue(slug: str, name: str, crm_id: str | None, meta: dict | None = None) -> None:
    import datetime
    items = load_queue()
    items = [r for r in items if r["slug"] != slug]   # replace if already present
    rec = {"slug": slug, "name": name, "crm_id": crm_id or "",
           "queued_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    if meta:
        rec["meta"] = meta
    items.append(rec)
    items = sorted(items, key=lambda x: x.get("queued_at") or "", reverse=True)[:QUEUE_CAP]
    save_queue(items)


def remove_from_queue(slug: str) -> bool:
    items = load_queue()
    filtered = [r for r in items if r["slug"] != slug]
    if len(filtered) == len(items):
        return False
    save_queue(filtered)
    return True


def clear_queue() -> int:
    items = load_queue()
    save_queue([])
    return len(items)


def queue_count() -> int:
    return len(load_queue())


def load_drafts() -> list[CompanyState]:
    if not DRAFTS_FILE.exists():
        return []
    try:
        data = json.loads(DRAFTS_FILE.read_text(encoding="utf-8"))
        return [CompanyState.model_validate(item) for item in data.get("items", [])]
    except Exception:
        return []


def save_drafts(drafts: list[CompanyState]) -> None:
    data = {"items": [cs.model_dump() for cs in drafts]}
    safe_write_text(DRAFTS_FILE, json.dumps(data, indent=2, ensure_ascii=False))


def get_draft(slug: str) -> CompanyState | None:
    for cs in load_drafts():
        if cs.slug == slug:
            return cs
    return None


def upsert_draft(cs: CompanyState) -> None:
    import datetime
    cs.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    drafts = load_drafts()
    out = []
    for d in drafts:
        if d.slug != cs.slug:
            out.append(d)
    out.append(cs)
    trimmed_dicts = _trim_keep_recent([c.model_dump() for c in out], "updated_at", 15)
    save_drafts([CompanyState.model_validate(d) for d in trimmed_dicts])


def remove_draft(slug: str) -> None:
    drafts = load_drafts()
    drafts = [d for d in drafts if d.slug != slug]
    save_drafts(drafts)


def clear_drafts() -> int:
    drafts = load_drafts()
    count = len(drafts)
    save_drafts([])
    return count


def load_archive() -> list[dict]:
    if not ARCHIVE_FILE.exists():
        return []
    try:
        data = json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def save_archive(records: list[dict]) -> None:
    data = {"items": records}
    safe_write_text(ARCHIVE_FILE, json.dumps(data, indent=2, ensure_ascii=False))


def append_archive(record: dict) -> None:
    records = load_archive()
    records.append(record)
    trimmed = _trim_keep_recent(records, "approved_at", 15)
    save_archive(trimmed)


def clear_archive() -> int:
    records = load_archive()
    count = len(records)
    save_archive([])
    return count


# ---- follow-ups ------------------------------------------------------------
# A separate store, mirroring drafts/archive. Each record is a FollowUp (the CRM 'tracker' for one
# pending touch). The actual follow-up email is a normal CompanyState in drafts.json keyed by the
# FollowUp.draft_slug; this store only tracks the pending/queued follow-up itself.

def load_followups() -> list[FollowUp]:
    if not FOLLOWUPS_FILE.exists():
        return []
    try:
        data = json.loads(FOLLOWUPS_FILE.read_text(encoding="utf-8"))
        return [FollowUp.model_validate(x) for x in data.get("items", [])]
    except Exception:
        return []


def save_followups(items: list[FollowUp]) -> None:
    data = {"items": [f.model_dump(mode="json") for f in items]}
    safe_write_text(FOLLOWUPS_FILE, json.dumps(data, indent=2, ensure_ascii=False))


def get_followup(fid: str) -> FollowUp | None:
    return next((f for f in load_followups() if f.id == fid), None)


def upsert_followup(fu: FollowUp) -> None:
    import datetime
    fu.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    items = load_followups()
    for i, f in enumerate(items):
        if f.id == fu.id:
            items[i] = fu
            save_followups(items)
            return
    items.append(fu)
    save_followups(items)


def remove_followup(fid: str) -> bool:
    items = load_followups()
    kept = [f for f in items if f.id != fid]
    if len(kept) != len(items):
        save_followups(kept)
        return True
    return False


def clear_followups() -> int:
    items = load_followups()
    count = len(items)
    save_followups([])
    return count


# ---- sent items (Phase 0) --------------------------------------------------
# One row per approved send — the durable outcome log the pipeline / voice-stats / suppression /
# cost all fold over. Unlike drafts (cap 15) this is NOT aggressively trimmed: the stats need the
# full history. A generous cap keeps the file bounded on a long-running install.

SENT_ITEMS_CAP = 5000


def load_sent_items() -> list[SentItem]:
    if not SENT_ITEMS_FILE.exists():
        return []
    try:
        data = json.loads(SENT_ITEMS_FILE.read_text(encoding="utf-8"))
        return [SentItem.model_validate(x) for x in data.get("items", [])]
    except Exception:
        return []


def save_sent_items(items: list[SentItem]) -> None:
    trimmed = sorted(items, key=lambda s: s.approved_at or s.created_at or "", reverse=True)[:SENT_ITEMS_CAP]
    data = {"items": [s.model_dump(mode="json") for s in trimmed]}
    safe_write_text(SENT_ITEMS_FILE, json.dumps(data, indent=2, ensure_ascii=False))


def get_sent_item(sid: str) -> SentItem | None:
    return next((s for s in load_sent_items() if s.id == sid), None)


def upsert_sent_item(si: SentItem) -> None:
    import datetime
    si.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    items = load_sent_items()
    for i, s in enumerate(items):
        if s.id == si.id:
            items[i] = si
            save_sent_items(items)
            return
    items.append(si)
    save_sent_items(items)


def next_sent_item_id(slug: str) -> str:
    """A unique id per send for a slug: slug#0, slug#1, ... (bounce retries add rungs)."""
    n = sum(1 for s in load_sent_items() if s.slug == slug)
    return f"{slug}#{n}"


def clear_sent_items() -> int:
    items = load_sent_items()
    count = len(items)
    save_sent_items([])
    return count


# ---- suppressions / do-not-contact (Phase 4a) -----------------------------

def load_suppressions() -> list[dict]:
    if not SUPPRESSIONS_FILE.exists():
        return []
    try:
        data = json.loads(SUPPRESSIONS_FILE.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def save_suppressions(items: list[dict]) -> None:
    safe_write_text(SUPPRESSIONS_FILE, json.dumps({"items": items}, indent=2, ensure_ascii=False))


def clear_suppressions() -> int:
    items = load_suppressions()
    save_suppressions([])
    return len(items)


# ---- snippets / saved blocks (Phase 4b) -----------------------------------

def load_snippets() -> list[dict]:
    if not SNIPPETS_FILE.exists():
        return []
    try:
        data = json.loads(SNIPPETS_FILE.read_text(encoding="utf-8"))
        return data.get("items", [])
    except Exception:
        return []


def save_snippets(items: list[dict]) -> None:
    safe_write_text(SNIPPETS_FILE, json.dumps({"items": items}, indent=2, ensure_ascii=False))


# ---- session cost stats (Phase 1e) ----------------------------------------

def load_session_stats() -> dict:
    if not SESSION_STATS_FILE.exists():
        return {"cost": 0.0, "drafts": 0, "in": 0, "out": 0, "cached": 0, "by_model": {}}
    try:
        return json.loads(SESSION_STATS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"cost": 0.0, "drafts": 0, "in": 0, "out": 0, "cached": 0, "by_model": {}}


def save_session_stats(d: dict) -> None:
    try:
        safe_write_text(SESSION_STATS_FILE, json.dumps(d, indent=2, ensure_ascii=False))
    except Exception:
        pass


def reset_session_stats() -> None:
    save_session_stats({"cost": 0.0, "drafts": 0, "in": 0, "out": 0, "cached": 0, "by_model": {}})


def save_batch(batch: BatchState) -> None:
    safe_write_text(batch_path(batch.batch_id), batch.model_dump_json(indent=2))


def load_batch(batch_id: str) -> BatchState | None:
    p = batch_path(batch_id)
    if not p.exists():
        return None
    return BatchState.model_validate_json(p.read_text(encoding="utf-8"))


def save_cache(slug: str, cache: dict) -> None:
    safe_write_text(cache_path(slug), json.dumps(cache, indent=2, ensure_ascii=False))


def load_cache(slug: str) -> dict | None:
    p = cache_path(slug)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def list_custom_voices(kind: str | None = "outreach",
                       include_challengers: bool = False) -> list[CustomVoice]:
    """Voices of one kind (default 'outreach' so existing callers never see follow-up voices).
    Pass kind=None for every voice regardless of kind.

    `include_challengers` (default False) hides A/B challenger clones (voices with `challenger_of`
    set). Challengers are new, so the default keeps every existing caller — the editor, the voices
    list, follow-up routing — byte-for-byte its current self. Only the routing bandit opts in
    (`resolve_voice`) so a challenger can actually receive live sends while it is being tested."""
    voices = []
    for p in S.VOICES_DIR.glob("*.json"):
        try:
            v = CustomVoice.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not include_challengers and (getattr(v, "challenger_of", "") or ""):
            continue
        if kind is None or (getattr(v, "kind", "outreach") or "outreach") == kind:
            voices.append(v)
    return sorted(voices, key=lambda v: v.updated_at, reverse=True)


def get_custom_voice(voice_id: str) -> CustomVoice | None:
    p = S.VOICES_DIR / f"{voice_id}.json"
    if p.exists():
        try:
            return CustomVoice.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def save_custom_voice(voice: CustomVoice) -> None:
    import datetime
    voice.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    p = S.VOICES_DIR / f"{voice.id}.json"
    safe_write_text(p, voice.model_dump_json(indent=2))


def delete_custom_voice(voice_id: str) -> None:
    p = S.VOICES_DIR / f"{voice_id}.json"
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


# ---- Custom Sourcing Prompts -----------------------------------------------

def list_custom_sourcing_prompts() -> list[CustomSourcingPrompt]:
    """Return all custom sourcing prompts on disk, sorted by display_name."""
    out: list[CustomSourcingPrompt] = []
    if not S.SOURCING_PROMPTS_DIR.exists():
        return out
    for p in S.SOURCING_PROMPTS_DIR.glob("*.json"):
        try:
            sp = CustomSourcingPrompt.model_validate_json(p.read_text(encoding="utf-8"))
            out.append(sp)
        except Exception:
            continue
    return sorted(out, key=lambda x: x.display_name or x.id)


def get_custom_sourcing_prompt(prompt_id: str) -> CustomSourcingPrompt | None:
    p = S.SOURCING_PROMPTS_DIR / f"{prompt_id}.json"
    if p.exists():
        try:
            return CustomSourcingPrompt.model_validate_json(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def save_custom_sourcing_prompt(prompt: CustomSourcingPrompt) -> None:
    import datetime
    prompt.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    p = S.SOURCING_PROMPTS_DIR / f"{prompt.id}.json"
    safe_write_text(p, prompt.model_dump_json(indent=2))


def delete_custom_sourcing_prompt(prompt_id: str) -> None:
    p = S.SOURCING_PROMPTS_DIR / f"{prompt_id}.json"
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


# ---- voice version history (Layer 4) --------------------------------------
# Every learned (or manual, if the caller chooses) change to a voice is snapshotted here BEFORE the
# write, so any change is one-click reversible. This extends the draft loop's "restore the original"
# guarantee to the voice itself — the safety net that makes auto-apply safe. Bounded per voice.

VOICE_HISTORY_CAP = 30


def _voice_history_dir(voice_id: str) -> Path:
    safe = "".join(ch for ch in (voice_id or "voice") if ch.isalnum() or ch in "-_#") or "voice"
    d = S.VOICE_HISTORY_DIR / safe
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def save_voice_version(voice: CustomVoice, *, note: str = "") -> str:
    """Snapshot the CURRENT on-disk voice before it is overwritten. Returns the snapshot ts (its id).
    Reads the live file so the snapshot is exactly what was there, not the in-memory candidate.
    Never raises (a failed snapshot must not block a learning cycle — but then apply should abort)."""
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
    try:
        live = get_custom_voice(voice.id)
        payload = (live or voice).model_dump(mode="json")
        rec = {"ts": ts, "note": note, "voice_id": voice.id, "voice": payload}
        d = _voice_history_dir(voice.id)
        safe_write_text(d / f"{ts}.json", json.dumps(rec, indent=2, ensure_ascii=False))
        _trim_voice_history(voice.id)
        return ts
    except Exception:
        return ""


def _trim_voice_history(voice_id: str) -> None:
    try:
        d = _voice_history_dir(voice_id)
        snaps = sorted(d.glob("*.json"), key=lambda p: p.name, reverse=True)
        for p in snaps[VOICE_HISTORY_CAP:]:
            try:
                p.unlink()
            except Exception:
                pass
    except Exception:
        pass


def list_voice_versions(voice_id: str) -> list[dict]:
    """Newest-first list of {ts, note, display_name} for a voice's snapshots. Never raises."""
    out: list[dict] = []
    try:
        d = _voice_history_dir(voice_id)
        for p in sorted(d.glob("*.json"), key=lambda p: p.name, reverse=True):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
                out.append({"ts": rec.get("ts", p.stem), "note": rec.get("note", ""),
                            "display_name": (rec.get("voice") or {}).get("display_name", "")})
            except Exception:
                continue
    except Exception:
        pass
    return out


def get_voice_version(voice_id: str, ts: str) -> CustomVoice | None:
    try:
        p = _voice_history_dir(voice_id) / f"{ts}.json"
        if not p.exists():
            return None
        rec = json.loads(p.read_text(encoding="utf-8"))
        return CustomVoice.model_validate(rec.get("voice") or {})
    except Exception:
        return None


def restore_voice_version(voice_id: str, ts: str) -> CustomVoice | None:
    """Roll a voice back to a snapshot. Snapshots the current state first (so a rollback is itself
    reversible), then writes the historical voice over the live one. Returns the restored voice."""
    v = get_voice_version(voice_id, ts)
    if v is None:
        return None
    cur = get_custom_voice(voice_id)
    if cur is not None:
        save_voice_version(cur, note=f"pre-rollback (to {ts})")
    save_custom_voice(v)
    return v


# ---- export (read-only, flattened) ----------------------------------------

_EXPORT_COLUMNS = [
    "target", "ref", "voice", "role_exists", "company_size",
    "contact_name", "contact_title", "contact_email", "email_confidence",
    "contact_unverified", "disqualified", "subject", "state", "status", "edited",
    "final_email",
]


def _export_row(cs: CompanyState) -> list:
    contact = ((cs.cache or {}).get("contact")) or {}
    return [
        cs.name,
        cs.ref or "",
        cs.voice or "",
        "" if cs.role_exists is None else ("yes" if cs.role_exists else "no"),
        cs.company_size or "",
        contact.get("name", ""),
        contact.get("title", ""),
        (cs.spec or {}).get("send_to", "") or contact.get("email", ""),
        contact.get("email_confidence", ""),
        "yes" if cs.contact_unverified else "no",
        "yes" if cs.disqualified else "no",
        cs.subject or "",
        cs.state.value,
        cs.status_pill,
        "yes" if cs.was_edited() else "no",
        cs.final_email or cs.machine_email or "",
    ]


def _archive_export_row(rec: dict) -> list:
    contact = rec.get("contact") or {}
    return [
        rec.get("name", ""), rec.get("ref", ""), rec.get("voice", ""),
        "", "",  # role_exists / company_size not retained in archive
        contact.get("name", ""), contact.get("title", ""),
        contact.get("email", ""), contact.get("email_confidence", ""),
        "yes" if rec.get("contact_unverified") else "no", "no",
        rec.get("subject", ""), "ready", "", "",
        rec.get("final_email", "") or rec.get("machine_email", ""),
    ]


def _export_rows(scope: str) -> list[list]:
    if scope == "archive":
        return [_archive_export_row(r) for r in load_archive()]
    return [_export_row(cs) for cs in load_drafts()]


def export_csv_bytes(batch: BatchState | None = None, scope: str = "drafts") -> bytes:
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(_EXPORT_COLUMNS)
    for row in _export_rows(scope):
        w.writerow(row)
    return buf.getvalue().encode("utf-8-sig")


def export_xlsx_bytes(batch: BatchState | None = None, scope: str = "drafts") -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Outreach"
    ws.append(_EXPORT_COLUMNS)
    for row in _export_rows(scope):
        ws.append(row)
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def export_count(scope: str = "drafts") -> int:
    return len(_export_rows(scope))
