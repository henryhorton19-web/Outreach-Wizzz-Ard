"""Is this research cache good enough to write an email from?

When research fails schema validation, research.py falls back to a minimal cache
whose facts are literal placeholder strings it writes itself, for example
"research incomplete before facts were confirmed". The composer treats those as
facts and produces an empty body.

Because the placeholders are written by this codebase rather than by a model,
they can be matched exactly. Salvage having run is NOT the test: a cache that hit
a ValidationError and still carries real sourced facts is perfectly usable, and
re-researching it would spend the search budget for nothing.
"""
from __future__ import annotations

# Written verbatim by research.py's minimal-cache branch. Matching the code's own
# strings rather than guessing at model output is what makes this reliable.
_PLACEHOLDERS = (
    "research incomplete before facts were confirmed",
    "market context was not established before research stopped",
    "company details were not verified before research stopped",
    "salvage fell back to a minimal cache",
)


def _blob(cache: dict) -> str:
    facts = " ".join(str(p.get("fact", "")) for p in (cache.get("proof_points") or [])
                     if isinstance(p, dict))
    thesis = " ".join(str(v) for v in (cache.get("thesis") or {}).values())
    failures = " ".join(str(f) for f in (cache.get("research_failures") or []))
    return f"{facts} {thesis} {failures}".lower()


def degraded_reasons(cache: dict) -> list[str]:
    """Why this cache cannot produce a usable draft. Empty means it can."""
    if not cache:
        return ["the research cache is empty"]

    reasons: list[str] = []
    blob = _blob(cache)
    hits = [p for p in _PLACEHOLDERS if p in blob]
    if hits:
        reasons.append(f"the cache carries placeholder text where facts should be: {hits[0]!r}")

    real_facts = [p for p in (cache.get("proof_points") or [])
                  if isinstance(p, dict) and str(p.get("fact", "")).strip()
                  and not any(ph in str(p.get("fact", "")).lower() for ph in _PLACEHOLDERS)]
    if not real_facts:
        reasons.append("the cache has no usable proof point")

    company = cache.get("company") or {}
    if not str(company.get("what_they_do") or "").strip():
        reasons.append("the cache does not say what the company does")

    return reasons


def is_degraded(cache: dict) -> bool:
    """True when a draft built from this cache would be empty or near-empty."""
    return bool(degraded_reasons(cache))
