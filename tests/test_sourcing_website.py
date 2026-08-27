"""verify_candidate() must never invent a website. A slug-derived '.com' guess with
no marker is indistinguishable from a real, harvester-supplied URL by the time it
reaches CompanyState.website — and EXECUTION_PLAN_4_CONTACT_DOMAIN.md's domain-pin
step trusts CompanyState.website unconditionally, precisely because it is assumed
to be real. This is the upstream half of the chandler@example-saas-alt.test / lauren@example-saas-alt.test
bug: the harvester found no real site for 'exsaas', so verify.py silently produced
'https://example-saas-alt.test' (wrong TLD) and downstream research trusted it as given.
"""
from app.sourcing.verify import verify_candidate


def test_no_website_produces_no_website_not_a_slug_guess():
    raw = {"name": "exsaas", "slug": "exsaas", "meta": {}}   # no meta["website"], no ref
    out = verify_candidate(raw)
    assert out["website"] == "", \
        f"a slug-derived guess leaked through as if it were real: {out['website']!r}"


def test_website_source_is_reported_when_present():
    raw = {"name": "exsaas", "slug": "exsaas", "meta": {"website": "https://example-saas.test"}}
    out = verify_candidate(raw)
    assert out["website"] == "https://example-saas.test"
    assert out.get("website_source") == "harvester", \
        "a real, harvester-supplied website must be marked as such"


def test_ref_is_accepted_but_marked_lower_confidence_than_meta_website():
    raw = {"name": "exsaas", "slug": "exsaas", "ref": "https://example-saas.test", "meta": {}}
    out = verify_candidate(raw)
    assert out["website"] == "https://example-saas.test"
    assert out.get("website_source") in ("harvester", "ref")
