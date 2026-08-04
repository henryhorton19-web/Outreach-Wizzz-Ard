"""Engine tests: the deterministic core's guarantees.

Run: cd paris_app && PYTHONPATH=.:engine python -m pytest tests/ -q
(or: python -m pytest tests -q  with conftest adding paths)
"""
import re
import pytest

import draft_engine as de
import config as C


def _cache(role_exists=True, size="small", proof=None, recent=True, wm="remote_english",
           lang="English", disq=False):
    return {
        "company": {"name": "Acme", "what_they_do": "B2B software.",
                    "role_exists": role_exists, "role_title": "Analyst", "company_size": size,
                    "work_mode": wm, "working_language": lang, "disqualified": disq},
        "proof_points": proof or [
            {"fact": "Acme serves mid-market SaaS teams.", "source": "https://a", "kind": "product"},
            {"fact": "Acme grew revenue across two markets.", "source": "https://b", "kind": "traction"},
        ],
        "recent_point": {"present": recent, "kind": "raise", "detail": "Acme's seed round",
                         "source": "https://c"},
        "contact": {"status": "found", "name": "Alex Founder", "title": "CEO",
                    "email": "alex@acme.com", "email_confidence": "medium", "contact_verified": True},
        "situation_read": "Acme is scaling its commercial side",
        "evidence_sources": ["https://a", "https://b", "https://c"],
        "overall_confidence": "medium",
    }


def test_prepare_produces_tie_not_frame():
    # The engine no longer owns the frame: prepare supplies the CV tie + allowed_facts, and the
    # frame slots come back empty (the app layer fills them from the chosen voice).
    spec = de.prepare(_cache(), "role_small")
    assert spec["greeting"] == "" and spec["opening_fallback"] == "" and spec["ask"] == ""
    assert spec["evidence"], "the tie must select at least one profile experience"
    # allowed_facts must include target proof + candidate anchors, tagged by side
    sides = {f["about"] for f in spec["allowed_facts"]}
    assert "target" in sides and "candidate" in sides


def test_mock_email_is_gate_clean():
    for voice in ("no_role_small", "role_small", "role_large"):
        spec = de.prepare(_cache(role_exists=(voice != "no_role_small")), voice)
        parts = de.mock_email(spec)
        assert parts["body"].strip()
        rep = de.critique(parts["body"], spec["ask"], spec)
        assert not rep.hard, f"{voice} mock produced hard notes: {rep.hard}"


def test_no_dashes_normalized():
    assert "\u2014" not in de.normalize("a\u2014b")
    assert "--" not in de.normalize("a--b")


def test_numeric_guard_blocks_untraceable_number():
    spec = de.prepare(_cache(), "role_small")
    # 999 is neither in the profile whitelist nor any allowed fact
    rep = de.critique("We serve 999 enterprise logos already.", spec["ask"], spec)
    assert any("number not from facts" in h for h in rep.hard)


def test_numeric_guard_allows_profile_number():
    spec = de.prepare(_cache(), "role_small")
    # 160 (the Xelix $160m) is whitelisted in the profile
    rep = de.critique("I supported a $160m fundraise at Solano.", spec["ask"], spec)
    assert not any("number not from facts" in h for h in rep.hard)


def test_timeline_guard_flags_completed_hpe():
    spec = de.prepare(_cache(), "role_small")
    rep = de.critique("I worked at HPE last summer.", spec["ask"], spec)
    assert any("timeline" in h for h in rep.hard)


def test_finalize_assembles_body_with_empty_frame():
    # finalize is now frame-light (the live path uses the app's assemble_custom). With prepare's
    # empty frame, finalize still normalises and embeds the body, prepended by the recent opening.
    spec = de.prepare(_cache(), "role_small")
    parts = {"body": "This is the composed body about building inside a company.", "ask": ""}
    out = de.finalize(spec, parts)
    email = out["email"]
    assert "seed round" in email.lower()            # opening derived from the recent point
    assert out["machine_body"] in email             # the edit anchor is embedded verbatim
    assert "\u2014" not in email and "--" not in email


def test_word_count_soft_note_out_of_range():
    spec = de.prepare(_cache(), "role_small")
    rep = de.critique("Too short.", spec["ask"], spec)
    assert "word count" in rep.soft


def test_tie_selects_by_signal():
    # fundraising target -> fund_co; AI target -> tech_co
    cache_ai = _cache(proof=[{"fact": "an LLM agent platform for AI research"}], recent=False)
    fund = de._select_evidence(_cache(proof=[{"fact": "raised a Series A led by a growth fund"}], recent=False), "role_small")
    ai = de._select_evidence(cache_ai, "role_small")
    fund_keys = {e["_key"] for e in fund}
    ai_keys = {e["_key"] for e in ai}
    assert "fund_co" in fund_keys
    assert "tech_co" in ai_keys


def test_innova_gated_off_by_default():
    # a generic target with no ownership/ops/zero-to-one signal must NOT surface side_co
    ev = de._select_evidence(_cache(proof=[{"fact": "a payments API for banks"}]), "role_large")
    assert "side_co" not in {e["_key"] for e in ev}


def test_first_name_strips_honorifics():
    assert de._first_name("Dr. Jane Smith") == "Jane"
    assert de._first_name("") == "there"
