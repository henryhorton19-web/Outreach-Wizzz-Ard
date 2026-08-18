"""A preflight refusal must be recoverable by the operator (Plan 31, Stage 4).

Observed: Mankinds was correctly refused for having no named contact, and the only offered action was
"Retry with fresh research" -- which cannot find a contact research already failed to find, and costs
another call. The endpoint that sets an address 400s until a draft exists, and no draft exists.
"""
import pytest
from fastapi.testclient import TestClient

from app import store, preflight, settings as S
from app.models import CompanyState, State
from app.server import app


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    yield


@pytest.fixture
def client():
    c = TestClient(app)
    c.headers.update({"x-wizzard-token": S.SESSION_TOKEN})
    return c


def _blocked():
    cs = CompanyState(slug="exai", name="Mankinds", state=State.error,
                      error="not enough to write from: no named contact was found",
                      cache={"company": {"name": "Mankinds", "what_they_do": "stealth AI",
                                         "resolved_domain": "example-ai.test"},
                             "contact": {"name": "Unknown", "email": "unknown@example-ai.test",
                                         "email_method": "pattern_guess"},
                             "proof_points": [{"fact": "in stealth"}]})
    store.upsert_draft(cs)
    return cs


# ---- blockers are classified -----------------------------------------------

def test_blockers_are_classified_by_who_can_fix_them():
    cache = {"company": {"what_they_do": "stealth AI"},
             "contact": {"name": "Unknown", "email": "unknown@example-ai.test"},
             "proof_points": [{"fact": "in stealth"}]}
    kinds = {b["kind"] for b in preflight.blockers_detailed(cache)}
    assert kinds == {"needs_contact"}


def test_thin_research_is_classified_as_needing_research():
    cache = {"company": {"what_they_do": ""}, "contact": {"name": "Ada Lovelace",
             "email": "ada@x.com", "email_method": "found_on_page"}, "proof_points": []}
    kinds = {b["kind"] for b in preflight.blockers_detailed(cache)}
    assert kinds == {"needs_research"}


def test_the_two_contact_blockers_are_reported_once():
    cache = {"company": {"what_they_do": "stealth AI"},
             "contact": {"name": "Unknown", "email": "unknown@example-ai.test"},
             "proof_points": [{"fact": "in stealth"}]}
    assert len(preflight.blockers_detailed(cache)) == 1


def test_blockers_string_form_still_works():
    cache = {"company": {"what_they_do": ""}, "contact": {"name": "Unknown"}, "proof_points": []}
    out = preflight.blockers(cache)
    assert isinstance(out, list) and all(isinstance(x, str) for x in out)


def test_blockers_detailed_never_raise():
    for bad in (None, {}, {"contact": 3}):
        assert isinstance(preflight.blockers_detailed(bad), list)


# ---- the override endpoint -------------------------------------------------

def test_setting_a_contact_clears_the_block(client):
    _blocked()
    r = client.put("/api/companies/exai/contact",
                   json={"name": "Alan Turing", "email": "alan@example-ai.test"})
    assert r.status_code == 200
    cs = store.get_draft("exai")
    c = (cs.cache or {})["contact"]
    assert c["name"] == "Alan Turing"
    assert c["email"] == "alan@example-ai.test"
    assert c["email_method"] == "manual"
    assert c["contact_verified"] is True
    assert preflight.blockers(cs.cache) == []


def test_setting_a_contact_works_before_any_draft_exists(client):
    _blocked()
    assert store.get_draft("exai").machine_email is None
    assert client.put("/api/companies/exai/contact",
                      json={"name": "Alan Turing", "email": "alan@example-ai.test"}).status_code == 200


def test_a_name_only_override_is_accepted(client):
    _blocked()
    r = client.put("/api/companies/exai/contact", json={"name": "Alan Turing"})
    assert r.status_code == 200
    assert (store.get_draft("exai").cache or {})["contact"]["name"] == "Alan Turing"


def test_a_placeholder_name_is_rejected(client):
    _blocked()
    assert client.put("/api/companies/exai/contact",
                      json={"name": "Unknown"}).status_code == 400


def test_a_bad_address_is_rejected(client):
    _blocked()
    assert client.put("/api/companies/exai/contact",
                      json={"name": "Alan Turing", "email": "not-an-email"}).status_code == 400


def test_an_address_off_the_company_domain_is_accepted_but_flagged(client):
    _blocked()
    r = client.put("/api/companies/exai/contact",
                   json={"name": "Alan Turing", "email": "alan@gmail.com"})
    assert r.status_code == 200
    assert r.json().get("domain_mismatch") is True


def test_unknown_slug_is_404(client):
    assert client.put("/api/companies/nope/contact", json={"name": "A B"}).status_code == 404
