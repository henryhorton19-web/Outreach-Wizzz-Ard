"""Portability guard: staged .eml drafts must resolve to a cross-platform, per-user location on any
machine (macOS / Windows / Linux) — never a hardcoded absolute path. Regression cover for the
previously hardcoded Windows OneDrive outbox path.
"""
import re
from pathlib import Path

import pytest

from app import apollo
from app import settings as S


def test_no_hardcoded_absolute_path_in_source():
    src = Path(apollo.__file__).read_text(encoding="utf-8")
    assert "HenryHorton" not in src and "OneDrive" not in src
    assert not re.search(r'Path\(\s*r?["\']([A-Za-z]:\\\\|/Users/)', src)


def test_eml_dir_defaults_under_data_dir(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    monkeypatch.setattr(S, "OUTBOX_DIR", outbox)
    st = S.load_settings(); st.eml_dir = ""
    monkeypatch.setattr(S, "load_settings", lambda: st)
    d = apollo._eml_dir()
    assert d == outbox and d.is_dir()


def test_eml_dir_override_and_fallback(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    monkeypatch.setattr(S, "OUTBOX_DIR", outbox)
    st = S.load_settings()
    monkeypatch.setattr(S, "load_settings", lambda: st)

    # a valid override is honored
    custom = tmp_path / "MyDrafts"
    st.eml_dir = str(custom)
    assert apollo._eml_dir() == custom and custom.is_dir()

    # an unwritable override (parent is a file) falls back to the portable default — never crashes
    afile = tmp_path / "afile"; afile.write_text("x")
    st.eml_dir = str(afile / "cannot")
    assert apollo._eml_dir() == outbox


def test_open_draft_writes_into_the_portable_outbox(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox"
    monkeypatch.setattr(S, "OUTBOX_DIR", outbox)
    st = S.load_settings(); st.eml_dir = ""
    monkeypatch.setattr(S, "load_settings", lambda: st)
    # opening will fail on a headless box, but the .eml is written first and the error carries its
    # path — assert it lives under the portable outbox, not a hardcoded location.
    try:
        path, _mid = apollo.open_email_draft(to="jane@acme.com", subject="Hi", body_text="Hello")
    except apollo.OutlookError as e:
        path = e.path
    assert path and Path(path).parent == outbox and Path(path).exists()
