"""Seen ledger (sourced_seen.json) for deduplication and novelty tracking."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from app import settings as S


def seen_file_path() -> Path:
    return S.DATA_DIR / "sourced_seen.json"


def load_seen() -> dict[str, dict]:
    """Load seen ledger mapping slug -> record."""
    p = seen_file_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("seen", {})
    except Exception:
        return {}


def save_seen(seen_map: dict[str, dict]) -> None:
    S.atomic_write_text(seen_file_path(), json.dumps({"seen": seen_map}, indent=2, ensure_ascii=False))


def is_seen(slug: str, expiry_days: int = 60) -> bool:
    """Check if a candidate slug has been seen within the expiry window."""
    seen_map = load_seen()
    rec = seen_map.get(slug)
    if not rec:
        return False
    seen_at_str = rec.get("seen_at")
    if not seen_at_str:
        return False
    try:
        seen_at = datetime.fromisoformat(seen_at_str)
        now = datetime.now(timezone.utc)
        if (now - seen_at) > timedelta(days=expiry_days):
            return False   # Expired
        return True
    except Exception:
        return False


def record_seen(slug: str, name: str, verdict: str = "seen", reason: str = "") -> None:
    """Record a candidate slug as seen in the ledger."""
    seen_map = load_seen()
    now_iso = datetime.now(timezone.utc).isoformat()
    seen_map[slug] = {
        "slug": slug,
        "name": name,
        "verdict": verdict,
        "reason": reason,
        "seen_at": now_iso,
    }
    save_seen(seen_map)
