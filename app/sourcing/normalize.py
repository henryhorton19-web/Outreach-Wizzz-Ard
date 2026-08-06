"""Canonical slug folding for sourcing candidates."""
from __future__ import annotations

import re
from urllib.parse import urlparse
from app.slugs import slug as base_slug

_LEGAL_SUFFIXES = re.compile(
    r"\b(sas|sarl|sa|spa|ltd|limited|inc|incorporated|gmbh|ag|bv|nv|co|corp|corporation|llc|plc|oy|ab|as|aps|kft|sl|holding|holdings)\b|"
    r"\bsp\.?\s*z\.?\s*o\.?\s*o\.?\b",
    re.IGNORECASE,
)


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
    parts = domain.split(".")
    root = parts[0] if len(parts) >= 2 else domain
    return base_slug(root)


def canonicalize_name(name: str, domain: str = "") -> str:
    """Strip common legal suffixes and normalize company name/domain for deduplication."""
    clean = _LEGAL_SUFFIXES.sub("", name or "").strip()
    clean_slug = base_slug(clean or name)
    if not clean_slug and domain:
        return canonicalize_domain(domain)
    if domain:
        dom_slug = canonicalize_domain(domain)
        if dom_slug and (dom_slug in clean_slug or clean_slug in dom_slug):
            return dom_slug if len(dom_slug) <= len(clean_slug) else clean_slug
    return clean_slug


def entity_slug(name: str, domain: str = "") -> str:
    """Alias for canonicalize_name providing standard entity slug derivation."""
    return canonicalize_name(name, domain=domain)
