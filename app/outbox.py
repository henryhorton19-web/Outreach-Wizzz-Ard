"""Outbox storage and historical email sync.

Manages saving approved emails into standard .eml format in S.OUTBOX_DIR (default: %APPDATA%/OutreachWizzard/outbox),
and syncing historical approved emails from store.load_archive() / store.load_sent_items().
"""
from __future__ import annotations

import sys
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formatdate, make_msgid
from pathlib import Path

from . import settings as S
from . import store
from . import attachments as attach_mod


def clean_filename(name: str) -> str:
    cleaned = "".join(ch for ch in str(name) if ch.isalnum() or ch in " -_").strip().lower()
    return cleaned.replace(" ", "_") or "draft"


def build_eml(to: str, subject: str, body_text: str,
              message_id: str | None = None,
              date_str: str | None = None,
              attachments: list[Path] | None = None) -> tuple[bytes, str]:
    """Construct an X-Unsent .eml payload. Returns (eml_bytes, message_id)."""
    msg = EmailMessage(policy=SMTP)
    msg["To"] = to
    import os, config as C
    st = S.load_settings()
    cand_email = C.ProfileStore.load().get("email", "") if hasattr(C, "ProfileStore") else ""
    from_addr = (
        getattr(st, "from_email", "")
        or getattr(st, "imap_username", "")
        or os.environ.get("WIZZARD_SENDER_EMAIL")
        or os.environ.get("PARIS_SENDER_EMAIL")
        or cand_email
        or "me@example.com"
    )
    msg["From"] = from_addr
    msg["Subject"] = subject or "(No Subject)"
    msg["Date"] = formatdate(localtime=True)
    msg["X-Unsent"] = "1"  # tells Outlook / Mail.app to open in draft/compose mode

    mid = message_id or make_msgid(domain="outreach-wizzard.local")
    msg["Message-ID"] = mid
    msg.set_content(body_text or "")

    if attachments:
        for p in attachments:
            try:
                p = Path(p)
                if p.exists() and p.is_file():
                    data = p.read_bytes()
                    msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=p.name)
            except Exception as e:
                print(f"[outbox] Skipping unreadable attachment {p}: {e}", file=sys.stderr)

    return msg.as_bytes(), mid


def save_to_outbox(record_or_cs, sent_id: str | None = None, message_id: str | None = None) -> Path:
    """Save an approved email (CompanyState or dict from archive/sent_items) as a .eml file in S.get_outbox_dir()."""
    outbox_dir = S.get_outbox_dir()
    outbox_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(record_or_cs, dict):
        rec = record_or_cs
        slug = rec.get("slug") or "draft"
        name = rec.get("name") or slug
        contact = rec.get("contact") or {}
        to = rec.get("send_to") or contact.get("email") or ""
        subject = rec.get("subject") or ""
        body = rec.get("final_email") or rec.get("approved_body") or rec.get("machine_email") or ""
        sid = sent_id or rec.get("sent_id") or rec.get("id") or slug
        mid = message_id or rec.get("message_id") or None
        attach_names = rec.get("attachments") or []
    else:
        cs = record_or_cs
        slug = cs.slug or "draft"
        name = cs.name or slug
        cache = cs.cache or {}
        contact = cache.get("contact") or {}
        spec = cs.spec or {}
        to = spec.get("send_to") or contact.get("email") or ""
        subject = cs.subject or ""
        body = cs.final_email or cs.machine_email or ""
        sid = sent_id or getattr(cs, "sent_id", None) or slug
        mid = message_id or None
        attach_names = getattr(cs, "attachments", None) or []

    paths = attach_mod.resolve_paths(attach_names) if attach_names else []

    eml_bytes, mid = build_eml(to=to, subject=subject, body_text=body, message_id=mid, attachments=paths)

    cname = clean_filename(name)
    csid = clean_filename(sid)
    filename = f"{cname}_{csid}.eml"
    out_path = outbox_dir / filename

    out_path.write_bytes(eml_bytes)
    return out_path


def sync_historical_outbox() -> int:
    """Read all historical approved emails from archive and sent items, generating .eml files if missing."""
    outbox_dir = S.get_outbox_dir()
    outbox_dir.mkdir(parents=True, exist_ok=True)
    existing_files = {p.name for p in outbox_dir.glob("*.eml")}

    count = 0
    archive_items = store.load_archive()
    sent_items = store.load_sent_items()

    sent_by_id = {s.id: s for s in sent_items}
    sent_by_slug = {s.slug: s for s in sent_items}

    for rec in archive_items:
        slug = rec.get("slug") or ""
        name = rec.get("name") or slug
        sid = rec.get("sent_id") or (sent_by_slug.get(slug).id if sent_by_slug.get(slug) else slug)

        cname = clean_filename(name)
        csid = clean_filename(sid)
        filename = f"{cname}_{csid}.eml"

        if filename in existing_files:
            continue

        si = sent_by_id.get(sid) or sent_by_slug.get(slug)
        if si and not rec.get("final_email"):
            rec["final_email"] = si.approved_body
        if si and not rec.get("subject"):
            rec["subject"] = si.approved_subject
        if si and not rec.get("contact", {}).get("email"):
            rec.setdefault("contact", {})["email"] = si.sent_to

        save_to_outbox(rec, sent_id=sid, message_id=si.message_id if si else None)
        existing_files.add(filename)
        count += 1

    for si in sent_items:
        cname = clean_filename(si.name or si.slug)
        csid = clean_filename(si.id)
        filename = f"{cname}_{csid}.eml"

        if filename in existing_files:
            continue

        rec = {
            "slug": si.slug,
            "name": si.name,
            "send_to": si.sent_to,
            "subject": si.approved_subject or si.subject,
            "final_email": si.approved_body,
            "sent_id": si.id,
            "message_id": si.message_id,
        }
        save_to_outbox(rec, sent_id=si.id, message_id=si.message_id)
        existing_files.add(filename)
        count += 1

    return count
