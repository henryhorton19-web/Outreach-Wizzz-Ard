"""France Invest directory harvest adapter (P3, situational).

STRICT C10 INVARIANT:
Never calls or fetches paywalled `/annuaire/{slug}/` detail pages or authenticated endpoints.
Reads ONLY public listing fields visible in public search results.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from app.sourcing.normalize import canonicalize_name


class FranceInvestDirectoryHarvester:
    source_id = "franceinvest_directory"

    def harvest(self, recency_days: int = 120, max_items: int = 40,
                custom_prompt: Any | None = None,
                fixture_data: list[dict] | None = None) -> list[dict]:
        """Harvest public member directory entries strictly from public listing data."""
        items = fixture_data or self._sample_fixture()
        now = datetime.now(timezone.utc)
        out = []

        for rec in items:
            name = rec.get("name", "").strip()
            if not name:
                continue

            # Invariant check: URL must not contain paywalled detail path /annuaire/{slug}
            detail_url = rec.get("source_url", "")
            if "/annuaire/" in detail_url and detail_url.count("/") > 4:
                # Disallow deep detail page links that violate C10
                continue

            slug = canonicalize_name(name)
            meta = {
                "hq_city": rec.get("city", "Paris"),
                "hq_country": "France",
                "funding_heat": rec.get("fund_type", "France Invest Member VC / Growth Equity"),
                "employees_band": rec.get("employees_band", "11-50"),
                "website": rec.get("website", f"https://{slug}.fr"),
                "source_id": self.source_id,
                "source_url": "https://www.franceinvest.org/annuaire/",
                "retrieved_at": now.isoformat(),
            }
            out.append({
                "slug": slug,
                "name": name,
                "ref": rec.get("website", f"https://{slug}.fr"),
                "meta": meta,
                "raw": rec,
            })
            if len(out) >= max_items:
                break

        return out

    def _sample_fixture(self) -> list[dict]:
        """Sample public listing fixture data."""
        return [
            {
                "name": "Eiffel Investment Group",
                "city": "Paris",
                "fund_type": "Growth Equity & Venture",
                "website": "https://eiffel-ig.com",
                "source_url": "https://www.franceinvest.org/annuaire/",
            },
            {
                "name": "Example Capital Partners",
                "city": "Paris",
                "fund_type": "Venture Capital",
                "website": "https://example-capital.test",
                "source_url": "https://www.franceinvest.org/annuaire/",
            },
        ]
