"""Tests for ingest ordering (XLSX, CSV, pasted text).

Verifies that ingesting a multi-row spreadsheet preserves exact top-to-bottom sequence
so Row 1 of the file appears at index 0 (top of queue), Row 2 at index 1, etc.
"""
import io
import pytest
import openpyxl

from app import ingest, store


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    monkeypatch.setattr(store, "LISTS_FILE", tmp_path / "lists.json")
    yield


def test_xlsx_ingest_preserves_top_to_bottom_row_order():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Targets"
    ws.append(["Company Name", "Contact Person"])
    companies = ["Alpha Corp", "Beta Inc", "Gamma Ltd", "Delta Tech", "Epsilon AI"]
    for c in companies:
        ws.append([c, f"CEO at {c}"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    rows = ingest.parse_xlsx_bytes(buf.getvalue())
    store.upsert_queue_batch(rows, list_id="default")

    queue = store.load_queue(list_id="default")
    queue_names = [q["name"] for q in queue]

    # Verify order matches spreadsheet top-to-bottom order: Alpha, Beta, Gamma, Delta, Epsilon
    assert queue_names == companies


def test_csv_ingest_preserves_top_to_bottom_order():
    companies = ["First Company", "Second Company", "Third Company", "Fourth Company"]
    raw_lines = ["Company Name"] + companies
    csv_bytes = "\n".join(raw_lines).encode("utf-8")

    rows = ingest.parse_csv_bytes(csv_bytes)
    store.upsert_queue_batch(rows, list_id="default")

    queue = store.load_queue(list_id="default")
    queue_names = [q["name"] for q in queue]

    assert queue_names == companies
