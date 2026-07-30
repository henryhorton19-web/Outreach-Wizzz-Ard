"""Canonical slug folding for sourcing candidates."""
from __future__ import annotations

import re
from urllib.parse import urlparse
from app.slugs import slug as base_slug

_LEGAL_SUFFIXES = re.compile(
    r"\b(sas|sarl|sa|spa|ltd|limited|inc|incorporated|gmbh|ag|bv|nv|co|corp|corporation|llc|plc)\b",
    re.IGNORECASE,
)


def canonicalize_name(name: str) -> str:
    """Strip common legal suffixes and normalize company name for deduplication."""
    clean = _LEGAL_SUFFIXES.sub("", name or "").strip()
    return base_slug(clean or name)


def canonicalize_domain(url_or_domain: str) -> str:
    """Extract canonical domain slug from a URL or domain string."""
    if not url_or_domain:
        return ""
    text = url_or_domain.strip().lower()
    if "://" in text:
        netloc = urlparse(text).netloc
    else:
        netloc = text.split("/")[0]
    domain = re.sub(r"^www\.", "", netloc)
    return base_slug(domain)
