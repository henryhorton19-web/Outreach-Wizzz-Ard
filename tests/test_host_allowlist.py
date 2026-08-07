"""The Host allowlist must accept every loopback form, on any port.

Reported as `bad host` on Pipeline / Follow-ups / Performance / Triage after
staging a large batch. Two causes:

  1. IPv6 loopback ([::1]) was not in the allowlist at all. The embedded webview
     can present it for a loopback connection depending on platform, so the
     failure is environment-dependent rather than every launch.
  2. The allowlist was rebuilt per-request from S.load_settings().port, and
     load_settings() swallows read failures and falls back to defaults -- so a
     transient failure rebuilt the list around port 8770 regardless of the port
     actually in use, 400-ing everything until the next successful read.

Binding a loopback check to a port number never provided the safety; rejecting
non-loopback hostnames does.
"""
import pytest

from app import settings as S


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from app.server import app
    return TestClient(app)


def _get(client, host):
    return client.get("/api/status",
                      headers={"x-wizzard-token": S.SESSION_TOKEN, "Host": host})


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.0.0.1:8770", "127.0.0.1:8775", "127.0.0.1:59123",
    "localhost", "localhost:8775",
    "[::1]", "[::1]:8775",
    "0.0.0.0:8775",
])
def test_every_loopback_form_is_accepted(client, host):
    r = _get(client, host)
    assert r.status_code != 400, f"{host} was rejected as a bad host"


@pytest.mark.parametrize("host", [
    "evil.com", "evil.com:8775", "127.0.0.1.evil.com", "notlocalhost",
])
def test_non_loopback_hosts_are_still_rejected(client, host):
    r = _get(client, host)
    assert r.status_code == 400, f"{host} should have been rejected"


def test_the_check_does_not_depend_on_the_settings_port(client, monkeypatch):
    """A port mismatch in settings must not produce 'bad host'."""
    import dataclasses
    orig = S.load_settings()
    monkeypatch.setattr(S, "load_settings", lambda: dataclasses.replace(orig, port=9999))
    r = _get(client, "127.0.0.1:59123")
    assert r.status_code != 400, \
        "a port mismatch produced 'bad host' -- the check still depends on the port"
