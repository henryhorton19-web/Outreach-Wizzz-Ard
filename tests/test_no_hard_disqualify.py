"""Research is never refused. A completed cache always produces a draft.

Before this fix, app/pipeline.py raised RuntimeError("disqualified: ...") when
the researched company failed a hardcoded Paris-or-remote-English gate -- so
researching a Polish company under a European FUND voice crashed with "making it
a disqualifier for a Paris-based candidate".

The research cache is saved BEFORE that throw, so the tokens were already spent.
Refusing could never save money; it only discarded work already paid for.
"""
import pathlib

import pytest

from app import validate as validate_mod


def _cache(work_mode="disqualify", lang="Polish", disq=True, reason="focused on Poland"):
    return {
        "company": {"name": "Jutro Medical", "work_mode": work_mode,
                    "working_language": lang, "disqualified": disq,
                    "disqualify_reason": reason,
                    "role_exists": False, "company_size": "small"},
        "contact": {"name": "Jane Doe", "email": "jane@jutro.io"},
        "proof_points": [{"fact": "Raised a Series A", "source": "https://x.com"}],
        "situation_read": "expanding across Poland",
    }


def test_pipeline_no_longer_raises_on_a_disqualified_company():
    """The reported crash, as a test."""
    src = pathlib.Path(__file__).parent.parent / "app" / "pipeline.py"
    text = src.read_text(encoding="utf-8")
    assert 'raise RuntimeError(f"disqualified' not in text, \
        "pipeline still raises on a disqualified company -- research is still being refused"


def test_fit_check_returns_an_advisory_not_a_verdict():
    """is_disqualified is replaced by a scored advisory that never blocks."""
    assert hasattr(validate_mod, "fit_notes"), \
        "expected a fit_notes() advisory to replace the hard is_disqualified() gate"
    notes = validate_mod.fit_notes(_cache(), audience="organisation")
    assert isinstance(notes, list)


def test_an_org_voice_gets_no_geographic_fit_warning():
    """A fund sourcing deals across Europe does not care where the target's staff
    sit. That is the whole point of the investment."""
    notes = validate_mod.fit_notes(_cache(), audience="organisation")
    joined = " ".join(str(n) for n in notes).lower()
    assert "paris" not in joined, f"org voice got a Paris-based fit warning: {notes}"
    assert "commute" not in joined


def test_a_self_voice_still_gets_the_advisory():
    """A candidate job search legitimately wants this signal -- as advice, not a crash."""
    notes = validate_mod.fit_notes(_cache(), audience="self")
    assert notes, "a self-audience voice should still receive a location/language advisory"


def test_no_engine_file_names_a_specific_city_or_country():
    """Mission-specific hardcodes must not survive in the engine."""
    root = pathlib.Path(__file__).parent.parent
    banned = ("paris_office", "marseille", "toulouse", "bordeaux")
    offenders = []
    for rel in ("app/research.py", "app/validate.py", "app/pipeline.py", "app/sourcing/gates.py"):
        text = (root / rel).read_text(encoding="utf-8").lower()
        for b in banned:
            if b in text:
                offenders.append(f"{rel}:{b}")
    assert not offenders, f"mission-specific hardcodes remain: {offenders}"
