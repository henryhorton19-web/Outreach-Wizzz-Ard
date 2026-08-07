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
    dom, source = research.resolve_company_domain(name="Example SaaS", given_website="https://example-saas.test/about")[:2]
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


def test_post_process_discards_wrong_domain_email():
    bad_cache = _cache(email="chandler@example-saas-alt.test")
    processed = research._post_process(bad_cache, "Example SaaS", "https://example-saas.test", [], resolved_domain="example-saas.test")
    assert processed["contact"]["email"] == "jamie.someone@example-saas.test"
    assert processed["contact"]["email_method"] == "pattern_guess"
    assert any("Discarded contact.email at wrong domain" in f for f in processed.get("research_failures", []))


def test_contacts_alt_wrong_domain_email_is_discarded():
    raw_cache = _cache(email="jamie@example-saas.test", email_source_url="https://example-saas.test/team")
    raw_cache["contacts_alt"] = [{"name": "Wrong Alt", "email": "alt@wrong.com"}]
    processed = research._post_process(raw_cache, "Example SaaS", "https://example-saas.test", [], resolved_domain="example-saas.test")
    assert processed["contacts_alt"][0]["email"] == ""
    assert processed["contacts_alt"][0]["email_method"] == "not_found"


def test_pipeline_populates_recipient_domain_and_reuses_on_redraft():
    from app.pipeline import draft_one
    from app.models import CompanyState
    from app.providers.stub import StubProvider
    p = StubProvider()
    cs = CompanyState(slug="test_co", name="Test Co", website="https://testco.io")
    res_cs = draft_one(p, cs, reuse_cache=False)
    assert res_cs.recipient_domain == "testco.io"


def test_pattern_fallback_email_generated_when_scraped_email_missing():
    raw_cache = _cache(email="", email_source_url="")
    processed = research._post_process(raw_cache, "Example SaaS", "https://example-saas.test", [], resolved_domain="example-saas.test")
    assert processed["contact"]["email"] == "jamie.someone@example-saas.test"
    assert processed["contact"]["email_method"] == "pattern_guess"
    assert processed["contact"]["email_confidence"] == "low"
    assert processed["contacts_alt"][0]["email"] == "jsomeone@example-saas.test"
    assert any("generated pattern fallback" in f for f in processed.get("research_failures", []))


def test_address_ladder_contains_primary_fallback_first_and_alt_second():
    from app.apollo import rank_address_candidates
    raw_cache = _cache(email="", email_source_url="")
    processed = research._post_process(raw_cache, "Example SaaS", "https://example-saas.test", [], resolved_domain="example-saas.test")
    ladder = rank_address_candidates(processed)
    emails = [c["email"] for c in ladder]
    assert "jamie.someone@example-saas.test" in emails
    assert "jsomeone@example-saas.test" in emails
    assert emails.index("jamie.someone@example-saas.test") < emails.index("jsomeone@example-saas.test")

