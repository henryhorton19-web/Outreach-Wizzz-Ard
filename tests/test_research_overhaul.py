from app.research import _completeness_gaps, _post_process
from app.validate import research_capped

def test_completeness_gaps_full():
    cache = {
        "company": {
            "company_size": "small",
            "company_size_evidence": "Found on LinkedIn",
            "role_exists": True,
            "role_source": "https://example.com/job"
        },
        "contact": {
            "name": "Alex"
        },
        "proof_points": [
            {"fact": "Something", "source": "https://example.com/a"}
        ]
    }
    assert _completeness_gaps(cache) == []

def test_completeness_gaps_blank_evidence():
    cache = {
        "company": {
            "company_size": "small",
            "company_size_evidence": "",
            "role_exists": False
        },
        "situation_read": "Scaling",
        "proof_points": [
            {"fact": "Something", "source": "https://example.com/a"}
        ]
    }
    gaps = _completeness_gaps(cache)
    assert "company_size_evidence" in gaps

def test_completeness_gaps_role_true_no_source():
    cache = {
        "company": {
            "company_size": "large",
            "company_size_evidence": "Lots of people",
            "role_exists": True,
            "role_source": ""
        },
        "contact": {"name": "Alex"},
        "proof_points": [
            {"fact": "Something", "source": "https://example.com/a"}
        ]
    }
    gaps = _completeness_gaps(cache)
    assert "role_source (a role was claimed but not sourced)" in gaps

def test_completeness_gaps_no_contact_or_read():
    cache = {
        "company": {
            "company_size": "large",
            "company_size_evidence": "Lots of people",
            "role_exists": False
        },
        "contact": {"name": ""},
        "situation_read": "",
        "proof_points": [
            {"fact": "Something", "source": "https://example.com/a"}
        ]
    }
    gaps = _completeness_gaps(cache)
    assert "contact.name or situation_read" in gaps

def test_post_process_sorts_staleness():
    cache = {
        "proof_points": [
            {"fact": "Old fact", "staleness": "stale"},
            "bare string fact",
            {"fact": "New fact", "staleness": "fresh"}
        ]
    }
    res = _post_process(cache, "Acme", "https://acme.com", [])
    pts = res["proof_points"]
    assert len(pts) == 3
    assert pts[0].get("staleness") == "fresh"
    assert pts[1].get("staleness") == "stale"
    assert pts[2].get("fact") == "bare string fact"

def test_post_process_display_name():
    cache = {"company": {"name": "acme corp"}}
    res = _post_process(cache, "acme", "https://acme.com", [])
    assert res["company"]["name"] == "Acme Corp"
    
    cache2 = {"company": {"name": "Adyen"}}
    res2 = _post_process(cache2, "stripe", "https://stripe.com", [])
    assert res2["company"]["name"] == "Stripe"

def test_research_capped():
    assert research_capped({"research_failures": ["Research incomplete: could not confirm..."]}) is True
    assert research_capped({"research_failures": ["Stopped early..."]}) is True
    assert research_capped({"research_failures": []}) is False
    assert research_capped({}) is False
