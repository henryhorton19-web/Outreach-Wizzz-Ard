"""Audit trail. Per approved target we record: the sourced facts and their source URLs, the
ORIGINAL machine draft alongside the reviewer's EDITED final, and the approval record (voice, OS
user, timestamp). No API keys are ever written here.
"""
from __future__ import annotations

import getpass
import json
from datetime import datetime, timezone

from . import settings as S
from . import store
from .models import CompanyState


def os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "unknown"


def build_record(cs: CompanyState, voice: str, batch_id: str) -> dict:
    cache = cs.cache or {}
    contact = cache.get("contact") or {}
    return {
        "slug": cs.slug,
        "name": cs.name,
        "ref": cs.ref or "",
        "voice": voice,
        "batch_id": batch_id,
        "role_exists": cs.role_exists,
        "company_size": cs.company_size,
        "subject": cs.subject or "",
        "contact": {
            "name": contact.get("name", ""),
            "title": contact.get("title", ""),
            "email": (cs.spec or {}).get("send_to") or contact.get("email", ""),
            "email_confidence": contact.get("email_confidence", ""),
            "contact_verified": contact.get("contact_verified", False),
        },
        "proof_points": [p.get("fact", "") for p in (cache.get("proof_points") or []) if isinstance(p, dict)],
        "recent_point": (cache.get("recent_point") or {}).get("detail", ""),
        "evidence_sources": cache.get("evidence_sources") or [],
        "machine_email": cs.machine_email or "",
        "final_email": cs.final_email or "",
        "was_edited": cs.was_edited(),
        "contact_unverified": cs.contact_unverified,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approver_os_user": os_user(),
        "notes": [n.model_dump() for n in cs.notes],
    }


def write_record(record: dict) -> None:
    path = S.AUDIT_DIR / f"{record['slug']}_{record['approved_at'].replace(':', '').replace('.', '')}.json"
    store.safe_write_text(path, json.dumps(record, indent=2, ensure_ascii=False))
