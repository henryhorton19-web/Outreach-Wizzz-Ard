"""Adaptive edit ledger (Phase 3).

Captures the seniors' body-level edits at approve time and feeds a rolling per-voice window back
into the compose prompt, so future drafts drift toward how they actually revise. The fixed house
rules and the curated few-shot in compose.py are the stable backbone; this is the adaptive layer
on top of them.

App layer only. Storage is one JSONL file per voice under the data dir. Every function swallows
its own errors: the ledger must never break an approve or a draft.
"""
from __future__ import annotations

import json
import difflib
import datetime
from pathlib import Path

from . import settings as S


def edit_effort(before: str, after: str) -> float:
    """Normalised edit distance in [0, 1]: 0 = identical (draft was kept), 1 = fully rewritten.
    This is the 'editing effort' signal (cf. PRELUDE/CIPHER) — small effort means the voice is
    already right (reinforce), large effort means learn the delta. Uses stdlib difflib, no dep."""
    b, a = (before or "").strip(), (after or "").strip()
    if not b and not a:
        return 0.0
    ratio = difflib.SequenceMatcher(None, b, a).ratio()
    return max(0.0, min(1.0, 1.0 - ratio))

LEDGER_DIR = S.DATA_DIR / "edit_ledger"
try:
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

_MAX_STORED = 50          # keep each voice file bounded
_MIN_DIFF_CHARS = 12      # ignore trivial edits (a typo, a single word)
_DEFAULT_K = 4            # how many recent pairs to inject into the prompt


def _path(voice: str) -> Path:
    safe = "".join(ch for ch in (voice or "default") if ch.isalnum() or ch in "-_") or "default"
    return LEDGER_DIR / f"{safe}.jsonl"


def _extract_edited_body(machine_email: str, machine_body: str, final_email: str):
    """Recover the senior's edited BODY from the final full email, using the machine email and
    machine body as anchors. Returns the edited body when only the body changed; returns None when
    the frame (greeting, opening, fund paragraph, close) was also edited, since that is ambiguous
    to attribute to the body and is better skipped than stored noisily."""
    if not (machine_email and machine_body and final_email):
        return None
    idx = machine_email.find(machine_body)
    if idx == -1:
        return None
    prefix = machine_email[:idx]
    suffix = machine_email[idx + len(machine_body):]
    if final_email.startswith(prefix) and final_email.endswith(suffix) \
            and len(final_email) >= len(prefix) + len(suffix):
        end = len(final_email) - len(suffix)
        return final_email[len(prefix):end]
    return None


def record_edit(voice: str, machine_email: str, machine_body: str, final_email: str,
                sent_id: str = "") -> bool:
    """On approve, store a body before/after pair when the senior meaningfully edited the body.
    Returns True if a pair was stored. Never raises.

    `sent_id` links the pair to its SentItem so the learning loop can weight it by outcome
    (replied/awaiting/bounced). `effort` is the normalised edit distance (the learning signal)."""
    try:
        if not machine_email or not final_email or final_email == machine_email:
            return False
        edited_body = _extract_edited_body(machine_email, machine_body, final_email)
        if edited_body is None:
            return False
        before = (machine_body or "").strip()
        after = edited_body.strip()
        if not after or after == before:
            return False
        # skip trivial tweaks (near-identical length and same opening)
        if abs(len(after) - len(before)) < _MIN_DIFF_CHARS and after[:40] == before[:40]:
            return False
        rec = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "voice": voice or "",
            "before": before,
            "after": after,
            "effort": edit_effort(before, after),
            "sent_id": sent_id or "",
        }
        p = _path(voice)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _trim(p)
        return True
    except Exception:
        return False


def _trim(p: Path) -> None:
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_STORED:
            p.write_text("\n".join(lines[-_MAX_STORED:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def edit_count(voice: str) -> int:
    """How many meaningful body edits have been recorded for this voice. A coarse proxy for
    'how often the draft is rewritten before approval' shown in the Voice Performance table.
    Never raises."""
    try:
        p = _path(voice)
        if not p.exists():
            return 0
        return sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())
    except Exception:
        return 0


def edit_intensity(voice: str) -> float | None:
    """A 0..1 edit-intensity score for the perf table's tiny bar: recorded edits capped at the
    stored window. None when there is nothing recorded (renders as no bar). Never raises."""
    n = edit_count(voice)
    if n <= 0:
        return None
    return min(1.0, n / float(_MAX_STORED))


def recent_examples(voice: str, k: int = _DEFAULT_K) -> list[tuple[str, str]]:
    """Most recent k (before, after) pairs for this voice, oldest first. Never raises."""
    try:
        p = _path(voice)
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()
        out: list[tuple[str, str]] = []
        for ln in lines[-k:]:
            try:
                r = json.loads(ln)
                if r.get("before") and r.get("after"):
                    out.append((r["before"], r["after"]))
            except Exception:
                continue
        return out
    except Exception:
        return []


def triples_for_learning(voice: str, k: int = 20) -> list[dict]:
    """Most recent k learning records for a voice (oldest first), each a dict with before/after/
    effort/sent_id/ts. The learning loop joins `sent_id` to a SentItem for the outcome weight.
    Records written before this field existed simply carry effort=recomputed, sent_id=''. Never
    raises."""
    try:
        p = _path(voice)
        if not p.exists():
            return []
        out: list[dict] = []
        for ln in p.read_text(encoding="utf-8").splitlines()[-k:]:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            before, after = r.get("before"), r.get("after")
            if not (before and after):
                continue
            out.append({
                "ts": r.get("ts", ""),
                "before": before,
                "after": after,
                "effort": r.get("effort", edit_effort(before, after)),
                "sent_id": r.get("sent_id", ""),
            })
        return out
    except Exception:
        return []


def examples_block(voice: str, k: int = _DEFAULT_K) -> str:
    """Render recent edits as an appendable compose-prompt block, or '' if there are none.
    Injected alongside the fixed REVISION EXAMPLES in compose.py."""
    pairs = recent_examples(voice, k)
    if not pairs:
        return ""
    parts = ["\n--- RECENT REVISIONS BY THE SENIORS (learn from these; most recent last) ---"]
    for i, (before, after) in enumerate(pairs, 1):
        parts.append(f"\nRevision {i}\nBEFORE: {before}\nAFTER:  {after}")
    return "\n".join(parts) + "\n"
