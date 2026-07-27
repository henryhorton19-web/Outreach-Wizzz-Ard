"""Ingestion: turn pasted text (one target per line), a CSV (first column = name), or the
Outreach_Tracker workbook into normalized rows. Trusted input: no scoring, no fit verdict. A
ref token (category/source) is kept only for display/audit; nothing downstream depends on it.
"""
from __future__ import annotations

import csv
import io
import re

from .slugs import slug
from . import tracker as tracker_mod


def _split_line(line: str) -> tuple[str, str | None, str | None]:
    """A line may be 'Target Name' or 'Target Name, ref' or 'Target Name, https://url'.
    Only treat a trailing token as a ref if it looks id-ish (alnum/dash, no spaces).
    Returns (name, ref, website)"""
    line = line.strip()
    if not line:
        return "", None, None
    for sep in ("\t", ","):
        if sep in line:
            head, _, tail = line.rpartition(sep)
            tail = tail.strip()
            if head.strip():
                if re.match(r"^(https?://|www\.)\S+$", tail) or re.match(r"^[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}(/\S*)?$", tail):
                    return head.strip(), None, tail
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-_]{1,}", tail):
                    return head.strip(), tail, None
            return line.replace("\t", " ").strip(), None, None
    return line, None, None


def _display_name(raw: str) -> str:
    raw = (raw or "").strip()
    # Only fix all-lowercase input; preserve intentional casing (eGym, PPRO, xAI...).
    return raw.title() if raw.islower() else raw


def _rows_from_records(records: list[dict]) -> list[dict]:
    rows: list[dict] = []
    seen: dict[str, int] = {}
    for rec in records:
        name = _display_name(rec.get("name", ""))
        if not name:
            continue
        key = slug(name)
        if key in seen:
            seen[key] += 1
            key = f"{key}_{seen[key]}"
        else:
            seen[key] = 0
        row = {"slug": key, "name": name}
        if rec.get("ref"):
            row["ref"] = rec["ref"]
        if rec.get("website"):
            row["website"] = rec["website"]
        # optional contact hints carried from the tracker (used to seed research)
        if rec.get("contact_name"):
            row["contact_name"] = rec["contact_name"]
        if rec.get("contact_email"):
            row["contact_email"] = rec["contact_email"]
        rows.append(row)
    return rows


def parse_names(text: str) -> list[dict]:
    """Return list of {slug, name, ref?} from pasted text, de-duplicated."""
    records = []
    for raw in (text or "").splitlines():
        name, ref, website = _split_line(raw)
        if not name:
            continue
        rec = {"name": name}
        if ref:
            rec["ref"] = ref
        if website:
            rec["website"] = website
        records.append(rec)
    return _rows_from_records(records)


def parse_csv_bytes(data: bytes) -> list[dict]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    records = []
    for i, r in enumerate(reader):
        if not r:
            continue
        first = (r[0] or "").strip()
        if i == 0 and first.lower() in ("company name", "company", "name", "target"):
            continue  # header
        if first:
            records.append({"name": first})
    return _rows_from_records(records)


def parse_xlsx_bytes(data: bytes) -> list[dict]:
    """If the workbook looks like the Outreach_Tracker (has the target sheets), read those;
    otherwise fall back to first-column-of-first-sheet."""
    records = tracker_mod.parse_tracker_bytes(data)
    if records:
        return _rows_from_records(records)
    # fallback: first column of the active sheet
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.active
        out = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if not row:
                continue
            first = row[0]
            if first is None:
                continue
            first = str(first).strip()
            if i == 0 and first.lower() in ("company name", "company", "name", "target"):
                continue
            if first:
                out.append({"name": first})
        return _rows_from_records(out)
    except Exception:
        return []
