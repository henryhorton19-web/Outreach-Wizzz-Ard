"""Test that the exclusion toggle gates ingest correctly and approve writes unconditionally."""
import json
import pytest
from app import store, settings as S


def test_exclusion_toggle_gates_ingest(monkeypatch, tmp_path):
    """When exclusion_enabled=True, a slug already in excluded.json must be rejected."""
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "EXCLUDED_FILE", tmp_path / "excluded.json")
    monkeypatch.setattr(store, "DRAFTS_FILE", tmp_path / "drafts.json")
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")

    (tmp_path / "excluded.json").write_text(json.dumps(["acme"]), encoding="utf-8")

    from app.server import _exclusion_blocked
    result = _exclusion_blocked("acme")
    assert result is True, "toggle-gated ingest should block an excluded slug"


def test_exclusion_toggle_off_allows_ingest(monkeypatch, tmp_path):
    """When exclusion_enabled=False, excluded slugs still pass the ingest gate."""
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "EXCLUDED_FILE", tmp_path / "excluded.json")

    (tmp_path / "excluded.json").write_text(json.dumps(["acme"]), encoding="utf-8")

    st = S.Settings()
    st.exclusion_enabled = False
    monkeypatch.setattr(S, "load_settings", lambda: st)

    from app.server import _exclusion_blocked
    result = _exclusion_blocked("acme")
    assert result is False, "toggle-off should allow an excluded slug through ingest"


def test_add_to_exclusion_set_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "EXCLUDED_FILE", tmp_path / "excluded.json")
    store.add_to_exclusion_set("newco")
    slugs = store.excluded_slugs()
    assert "newco" in slugs
