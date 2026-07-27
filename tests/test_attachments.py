import pytest
import os
import email
from email.policy import SMTP
from pathlib import Path

from app import settings as S
from app import attachments as attach_mod
from app import apollo as apollo_mod
from app.models import CompanyState, State


@pytest.fixture
def clean_data_dir(tmp_path, monkeypatch):
    # Set PARIS_DATA_DIR to a temporary directory for each test
    monkeypatch.setenv("PARIS_DATA_DIR", str(tmp_path))
    # Re-initialize path constants under settings
    S.DATA_DIR = tmp_path
    S.CACHE_DIR = tmp_path / "caches"
    S.BATCH_DIR = tmp_path / "batches"
    S.AUDIT_DIR = tmp_path / "audit"
    S.VOICES_DIR = tmp_path / "voices"
    S.ATTACH_DIR = tmp_path / "attachments"
    S.SETTINGS_FILE = tmp_path / "settings.json"
    
    # Ensure they exist
    for d in (S.DATA_DIR, S.CACHE_DIR, S.BATCH_DIR, S.AUDIT_DIR, S.VOICES_DIR, S.ATTACH_DIR):
        d.mkdir(parents=True, exist_ok=True)
    yield tmp_path


def test_save_upload_happy_path(clean_data_dir):
    data = b"%PDF-1.4 ... fake pdf data ..."
    name = attach_mod.save_upload(data, "my_cv.pdf")
    assert name == "my_cv.pdf"
    assert (S.ATTACH_DIR / name).is_file()
    assert (S.ATTACH_DIR / name).read_bytes() == data


def test_save_upload_unsupported_ext(clean_data_dir):
    with pytest.raises(attach_mod.AttachmentError, match="unsupported file type"):
        attach_mod.save_upload(b"some data", "hack.exe")


def test_save_upload_oversized(clean_data_dir):
    data = b"x" * (attach_mod.MAX_BYTES + 1)
    with pytest.raises(attach_mod.AttachmentError, match="file too large"):
        attach_mod.save_upload(data, "big.pdf")


def test_save_upload_empty(clean_data_dir):
    with pytest.raises(attach_mod.AttachmentError, match="empty file"):
        attach_mod.save_upload(b"", "empty.pdf")


def test_attachments_round_trip(clean_data_dir):
    data = b"test docx contents"
    name = attach_mod.save_upload(data, "Cover_Letter.docx")
    assert name == "Cover_Letter.docx"
    
    lst = attach_mod.list_attachments()
    assert len(lst) == 1
    assert lst[0]["name"] == "Cover_Letter.docx"
    assert lst[0]["size"] == len(data)
    
    paths = attach_mod.resolve_paths([name])
    assert len(paths) == 1
    assert paths[0] == S.ATTACH_DIR / name
    
    deleted = attach_mod.delete_attachment(name)
    assert deleted is True
    assert not (S.ATTACH_DIR / name).exists()
    assert len(attach_mod.list_attachments()) == 0


def test_attachments_traversal_guards(clean_data_dir):
    # Try resolving a traversal path
    assert attach_mod.resolve_paths(["../settings.json"]) == []
    assert attach_mod.resolve_paths(["/etc/passwd"]) == []
    assert attach_mod.resolve_paths(["\\\\windows\\system32"]) == []
    
    # Try deleting traversal path
    assert attach_mod.delete_attachment("../settings.json") is False


def test_guess_mime(clean_data_dir):
    assert attach_mod.guess_mime(Path("test.pdf")) == ("application", "pdf")
    assert attach_mod.guess_mime(Path("test.docx")) == (
        "application", "vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert attach_mod.guess_mime(Path("test.png")) == ("image", "png")
    assert attach_mod.guess_mime(Path("test.txt")) == ("text", "plain")
    assert attach_mod.guess_mime(Path("test.invalidext")) == ("application", "octet-stream")


def test_build_eml_no_attachments(clean_data_dir):
    eml_bytes, mid = apollo_mod._build_eml("target@example.com", "Subject Line", "Email body text")
    assert mid and mid.startswith("<") and mid.endswith(">")
    msg = email.message_from_bytes(eml_bytes, policy=SMTP)
    assert msg["To"] == "target@example.com"
    assert msg["Subject"] == "Subject Line"
    assert msg["X-Unsent"] == "1"
    assert msg["Message-ID"] == mid
    assert msg.get_content_type() == "multipart/alternative"


def test_build_eml_message_id_reused(clean_data_dir):
    # A caller may pass a pre-chosen Message-ID (bounce re-target keeps thread identity).
    _b1, mid1 = apollo_mod._build_eml("a@b.com", "S", "body")
    _b2, mid2 = apollo_mod._build_eml("a@b.com", "S", "body", message_id=mid1)
    assert mid1 == mid2


def test_build_eml_with_attachments(clean_data_dir):
    pdf_path = S.ATTACH_DIR / "resume.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake resume content")
    
    eml_bytes, _mid = apollo_mod._build_eml(
        "target@example.com", "Subject Line", "Email body text", attachments=[pdf_path]
    )
    msg = email.message_from_bytes(eml_bytes, policy=SMTP)
    assert msg["To"] == "target@example.com"
    assert msg["Subject"] == "Subject Line"
    assert msg["X-Unsent"] == "1"
    assert msg.get_content_type() == "multipart/mixed"
    
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    att = attachments[0]
    assert att.get_filename() == "resume.pdf"
    assert att.get_content_type() == "application/pdf"
    assert att.get_content() == b"%PDF-1.4 fake resume content"


def test_apollo_verify_wiring(clean_data_dir, monkeypatch):
    # Save a default settings attachment
    st = S.load_settings()
    st.attach_by_default = True
    
    # Create the default file on disk
    pdf_name = attach_mod.save_upload(b"%PDF-1.4 fake pdf", "default_resume.pdf")
    st.default_attachments = [pdf_name]
    S.save_settings(st)
    
    recorded_args = {}
    
    def mock_open_email_draft(to, subject, body_text, attachments=None, message_id=None, company_name=None, **kwargs):
        recorded_args["to"] = to
        recorded_args["subject"] = subject
        recorded_args["body_text"] = body_text
        recorded_args["attachments"] = attachments
        return "/dummy/path/draft.eml", "<test-message-id@local>"
        
    monkeypatch.setattr(apollo_mod, "open_email_draft", mock_open_email_draft)
    
    # Create minimal CompanyState row
    cs = CompanyState(
        slug="test-co",
        name="Test Co",
        website="https://test.co",
        state=State.drafted,
        subject="Hello Test",
        final_email="This is the final email",
        cache={
            "contact": {
                "name": "Jane Doe",
                "email": "jane@test.co",
                "contact_verified": True
            }
        }
    )
    
    receipt = apollo_mod.apollo_verify([cs], "role_small")
    assert receipt["opened"] == 1
    assert recorded_args["to"] == "jane@test.co"
    assert recorded_args["subject"] == "Hello Test"
    assert recorded_args["body_text"] == "This is the final email"
    assert len(recorded_args["attachments"]) == 1
    assert recorded_args["attachments"][0] == S.ATTACH_DIR / "default_resume.pdf"
    
    # Test Phase 2 override: override with another attachment
    other_name = attach_mod.save_upload(b"%PDF-1.4 other pdf", "other_portfolio.pdf")
    cs.attachments = [other_name]
    
    recorded_args.clear()
    receipt = apollo_mod.apollo_verify([cs], "role_small")
    assert receipt["opened"] == 1
    assert len(recorded_args["attachments"]) == 1
    assert recorded_args["attachments"][0] == S.ATTACH_DIR / "other_portfolio.pdf"
