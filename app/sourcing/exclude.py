"""Permanent exclusion layer for contacted entities (Part 5 of EXECUTION_PLAN_5).

Maintains a set of excluded entity slugs/domains loaded from $WIZZARD_DATA_DIR/excluded.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from app.sourcing.normalize import canonicalize_name, canonicalize_domain


def build_exclusion_set(raw_entries: list) -> set[str]:
    """Parse raw export strings or dicts into a set of canonical entity slugs."""
    result: set[str] = set()
    for item in raw_entries:
        if isinstance(item, str):
            val = item.strip()
            if not val:
                continue
            # If it looks like a domain (contains a dot), parse it as one
            if "." in val and not " " in val:
                slug = canonicalize_domain(val)
            else:
                slug = canonicalize_name(val)
            if slug:
                result.add(slug)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("slug") or item.get("company") or ""
            dom = item.get("domain") or item.get("website") or ""
            if name:
                slug = canonicalize_name(name, domain=dom)
            else:
                slug = canonicalize_domain(dom)
            if slug:
                result.add(slug)
    return result


def exclusion_info() -> dict:
    """Return summary statistics for the live exclusion list."""
    from app import store, settings as S
    st = S.load_settings()
    excluded = store.excluded_slugs()
    return {
        "total": len(excluded),
        "enabled": getattr(st, "exclusion_enabled", True),
        "file_exists": (S.DATA_DIR / "excluded.json").exists(),
    }
