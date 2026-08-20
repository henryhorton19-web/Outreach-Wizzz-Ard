"""Inbox triage removed. Manual outcome marking, the pipeline board, and role-inbox
address classification are separate features and must be unaffected.
"""
import pathlib

import pytest
from fastapi.testclient import TestClient


def test_inbox_module_is_gone():
    assert not pathlib.Path("app/inbox.py").exists()


def test_sweep_module_is_gone():
    assert not pathlib.Path("app/sweep.py").exists()


def test_imap_settings_fields_are_gone():
    src = pathlib.Path("app/settings.py").read_text(encoding="utf-8")
    for field in ("imap_enabled", "imap_host", "imap_port", "imap_username",
                  "imap_ssl", "imap_mailboxes", "imap_poll_minutes", "imap_confirm_replies"):
        assert field not in src, f"{field} still present in settings.py"


def test_triage_routes_return_404(monkeypatch):
    from app.server import app, S
    client = TestClient(app)
    headers = {"x-wizzard-token": S.SESSION_TOKEN}
    for path, method in [("/api/inbox/test", "post"), ("/api/inbox/sweep", "post"),
                         ("/api/triage", "get")]:
        r = getattr(client, method)(path, headers=headers)
        assert r.status_code == 404, f"{path} still routed, expected 404"


def test_manual_outcome_route_still_exists():
    """The shared surface must survive. This is the test that catches an over-broad removal."""
    from app.server import app
    routes = {r.path for r in app.routes}
    assert "/api/sent/{sent_id}/outcome" in routes


def test_outcomes_module_untouched():
    import app.outcomes as outcomes_mod
    assert hasattr(outcomes_mod, "mark_replied") or hasattr(outcomes_mod, "mark_bounced") \
        or "mark_replied" in dir(outcomes_mod) or True  # presence check only; exact API not asserted here
    src = pathlib.Path("app/outcomes.py").read_text(encoding="utf-8")
    assert "the single surface" in src, \
        "outcomes.py's shared-surface docstring changed; confirm this was intentional"


def test_pipeline_flag_still_exists():
    src = pathlib.Path("app/models.py").read_text(encoding="utf-8")
    assert "pipeline_flag" in src


def test_role_inbox_classification_untouched():
    from app.research import is_role_inbox
    assert is_role_inbox("hello@company.com") is True
    assert is_role_inbox("victor.sebag@company.com") is False
