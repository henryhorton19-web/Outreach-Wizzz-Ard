"""A target must not reach composition when the inputs cannot support an honest letter (Plan 31, A4).

Observed: Fabrikam ID was composed with contact_first="Unknown", a pattern-guessed unknown@fabrikam-id.test and
a placeholder founder, and produced "Hi Unknown,". A fluent wrong letter is worse than no letter --
it is sendable.
"""
import pytest

from app import preflight


def _ok_cache():
    return {"company": {"name": "Fabrikam ID", "what_they_do": "e-commerce listing automation",
                        "resolved_domain": "fabrikam-id.test"},
            "contact": {"name": "Ada Lovelace", "email": "ada@fabrikam-id.test",
                        "email_method": "found_on_page"},
            "proof_points": [{"fact": "unifies messy product data"},
                             {"fact": "publishes to multiple channels"}]}


def test_a_complete_cache_passes():
    assert preflight.blockers(_ok_cache()) == []


def test_placeholder_contact_name_blocks():
    c = _ok_cache(); c["contact"]["name"] = "Unknown"
    assert any("contact" in b.lower() for b in preflight.blockers(c))


@pytest.mark.parametrize("junk", ["unknown", "N/A", "TBD", "founder", "team", "", "  "])
def test_every_placeholder_form_blocks(junk):
    c = _ok_cache(); c["contact"]["name"] = junk
    assert preflight.blockers(c)


def test_unknown_local_part_blocks():
    c = _ok_cache(); c["contact"]["email"] = "unknown@fabrikam-id.test"
    assert preflight.blockers(c)


def test_thin_research_blocks():
    c = _ok_cache(); c["proof_points"] = []
    assert any("research" in b.lower() or "fact" in b.lower() for b in preflight.blockers(c))


def test_missing_what_they_do_blocks():
    c = _ok_cache(); c["company"]["what_they_do"] = ""
    assert preflight.blockers(c)


def test_a_guessed_address_alone_does_not_block():
    # a guessed address on a real person is a warning, not a blocker: the operator can correct it
    c = _ok_cache(); c["contact"]["email_method"] = "pattern_guess"
    assert preflight.blockers(c) == []


def test_blockers_never_raise():
    for bad in (None, {}, {"contact": None}, {"company": 3}):
        assert isinstance(preflight.blockers(bad), list)
