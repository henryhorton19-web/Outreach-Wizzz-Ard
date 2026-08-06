"""Regression tests for domain-pinned contact discovery.

The bug: research for a company returned chandler@example-saas-alt.test and lauren@example-saas-alt.test
(wrong person, wrong domain) when the correct answer was jamie@example-saas.test. Every
test here fails against the code that produced that bug.
"""
import app.research as research


def _cache(email="", email_confidence="low", contact_verified=False,
          email_source_url="", company_website="https://example-saas.test"):
    return {
        "company": {"name": "Example SaaS", "website": company_website, "role_exists": False,
                    "company_size": "small", "company_size_evidence": "seed, 12 staff",
                    "work_mode": "remote_english", "working_language": "English"},
        "contact": {"status": "found", "name": "Jamie Someone", "role_basis": "founder",
                    "email": email, "email_confidence": email_confidence,
                    "contact_verified": contact_verified, "email_source_url": email_source_url},
        "situation_read": "recently raised a seed round",
        "proof_points": [{"fact": "Raised seed", "source": "https://techcrunch.com/x",
                         "kind": "funding", "staleness": "fresh"}],
        "evidence_sources": ["https://techcrunch.com/x"],
        "overall_confidence": "medium",
    }


def test_email_domain_must_match_the_resolved_company_domain():
    """The bug in one assertion: an email at a domain that is not the company's
    resolved domain must be rejected, not passed through as a best guess."""
    bad = _cache(email="chandler@example-saas-alt.test")  # company website is example-saas.test
    gaps = research._contact_gaps(bad)
    assert any("domain" in g for g in gaps), \
        "a wrong-domain email was not flagged as a gap"


def test_matching_domain_with_no_source_is_flagged_as_unverified_guess():
    """A correct-domain email with no citable source must still be labelled a
    guess, not silently accepted as if it were found on a page."""
    guess = _cache(email="jamie@example-saas.test", email_source_url="")
    gaps = research._contact_gaps(guess)
    assert any("source" in g for g in gaps)


def test_matching_domain_with_a_real_source_passes():
    good = _cache(email="jamie@example-saas.test", email_confidence="high",
                  contact_verified=True, email_source_url="https://example-saas.test/team")
    assert research._contact_gaps(good) == []


def test_resolve_domain_prefers_an_explicitly_given_website():
    dom, source = research.resolve_company_domain(name="Example SaaS", given_website="https://example-saas.test/about")
    assert dom == "example-saas.test"
    assert source == "given"


def test_the_fabrication_instruction_is_gone_from_the_prompt():
    """The literal instruction that caused the bug must not be in the prompt sent
    to the model. This test reads the actual string sent, not a docstring."""
    contract = research._OUTPUT_CONTRACT
    assert "NEVER blank" not in contract, \
        "the model is still told to never leave the email blank"
    assert "best-guess" not in contract.lower() or "confirmed_domain" in contract, \
        "a best-guess instruction survives with no domain constraint attached"
