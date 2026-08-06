"""Orchestrator and background job registry for 'Find new targets' sourcing."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app import store
from app import suppression
from app.sourcing.harvest import AVAILABLE_HARVESTERS
from app.sourcing.seen import is_seen, record_seen
from app.sourcing.verify import verify_candidate
from app.sourcing.screen import screen_candidate

_ACTIVE_JOBS: dict[str, dict] = {}
_LAST_RUN: dict | None = None


def get_active_job(job_id: str) -> dict | None:
    return _ACTIVE_JOBS.get(job_id)


def get_last_run() -> dict | None:
    return _LAST_RUN


def cancel_job(job_id: str) -> bool:
    job = _ACTIVE_JOBS.get(job_id)
    if job:
        job["status"] = "cancelled"
        job["stage"] = "Cancelled"
        return True
    return False


def start_sourcing_job(settings: Any,
                       target_n: int = 10,
                       max_candidates: int = 40,
                       recency_days: int = 120,
                       sources: list[str] | None = None,
                       sourcing_prompt_id: str | None = None,
                       fixture_harvest: list[dict] | None = None) -> dict:
    """Start a new sourcing job in background or synchronous mode."""
    job_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).isoformat()
    sources = sources or getattr(settings, "sourcing_sources", ["techeu_funding_feed", "grounded_search"])

    # Load custom prompt if specified
    custom_prompt = None
    if sourcing_prompt_id:
        custom_prompt = store.get_custom_sourcing_prompt(sourcing_prompt_id)
        if custom_prompt:
            if custom_prompt.sources:
                sources = custom_prompt.sources
            if custom_prompt.recency_days:
                recency_days = custom_prompt.recency_days

    job = {
        "job_id": job_id,
        "started_at": started_at,
        "status": "running",
        "stage": "Harvesting",
        "sources": sources,
        "target_n": target_n,
        "max_candidates": max_candidates,
        "sourcing_prompt_id": sourcing_prompt_id,
        "counts": {
            "harvested": 0,
            "checked": 0,
            "already_seen": 0,
            "new": 0,
            "accepted": 0,
            "held": 0,
            "rejected": 0,
            "queued": 0,
        },
        "spend_usd": 0.0,
        "candidates": [],
        "errors": [],
        "notes": [],
        "added_slugs": [],
    }
    _ACTIVE_JOBS[job_id] = job

    # Run execution synchronous for clean predictability
    _execute_job(job, settings, recency_days, max_candidates, custom_prompt, fixture_harvest)
    return job


def _execute_job(job: dict, settings: Any, recency_days: int, max_candidates: int,
                  custom_prompt: Any | None, fixture_harvest: list[dict] | None = None) -> None:
    global _LAST_RUN
    sources = job["sources"]
    harvested_raw: list[dict] = []

    # 1. HARVEST
    if fixture_harvest:
        harvested_raw = fixture_harvest
    else:
        for s_id in sources:
            adapter = AVAILABLE_HARVESTERS.get(s_id)
            if not adapter:
                continue
            try:
                provider = getattr(settings, "provider_instance", None)
                raw_items = adapter.harvest(recency_days=recency_days, max_items=max_candidates, custom_prompt=custom_prompt, provider=provider)
                harvested_raw.extend(raw_items)
            except Exception as e:
                job["errors"].append(f"Source {s_id} error: {e}")

    job["counts"]["harvested"] = len(harvested_raw)
    job["stage"] = "Verification & Novelty Check"

    # Dedup check setup
    existing_slugs = store.queue_slugs() | {cs.slug for cs in store.load_drafts()}
    reject_expiry = getattr(settings, "sourcing_reject_expiry_days", 60)

    # 2. PRE-VERIFICATION SEEN CHECK & GATING
    candidates_to_verify = []
    for raw in harvested_raw:
        slug = raw["slug"]
        job["counts"]["checked"] += 1

        if is_seen(slug, expiry_days=reject_expiry):
            job["counts"]["already_seen"] += 1
            continue

        if slug in existing_slugs:
            job["counts"]["already_seen"] += 1
            continue

        job["counts"]["new"] += 1
        candidates_to_verify.append(raw)
        if len(candidates_to_verify) >= max_candidates:
            break

    # 3. VERIFY & SCREEN
    ingest_rows = []
    for raw in candidates_to_verify:
        if job["status"] == "cancelled":
            break

        verified = verify_candidate(raw, custom_prompt=custom_prompt)
        screened = screen_candidate(verified)
        job["candidates"].append(screened)

        slug = screened["canon_slug"]
        name = screened["name"]
        verdict = screened["verdict"]

        record_seen(slug, name, verdict=verdict, reason=screened.get("reject_reason", ""))

        if verdict == "accept" and screened.get("tier") == "Tier 1":
            job["counts"]["accepted"] += 1
            meta = dict(raw.get("meta") or {})
            meta["website_source"] = screened.get("website_source", "unresolved")
            if job.get("sourcing_prompt_id"):
                meta["sourced_by_preset"] = job["sourcing_prompt_id"]
            if meta["website_source"] == "unresolved":
                job["counts"]["unresolved_website"] = job["counts"].get("unresolved_website", 0) + 1
            ingest_rows.append({
                "slug": slug,
                "name": name,
                "ref": screened.get("website", ""),
                "meta": meta,
            })
        elif verdict == "needs_review" or screened.get("tier") == "Tier 2":
            job["counts"]["held"] += 1
        else:
            job["counts"]["rejected"] += 1
            
    # 4. AUTO-QUEUE (If sourcing_enabled)
    sourcing_enabled = getattr(settings, "sourcing_enabled", True)
    if sourcing_enabled and ingest_rows:
        from app.server import _ingest_to_queue
        list_id = store.active_list_id()
        res = _ingest_to_queue(ingest_rows, list_id=list_id)
        added_count = res.get("added") or 0
        job["counts"]["queued"] = added_count
        job["added_slugs"] = [r["slug"] for r in ingest_rows]
        # Undo must reverse the list the rows actually went into, not whichever
        # list happens to be active when the user clicks undo.
        job["added_list_id"] = list_id

    # Novelty note
    chk = job["counts"]["checked"]
    seen_c = job["counts"]["already_seen"]
    new_c = job["counts"]["new"]
    job["notes"].append(f"Checked {chk} candidates from sources · {seen_c} already seen from prior runs · {new_c} genuinely new.")

    if new_c == 0 and chk > 0:
        job["notes"].append("No new candidates found from these sources in the current recency window. Try widening recency or adding a custom prompt.")

    # Update prompt metrics if prompt used
    if custom_prompt:
        try:
            custom_prompt.last_run_at = datetime.now(timezone.utc).isoformat()
            custom_prompt.total_candidates_seen += new_c
            store.save_custom_sourcing_prompt(custom_prompt)
        except Exception:
            pass

    job["status"] = "completed"
    job["stage"] = "Completed"
    _LAST_RUN = job


def undo_sourcing_job(job_id: str) -> dict:
    """Remove queue rows added by a sourcing job."""
    job = _ACTIVE_JOBS.get(job_id) or _LAST_RUN
    if not job or not job.get("added_slugs"):
        return {"removed": 0, "skipped_drafted": 0}

    slugs = job.get("added_slugs", [])
    undo_list_id = job.get("added_list_id") or "default"
    draft_slugs = {cs.slug for cs in store.load_drafts()}
    removed = 0
    skipped_drafted = 0

    for s in slugs:
        if s in draft_slugs:
            skipped_drafted += 1
            continue
        if store.remove_from_queue(s, list_id=undo_list_id):
            removed += 1

    return {"removed": removed, "skipped_drafted": skipped_drafted,
            "list_id": undo_list_id}
