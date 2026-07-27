"""API-key storage.

On Windows, `keyring` uses the Credential Locker (DPAPI-backed). We NEVER write keys to disk
ourselves, never log them, and never place them in the JSON store or audit records. If a
platform has no keyring backend (e.g. a headless CI box), we fall back to an in-memory dict
for the session only, so the app still runs but nothing is persisted.

The Apollo key is prompted lazily (first approval); the Anthropic/Gemini key is prompted at
startup depending on the selected provider.
"""
from __future__ import annotations

SERVICE = "ParisOutreach"

# providers we store keys for
KNOWN = ("gemini", "anthropic", "apollo", "imap")

_mem: dict[str, str] = {}          # session-only fallback when no OS keyring is available
_backend_ok = True

import os as _os

if _os.environ.get("PARIS_NO_KEYRING") == "1":
    # Explicit opt-out: skip the OS keychain entirely (e.g. to avoid a rare macOS
    # keychain-prompt hang). Keys then live only for the current process.
    keyring = None                 # type: ignore
    _backend_ok = False
else:
    try:  # keyring import must never crash the app
        import keyring
        from keyring.errors import NoKeyringError
        try:
            keyring.get_keyring()      # probe; some envs raise only on first use
        except Exception:
            _backend_ok = False
    except Exception:
        keyring = None                 # type: ignore
        _backend_ok = False


def _valid(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in KNOWN:
        raise ValueError(f"unknown provider '{provider}'")
    return p


def set_key(provider: str, key: str, remember: bool = True) -> None:
    """Store a key. remember=True persists via the OS credential store (if available);
    remember=False (or no backend) keeps it in memory for this session only."""
    p = _valid(provider)
    key = (key or "").strip()
    if not key:
        raise ValueError("empty key")
    if remember and _backend_ok and keyring is not None:
        try:
            keyring.set_password(SERVICE, p, key)
            _mem.pop(p, None)
            return
        except Exception:
            pass  # fall through to memory rather than fail
    _mem[p] = key


def get_key(provider: str) -> str | None:
    p = _valid(provider)
    if p in _mem:
        return _mem[p]
    if _backend_ok and keyring is not None:
        try:
            return keyring.get_password(SERVICE, p)
        except Exception:
            return None
    return None


def clear_key(provider: str) -> None:
    p = _valid(provider)
    _mem.pop(p, None)
    if _backend_ok and keyring is not None:
        try:
            keyring.delete_password(SERVICE, p)
        except Exception:
            pass


def has_key(provider: str) -> bool:
    return bool(get_key(provider))


def backend_available() -> bool:
    return _backend_ok
