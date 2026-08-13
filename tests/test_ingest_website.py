"""Column 2 of an uploaded sheet is the website, and it must survive all the way to research.

research._identity_anchor only anchors profiling to a domain when CompanyState.website is set;
without it the model is told to work out which company this is, and it picks the better-known one.
Two companies called Elix is the failure this prevents.
"""
import io

import pytest

from app import ingest, store


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


def test_csv_second_column_is_read_as_website():
    rows = ingest.parse_csv_bytes(_csv("Northwind AI,https://northwind-ai.test\nExample Geo,example-geo.test\n"))
    assert [r["name"] for r in rows] == ["Northwind AI", "Example Geo"]
    assert rows[0]["website"] == "https://northwind-ai.test"
    assert rows[1]["website"] == "example-geo.test"


def test_csv_header_row_is_skipped_on_either_column():
    rows = ingest.parse_csv_bytes(_csv("Company,Website\nNorthwind AI,https://northwind-ai.test\n"))
    assert len(rows) == 1
    assert rows[0]["name"] == "Northwind AI"


def test_csv_without_a_second_column_still_works():
    rows = ingest.parse_csv_bytes(_csv("Northwind AI\nExample Geo\n"))
    assert len(rows) == 2
    assert "website" not in rows[0]


def test_non_url_second_column_is_ignored_not_passed_to_research():
    rows = ingest.parse_csv_bytes(_csv("Northwind AI,follow up next week\n"))
    assert rows[0]["name"] == "Northwind AI"
    assert "website" not in rows[0], "a non-URL column 2 must not reach research as a website"


def test_pasted_text_website_reaches_the_queue_record(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "QUEUE_FILE", tmp_path / "queue.json")
    store.upsert_queue("elix_ai", "Northwind AI", None, None, list_id="default",
                       website="https://northwind-ai.test")
    rec = [r for r in store.load_queue(list_id="default") if r["slug"] == "elix_ai"][0]
    assert rec["website"] == "https://northwind-ai.test"


def test_clean_website_normalises_and_rejects():
    assert ingest._clean_website("  https://northwind-ai.test/about ") == "https://northwind-ai.test/about"
    assert ingest._clean_website("www.northwind-ai.test") == "www.northwind-ai.test"
    assert ingest._clean_website("northwind-ai.test") == "northwind-ai.test"
    assert ingest._clean_website("call them Monday") == ""
    assert ingest._clean_website("") == ""
    assert ingest._clean_website(None) == ""
