"""Pure reply/bounce detection (Phase 5).

No IMAP, no I/O — takes parsed email.message.Message objects (or raw bytes) and the set of
outstanding SentItems, and decides: is this a reply to one of our sends? a hard bounce? or neither
(an auto-reply, a soft bounce, unrelated mail)? Kept pure so it is exhaustively testable against
canned RFC822 fixtures with no live server.

Matching rules (deliberately conservative — false negatives are safe, false positives pollute stats):
  * REPLY: the message's In-Reply-To or References header contains a SentItem's Message-ID. Never
    match on "Subject: Re:" alone (spoofable / thread-drift). When a Message-ID appears in more than
    one SentItem (the RFC does not guarantee global uniqueness across our own re-targets), the
    sender/domain of the reply breaks the tie.
  * BOUNCE: a Delivery Status Notification — content-type multipart/report; report-type=delivery-
    status, OR from a mailer-daemon / postmaster, with a hard 5.x.x status or Action: failed. Soft
    bounces (4.x.x) stay 'awaiting'. Auto-replies (Auto-Submitted / vacation) are ignored.
"""
from __future__ import annotations

import email
import re
from email.message import Message


AUTO_SUBMITTED_RE = re.compile(r"auto-(replied|generated|notified)", re.I)
HARD_STATUS_RE = re.compile(r"\b5\.\d{1,3}\.\d{1,3}\b")
SOFT_STATUS_RE = re.compile(r"\b4\.\d{1,3}\.\d{1,3}\b")
MSGID_RE = re.compile(r"<[^<>]+>")


def parse(raw: bytes | Message) -> Message:
    if isinstance(raw, Message):
        return raw
    return email.message_from_bytes(raw)


def _msgids_in(value: str) -> list[str]:
    return MSGID_RE.findall(value or "")


def is_auto_reply(msg: Message) -> bool:
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    precedence = (msg.get("Precedence") or "").strip().lower()
    # 'auto_reply' precedence; keep 'bulk'/'list' out of scope (not our replies anyway)
    if precedence in ("auto_reply",):
        return True
    subj = (msg.get("Subject") or "").lower()
    if AUTO_SUBMITTED_RE.search(subj) or "out of office" in subj or "automatic reply" in subj:
        return True
    return False


def reply_target(msg: Message, sent_index: dict[str, list]) -> str | None:
    """If this message is a reply to one of our sends, return the matching SentItem id, else None.
    `sent_index` maps a Message-ID -> list of SentItem-like objects (dicts with 'id','sent_to').
    """
    refs = _msgids_in(msg.get("In-Reply-To", "")) + _msgids_in(msg.get("References", ""))
    if not refs:
        return None
    from_addr = (email.utils.parseaddr(msg.get("From", ""))[1] or "").lower()
    from_domain = from_addr.split("@", 1)[1] if "@" in from_addr else ""
    for mid in refs:
        candidates = sent_index.get(mid)
        if not candidates:
            continue
        if len(candidates) == 1:
            return candidates[0]["id"]
        # tie-break by sender / domain of the reply matching where we sent
        for c in candidates:
            to = (c.get("sent_to") or "").lower()
            if to and (to == from_addr or (from_domain and to.endswith("@" + from_domain))):
                return c["id"]
        return candidates[0]["id"]     # last resort: first (still one of our sends)
    return None


def _walk_text(msg: Message) -> str:
    """Concatenate the text/* and message/delivery-status parts for status-code scanning."""
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype in ("text/plain", "message/delivery-status", "message/feedback-report") \
                    or ctype.startswith("text/"):
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        chunks.append(payload.decode("utf-8", "replace"))
                    else:
                        p = part.get_payload()
                        if isinstance(p, str):
                            chunks.append(p)
                except Exception:
                    continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                chunks.append(payload.decode("utf-8", "replace"))
        except Exception:
            pass
    # delivery-status sub-parts sometimes need header-form scanning too
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.get_content_type() == "message/delivery-status":
            for line in str(part).splitlines():
                chunks.append(line)
    return "\n".join(chunks)


def bounce_kind(msg: Message) -> str:
    """Return 'hard' (a hard/permanent bounce), 'soft' (transient — leave awaiting), or '' (not a
    bounce). Conservative: only clear DSNs with a 5.x.x status or Action: failed count as hard."""
    from_addr = (email.utils.parseaddr(msg.get("From", ""))[1] or "").lower()
    ctype = (msg.get_content_type() or "").lower()
    params = {k.lower(): v.lower() for k, v in (msg.get_params() or []) if isinstance(v, str)}
    is_dsn = ("report-type" in params and "delivery-status" in params.get("report-type", "")) \
        or ctype == "multipart/report"
    looks_daemon = any(tok in from_addr for tok in ("mailer-daemon", "postmaster", "mail-daemon"))
    if not (is_dsn or looks_daemon):
        return ""
    body = _walk_text(msg)
    action_failed = re.search(r"action:\s*failed", body, re.I) is not None
    if HARD_STATUS_RE.search(body) or action_failed:
        # a soft code also present shouldn't downgrade an explicit hard failure
        return "hard"
    if SOFT_STATUS_RE.search(body):
        return "soft"
    # a daemon message with no parseable status: treat as soft (don't poison stats on ambiguity)
    return "soft"


def failed_recipient(msg: Message) -> str:
    """Best-effort extraction of the address that bounced, from the DSN Final-Recipient. '' if none."""
    body = _walk_text(msg)
    m = re.search(r"final-recipient:\s*[^;]+;\s*([^\s]+)", body, re.I)
    if m:
        return m.group(1).strip().strip("<>").lower()
    m = re.search(r"original-recipient:\s*[^;]+;\s*([^\s]+)", body, re.I)
    if m:
        return m.group(1).strip().strip("<>").lower()
    return ""


def classify(msg: Message, sent_index: dict[str, list]) -> dict:
    """One message -> {'kind': 'reply'|'bounce'|'ignore', 'sent_id': id|None, 'bounce': 'hard'|'soft'|'',
    'failed_recipient': addr}. This is the single entry point the sweep calls per message."""
    msg = parse(msg)
    if is_auto_reply(msg):
        return {"kind": "ignore", "sent_id": None, "bounce": "", "failed_recipient": ""}
    bk = bounce_kind(msg)
    if bk == "hard":
        return {"kind": "bounce", "sent_id": None, "bounce": "hard",
                "failed_recipient": failed_recipient(msg)}
    if bk == "soft":
        return {"kind": "ignore", "sent_id": None, "bounce": "soft",
                "failed_recipient": failed_recipient(msg)}
    sid = reply_target(msg, sent_index)
    if sid:
        return {"kind": "reply", "sent_id": sid, "bounce": "", "failed_recipient": ""}
    return {"kind": "ignore", "sent_id": None, "bounce": "", "failed_recipient": ""}


def build_sent_index(sent_items) -> dict[str, list]:
    """Group SentItems by Message-ID for the sweep. Accepts SentItem models or dicts."""
    idx: dict[str, list] = {}
    for si in sent_items:
        mid = getattr(si, "message_id", None) if not isinstance(si, dict) else si.get("message_id")
        rs = getattr(si, "reply_state", None) if not isinstance(si, dict) else si.get("reply_state")
        rs = getattr(rs, "value", rs)
        if not mid or rs != "awaiting":
            continue
        sid = getattr(si, "id", None) if not isinstance(si, dict) else si.get("id")
        to = getattr(si, "sent_to", "") if not isinstance(si, dict) else si.get("sent_to", "")
        idx.setdefault(mid, []).append({"id": sid, "sent_to": to})
    return idx
