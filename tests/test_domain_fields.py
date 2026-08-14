"""`recipient_domain` was carrying two unrelated meanings (Plan 31, A5/A6).

  company_domain  -- WHICH COMPANY IS THIS. The identity anchor. Operator-supplied website first.
  recipient_domain -- WHICH MAILBOX AM I WRITING TO. Derived from the address actually being sent.

They are usually equal and must not be the same variable: typing an address must not silently
rewrite the company's identity, and a supplied website must pin the mailbox domain, not just the
research lookup.
"""
import pytest

from app import pipeline
from app.models import CompanyState


def test_company_domain_prefers_the_operator_website():
    cs = CompanyState(slug="s", name="Fabrikam ID", website="https://www.fabrikam-id.test/",
                      recipient_domain="mail.other.com")
    assert pipeline.company_domain(cs) == "fabrikam-id.test"


def test_company_domain_falls_back_to_research():
    cs = CompanyState(slug="s", name="Fabrikam ID",
                      cache={"company": {"resolved_domain": "fabrikam-id.test"}})
    assert pipeline.company_domain(cs) == "fabrikam-id.test"


def test_company_domain_ignores_the_recipient_domain():
    cs = CompanyState(slug="s", name="Fabrikam ID", recipient_domain="gmail.com")
    assert pipeline.company_domain(cs) == ""


def test_editing_the_address_does_not_change_company_domain():
    cs = CompanyState(slug="s", name="Fabrikam ID", website="https://www.fabrikam-id.test/")
    before = pipeline.company_domain(cs)
    cs.recipient_domain = "founders.fabrikam.io"
    assert pipeline.company_domain(cs) == before


def test_mailbox_domain_is_forced_to_the_operator_website():
    cache = {"contact": {"email": "ashish.jha@nativ-global.com", "email_method": "pattern_guess"}}
    out = pipeline.pin_mailbox_domain(cache, "nativ.com")
    assert out["contact"]["email"] == "ashish.jha@nativ.com"
    assert out["contact"]["email_method"] == "repinned_to_company_domain"


def test_a_verified_address_is_never_repinned():
    cache = {"contact": {"email": "a.b@other.com", "email_method": "manual",
                         "contact_verified": True}}
    out = pipeline.pin_mailbox_domain(cache, "nativ.com")
    assert out["contact"]["email"] == "a.b@other.com"


def test_a_found_address_is_never_repinned():
    cache = {"contact": {"email": "a.b@other.com", "email_method": "found_on_page"}}
    out = pipeline.pin_mailbox_domain(cache, "nativ.com")
    assert out["contact"]["email"] == "a.b@other.com"


def test_pin_is_a_noop_without_an_anchor():
    cache = {"contact": {"email": "a.b@other.com", "email_method": "pattern_guess"}}
    assert pipeline.pin_mailbox_domain(cache, "")["contact"]["email"] == "a.b@other.com"


def test_domain_helpers_never_raise():
    assert pipeline.company_domain(None) == ""
    assert isinstance(pipeline.pin_mailbox_domain(None, "x.com"), dict)
