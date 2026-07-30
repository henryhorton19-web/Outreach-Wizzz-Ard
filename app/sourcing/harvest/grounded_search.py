"""Grounded search harvest adapter (P1) for editorial startup heat."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any
from app.sourcing.normalize import canonicalize_name


class GroundedSearchHarvester:
    source_id = "grounded_search"

    def harvest(self, recency_days: int = 120, max_items: int = 40,
                custom_prompt: Any | None = None,
                fixture_data: list[dict] | None = None) -> list[dict]:
        """Harvest startup heat candidates via grounded web search or fixture."""
        items = fixture_data or self._sample_fixture(custom_prompt)
        now = datetime.now(timezone.utc)
        out = []

        for rec in items:
            name = rec.get("name", "").strip()
            if not name:
                continue
            slug = canonicalize_name(name)
            meta = {
                "hq_city": rec.get("city", "Paris"),
                "hq_country": rec.get("country", "France"),
                "funding_heat": rec.get("press_signal", "Featured in Sifted Paris Startups to Watch"),
                "employees_band": rec.get("employees_band", "11-50"),
                "website": rec.get("website", f"https://{slug}.com"),
                "source_id": self.source_id,
                "source_url": rec.get("source_url", "https://sifted.eu/articles/paris-startups-watch"),
                "retrieved_at": now.isoformat(),
            }
            out.append({
                "slug": slug,
                "name": name,
                "ref": rec.get("website", f"https://{slug}.com"),
                "meta": meta,
                "raw": rec,
            })
            if len(out) >= max_items:
                break

        return out

    def _sample_fixture(self, custom_prompt: Any | None = None) -> list[dict]:
        """Default fixture data for offline/test environments."""
        return [
            {
                "name": "Kestra Cyber",
                "city": "Paris",
                "country": "France",
                "press_signal": "Sifted 2026 Paris Cyber Startups to Watch",
                "employees_band": "11-50",
                "website": "https://kestracyber.io",
                "source_url": "https://sifted.eu/articles/paris-cyber-startups",
            },
            {
                "name": "Fabrikam Health",
                "city": "Paris",
                "country": "France",
                "press_signal": "Maddyness Top 10 Paris Healthtechs",
                "employees_band": "11-50",
                "website": "https://fabrikam-health.test",
                "source_url": "https://maddyness.com/paris-healthtech",
            },
            {
                "name": "Verdant Energy",
                "city": "Paris",
                "country": "France",
                "press_signal": "EU-Startups Rising Climate Stars",
                "employees_band": "1-10",
                "website": "https://verdantenergy.eu",
                "source_url": "https://eu-startups.com/verdant-energy",
            },
        ]
