"""Managed attachment store: files the user attaches to staged drafts (e.g. a CV).

Files are COPIED into DATA_DIR/attachments and referenced by managed filename, never by the
user's original path (which may move or vanish, especially in a bundled build). Mirrors the
app's 'own your state under the data dir' pattern.
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from . import settings as S

ALLOWED_EXT = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
MAX_BYTES = 15 * 1024 * 1024  # 15 MB

# mimetypes is patchy for Office formats across platforms; pin the ones we care about.
_MIME_FALLBACK = {
    ".pdf": ("application", "pdf"),
    ".doc": ("application", "msword"),
    ".docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".png": ("image", "png"),
    ".jpg": ("image", "jpeg"),
    ".jpeg": ("image", "jpeg"),
}


class AttachmentError(ValueError):
    """Raised when an upload is rejected (bad type, too large, empty)."""


def _dir() -> Path:
    d = S.ATTACH_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._-")
    return stem or "attachment"


def _unique(name: str) -> Path:
    d = _dir()
    p = d / name
    if not p.exists():
        return p
    stem, ext = Path(name).stem, Path(name).suffix
    i = 2
    while (d / f"{stem}-{i}{ext}").exists():
        i += 1
    return d / f"{stem}-{i}{ext}"


def save_upload(data: bytes, original_name: str) -> str:
    ext = Path(original_name or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise AttachmentError(
            f"unsupported file type '{ext or '?'}' (allowed: {', '.join(sorted(ALLOWED_EXT))})")
    if not data:
        raise AttachmentError("empty file")
    if len(data) > MAX_BYTES:
        raise AttachmentError(
            f"file too large ({len(data) // (1024 * 1024)} MB; max {MAX_BYTES // (1024 * 1024)} MB)")
    target = _unique(f"{_safe_stem(original_name)}{ext}")
    target.write_bytes(data)
    return target.name


def list_attachments() -> list[dict]:
    return [{"name": p.name, "size": p.stat().st_size}
            for p in sorted(_dir().glob("*")) if p.is_file()]


def _resolve_one(name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    p = _dir() / name
    try:
        if p.is_file() and p.resolve().parent == _dir().resolve():
            return p
    except OSError:
        return None
    return None


def resolve_paths(names: list[str]) -> list[Path]:
    return [p for p in (_resolve_one(n) for n in (names or [])) if p is not None]


def delete_attachment(name: str) -> bool:
    p = _resolve_one(name)
    if p is None:
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


def guess_mime(path: Path) -> tuple[str, str]:
    ext = path.suffix.lower()
    if ext in _MIME_FALLBACK:
        return _MIME_FALLBACK[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    if guessed and "/" in guessed:
        maintype, subtype = guessed.split("/", 1)
        return maintype, subtype
    return "application", "octet-stream"
