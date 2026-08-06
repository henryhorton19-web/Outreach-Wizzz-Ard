"""Test suite for the permanent exclusion layer (app/sourcing/exclude.py and store/settings integration)."""
from app.sourcing.exclude import build_exclusion_set, exclusion_info
from app import store


def test_build_exclusion_set_parses_various_formats():
    data = [
        "acme_corp",
        {"slug": "beta_tech"},
        {"domain": "gamma.io"},
        "delta_software.com",
    ]
    result = build_exclusion_set(data)
    assert "acme_corp" in result
    assert "beta_tech" in result
    assert "gamma" in result
    assert "delta_software" in result


def test_exclusion_info_returns_summary():
    info = exclusion_info()
    assert isinstance(info, dict)
    assert "total" in info
    assert "enabled" in info
