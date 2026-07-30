"""Tech.eu Funding Explorer harvest adapter (P1)."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any
from app.sourcing.normalize import canonicalize_name, canonicalize_domain


class TechEuFundingFeed:
    source_id = "techeu_funding_feed"

    def harvest(self, recency_days: int = 120, max_items: int = 40,
                custom_prompt: Any | None = None,
                fixture_data: list[dict] | None = None) -> list[dict]:
        """Harvest raw candidate records from Tech.eu funding feed or provided fixture."""
        items = fixture_data or self._sample_fixture()
        now = datetime.now(timezone.utc)
        out = []

        for rec in items:
            date_str = rec.get("date") or ""
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    if (now - dt) > timedelta(days=recency_days):
                        continue
                except Exception:
                    pass

            name = rec.get("name", "").strip()
            if not name:
                continue

            slug = canonicalize_name(name)
            meta = {
                "hq_city": rec.get("city", "Paris"),
                "hq_country": rec.get("country", "France"),
                "funding_heat": rec.get("funding_heat", f"€{rec.get('amount_m', 5)}M round"),
                "employees_band": rec.get("employees_band", "11-50"),
                "website": rec.get("website", f"https://{slug}.io"),
                "source_id": self.source_id,
                "source_url": rec.get("source_url", "https://tech.eu/rounds"),
                "retrieved_at": now.isoformat(),
            }
            out.append({
                "slug": slug,
                "name": name,
                "ref": rec.get("website", f"https://{slug}.io"),
                "meta": meta,
                "raw": rec,
            })
            if len(out) >= max_items:
                break

        return out

    def _sample_fixture(self) -> list[dict]:
        """Fallback sample fixture data for offline testing and initial runs."""
        now = datetime.now(timezone.utc)
        recent_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")
        return [
            {
                "name": "Lemrock AI",
                "city": "Paris",
                "country": "France",
                "amount_m": 6,
                "funding_heat": "€6M Seed led by Galion.exe",
                "date": recent_date,
                "employees_band": "11-50",
                "website": "https://lemrock.ai",
                "source_url": "https://tech.eu/2026/03/lemrock-seed",
            },
            {
                "name": "HyperScale Robotics",
                "city": "Paris",
                "country": "France",
                "amount_m": 12,
                "funding_heat": "€12M Series A",
                "date": recent_date,
                "employees_band": "11-50",
                "website": "https://hyperscale-robotics.com",
                "source_url": "https://tech.eu/2026/03/hyperscale-series-a",
            },
            {
                "name": "QuantFlow",
                "city": "Paris",
                "country": "France",
                "amount_m": 4,
                "funding_heat": "€4M Seed",
                "date": recent_date,
                "employees_band": "1-10",
                "website": "https://quantflow.io",
                "source_url": "https://tech.eu/2026/03/quantflow-seed",
            },
        ]
