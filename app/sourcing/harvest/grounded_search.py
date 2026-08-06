"""Grounded search harvest adapter (P1) for editorial startup heat."""
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from app.sourcing.normalize import canonicalize_name


class GroundedSearchHarvester:
    source_id = "grounded_search"

    def harvest(self, recency_days: int = 120, max_items: int = 40,
                custom_prompt: Any | None = None,
                fixture_data: list[dict] | None = None,
                provider: Any = None) -> list[dict]:
        """Harvest startup heat candidates via grounded web search or fixture."""
        query = self.build_query(custom_prompt, recency_days)
        is_live = os.environ.get("WIZZARD_SOURCING_LIVE") == "1" and provider is not None and getattr(provider, "provider", "") != "stub"
        if is_live:
            items = self._live_harvest(query, max_items, provider)
        else:
            items = fixture_data or self._sample_fixture(custom_prompt, query)
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
                "recency_unknown": True,
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

    def build_query(self, custom_prompt: Any | None = None,
                    recency_days: int = 120) -> str:
        """Build the search query this preset asks for.

        Separated from harvest() so the query is inspectable: it is what the preset
        editor previews, and it is the thing tests assert on. Before this existed,
        criteria_text reached only an f-string in verify.py and recency_days was
        accepted by every harvester and used by none.
        """
        mandate = (getattr(custom_prompt, "criteria_text", "") or "").strip()
        excludes = (getattr(custom_prompt, "exclude_notes", "") or "").strip()
        if not mandate:
            mandate = ("early-stage and growth technology companies with recent funding "
                       "or hiring momentum")
        parts = [
            f"Companies matching: {mandate}",
            f"Only signals published within the last {int(recency_days)} days.",
        ]
        if excludes:
            parts.append(f"Exclude: {excludes}")
        return " ".join(parts)

    def _sample_fixture(self, custom_prompt: Any | None = None,
                        query: str | None = None) -> list[dict]:
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

    def _live_harvest(self, query: str, max_items: int, provider: Any) -> list[dict]:
        """Call provider with web search enabled to find candidates matching query."""
        from app.json_contract import call_with_retry
        sys_prompt = "You are a Paris technology ecosystem researcher. Return a JSON array of objects matching the search query, each with keys: name, city, country, press_signal, employees_band, website, source_url."
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "city": {"type": "string"},
                    "country": {"type": "string"},
                    "press_signal": {"type": "string"},
                    "employees_band": {"type": "string"},
                    "website": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["name"]
            }
        }
        res = call_with_retry(provider, system=sys_prompt, user=query, schema=schema)
        return res if isinstance(res, list) else []
