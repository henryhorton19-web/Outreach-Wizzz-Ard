"""Tests for outbox storage and historical email sync."""
import json
from pathlib import Path
from email import message_from_bytes

from app import settings as S
from app import store
from app import outbox
from app.models import CompanyState, State


def test_build_eml():
    eml_bytes, mid = outbox.build_eml(
        to="founder@example.com",
        subject="Hello World",
        body_text="This is a test email.",
        message_id="<test1234@paris-outreach.local>"
    )
    assert eml_bytes is not None
    assert mid == "<test1234@paris-outreach.local>"

    parsed = message_from_bytes(eml_bytes)
    assert parsed["To"] == "founder@example.com"
    assert parsed["Subject"] == "Hello World"
    assert parsed["X-Unsent"] == "1"
    assert parsed["Message-ID"] == "<test1234@paris-outreach.local>"
    assert "This is a test email." in parsed.get_payload()


def test_save_to_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "OUTBOX_DIR", tmp_path / "outbox")

    cs = CompanyState(
        slug="test-company",
        name="Test Company",
        state=State.edited,
        subject="Test Subject",
        final_email="Hi team,\n\nTest body.",
        spec={"send_to": "ceo@testcompany.com"}
    )

    out_file = outbox.save_to_outbox(cs, sent_id="test-company#0")
    assert out_file.exists()
    assert out_file.parent == tmp_path / "outbox"
    assert "test_company" in out_file.name

    content = out_file.read_bytes()
    msg = message_from_bytes(content)
    assert msg["To"] == "ceo@testcompany.com"
    assert msg["Subject"] == "Test Subject"
    assert "Hi team," in msg.get_payload()


def test_sync_historical_outbox(tmp_path, monkeypatch):
    outbox_dir = tmp_path / "outbox"
    monkeypatch.setattr(S, "OUTBOX_DIR", outbox_dir)

    # Set up mock archive records
    archive_data = [
        {
            "slug": "alpha-corp",
            "name": "Alpha Corp",
            "sent_id": "alpha-corp#0",
            "subject": "Alpha Subject",
            "contact": {"email": "contact@alphacorp.com"},
            "final_email": "Body text for Alpha Corp."
        },
        {
            "slug": "beta-inc",
            "name": "Beta Inc",
            "sent_id": "beta-inc#0",
            "subject": "Beta Subject",
            "contact": {"email": "hello@betainc.com"},
            "final_email": "Body text for Beta Inc."
        }
    ]
    monkeypatch.setattr(store, "load_archive", lambda: archive_data)
    monkeypatch.setattr(store, "load_sent_items", lambda: [])

    synced_count = outbox.sync_historical_outbox()
    assert synced_count == 2

    files = list(outbox_dir.glob("*.eml"))
    assert len(files) == 2

    file_names = [f.name for f in files]
    assert any("alpha_corp" in fn for fn in file_names)
    assert any("beta_inc" in fn for fn in file_names)

    # Calling sync again should skip existing files and return 0
    resync_count = outbox.sync_historical_outbox()
    assert resync_count == 0
