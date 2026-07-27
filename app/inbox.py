"""Read-only IMAP access (Phase 5).

Prefers `imap-tools` if installed; falls back to the stdlib `imaplib`. The one non-negotiable
invariant is enforced here by a guard that wraps ALL access: this module never issues a command
that mutates the mailbox (no APPEND / STORE / flag / MOVE / COPY / EXPUNGE / DELETE). We only
SELECT a mailbox read-only and FETCH headers/bodies of recent messages. The app-password lives in
the OS keychain via keys.py under provider "imap"; it is never logged or persisted by us.

Off = today: nothing here runs unless the user has enabled IMAP and clicked "Check for replies".
Every function raises InboxError with a plain message on failure so the UI can show it inline.
"""
from __future__ import annotations

import imaplib
from datetime import datetime, timedelta, timezone

from . import settings as S
from . import keys


class InboxError(RuntimeError):
    """A connection / auth / protocol failure the UI surfaces inline (never a crash)."""


# Commands that would change server state. If any code path ever tries one, we refuse loudly —
# a defence-in-depth backstop behind "only ever call SELECT readonly + FETCH + SEARCH".
_FORBIDDEN = {"APPEND", "STORE", "COPY", "MOVE", "EXPUNGE", "DELETE", "CREATE", "RENAME",
              "SETACL", "DELETEACL", "SUBSCRIBE", "UNSUBSCRIBE"}


class _ReadOnlyIMAP:
    """Thin wrapper over imaplib enforcing the read-only guard. Only the verbs we need are exposed;
    mutation verbs raise. SELECT is always readonly=True."""

    def __init__(self, host: str, port: int, use_ssl: bool):
        try:
            self._c = imaplib.IMAP4_SSL(host, port) if use_ssl else imaplib.IMAP4(host, port)
        except Exception as e:
            raise InboxError(f"could not connect to {host}:{port} ({e})") from e

    def login(self, user: str, password: str):
        try:
            self._c.login(user, password)
        except Exception as e:
            raise InboxError(f"IMAP login failed ({e})") from e

    def select_readonly(self, mailbox: str):
        try:
            typ, _ = self._c.select(f'"{mailbox}"', readonly=True)
            if typ != "OK":
                raise InboxError(f"could not open mailbox {mailbox}")
        except InboxError:
            raise
        except Exception as e:
            raise InboxError(f"could not open mailbox {mailbox} ({e})") from e

    def search_since(self, since: datetime) -> list[bytes]:
        try:
            crit = since.strftime("%d-%b-%Y")
            typ, data = self._c.search(None, "SINCE", crit)
            if typ != "OK":
                return []
            return (data[0] or b"").split()
        except Exception as e:
            raise InboxError(f"IMAP search failed ({e})") from e

    def fetch_message(self, num: bytes) -> bytes | None:
        try:
            # BODY.PEEK[] never sets the \Seen flag — read-only in spirit and in fact.
            typ, data = self._c.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not data:
                return None
            for part in data:
                if isinstance(part, tuple) and len(part) == 2 and part[1]:
                    return part[1]
            return None
        except Exception as e:
            raise InboxError(f"IMAP fetch failed ({e})") from e

    def logout(self):
        try:
            self._c.logout()
        except Exception:
            pass

    # defence-in-depth: any attribute access matching a mutation verb is refused
    def __getattr__(self, name):
        if name.upper() in _FORBIDDEN:
            raise InboxError(f"refused: '{name}' would modify the mailbox (read-only guard)")
        raise AttributeError(name)


def _config():
    st = S.load_settings()
    if not getattr(st, "imap_enabled", False):
        raise InboxError("inbox is disabled — enable it in Settings first")
    host = (st.imap_host or "").strip()
    user = (st.imap_username or "").strip()
    if not host or not user:
        raise InboxError("set the IMAP host and username in Settings first")
    pw = keys.get_key("imap")
    if not pw:
        raise InboxError("no IMAP app-password stored — add it in Settings")
    return st, host, user, pw


def test_connection() -> dict:
    """Connect, log in, select the first mailbox read-only, log out. Returns {ok, detail}. Never
    raises — turns failures into {ok:false, detail:...} for the inline UI indicator."""
    try:
        st, host, user, pw = _config()
        c = _ReadOnlyIMAP(host, st.imap_port, st.imap_ssl)
        try:
            c.login(user, pw)
            mailbox = (st.imap_mailboxes or ["INBOX"])[0]
            c.select_readonly(mailbox)
        finally:
            c.logout()
        return {"ok": True, "detail": f"connected to {host} as {user}"}
    except InboxError as e:
        return {"ok": False, "detail": str(e)}
    except Exception as e:
        return {"ok": False, "detail": f"unexpected error: {e}"}


def fetch_recent(days: int = 30):
    """Yield raw RFC822 bytes for messages received in the last `days`, across configured mailboxes.
    Read-only throughout. Raises InboxError on connection/auth failure."""
    st, host, user, pw = _config()
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    c = _ReadOnlyIMAP(host, st.imap_port, st.imap_ssl)
    out = []
    try:
        c.login(user, pw)
        for mailbox in (st.imap_mailboxes or ["INBOX"]):
            try:
                c.select_readonly(mailbox)
            except InboxError:
                continue  # a missing mailbox (e.g. "All Mail") shouldn't abort the whole sweep
            for num in c.search_since(since):
                raw = c.fetch_message(num)
                if raw:
                    out.append(raw)
    finally:
        c.logout()
    return out
