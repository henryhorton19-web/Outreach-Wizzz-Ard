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
                provider: Any = None,
                exclude_names: list[str] | None = None,
                run_index: int = 0) -> list[dict]:
        """Harvest startup heat candidates via grounded web search or fixture."""
        window = self.window_for_run(recency_days, run_index)
        sector = self.sector_for_run(custom_prompt, run_index)
        query = self.build_query(custom_prompt, window, exclude_names, sector)
        # Go live whenever a real provider is available. This was previously gated on
        # WIZZARD_SOURCING_LIVE=1, a variable nothing in the application sets, so the
        # live path never ran for any user and the harvester returned fixtures
        # forever. The stub provider already gives tests an offline path, so the
        # variable added nothing except a permanent silent failure.
        searches_used = 0
        if fixture_data is not None:
            items = fixture_data
        elif provider is not None and not getattr(provider, "is_stub", False):
            items, searches_used = self._live_harvest(query, max_items, provider)
        else:
            items = self._sample_fixture(custom_prompt, query)

        # A grounded model decides for itself whether to search. A broad question is
        # answerable from training data, so a zero-search batch is not a finding.
        grounded = searches_used > 0
        now = datetime.now(timezone.utc)
        out = []

        for rec in items:
            name = str(rec.get("name") or "").strip()
            if not name:
                continue

            # A company without a citation is one the model recalled or invented.
            # Grounding reduces hallucination by roughly 40%, not to zero, and an
            # invented company consumes a research call and can reach a reviewer.
            source_url = str(rec.get("source_url") or "").strip()
            if grounded and not source_url:
                continue

            slug = canonicalize_name(name)
            if not slug:
                continue

            # Never fabricate. A guessed domain flows into contact discovery and
            # from there into a real email. An empty field is a fact.
            website = str(rec.get("website") or "").strip()

            meta = {
                "hq_city": str(rec.get("city") or ""),
                "hq_country": str(rec.get("country") or ""),
                "funding_heat": str(rec.get("press_signal") or ""),
                "employees_band": str(rec.get("employees_band") or ""),
                "website": website,
                "website_unresolved": not website,
                "source_id": self.source_id,
                "source_url": source_url,
                "retrieved_at": now.isoformat(),
                "grounded": grounded,
                "searches_used": searches_used,
            }
            out.append({
                "slug": slug,
                "name": name,
                "ref": website,
                "meta": meta,
                "raw": rec,
            })
            if len(out) >= max_items:
                break

        return out

    # Sector vocabulary recognised inside a mandate description. Rotation is derived
    # from the criteria text the user already writes, so no schema field is needed
    # and every existing preset keeps working.
    _SECTOR_TERMS = (
        "B2B software", "FinTech", "Digital Health", "Climate Tech", "SaaS",
        "payments", "insurtech", "marketplace", "developer tools", "cybersecurity",
        "AI", "logistics", "HR tech", "proptech", "energy",
    )
    _WINDOW_ROTATION = (30, 90, 180, 365)

    def sectors_in_mandate(self, custom_prompt: Any | None) -> list[str]:
        """Sector terms named in the mandate, in the order they appear."""
        text = (getattr(custom_prompt, "criteria_text", "") or "").lower()
        if not text:
            return []
        found = [(text.find(s.lower()), s) for s in self._SECTOR_TERMS if s.lower() in text]
        return [s for _, s in sorted(found)]

    def sector_for_run(self, custom_prompt: Any | None, run_index: int) -> str:
        """Rotate emphasis across the sectors the mandate names.

        Returns "" when the mandate names none, so the query stays broad rather than
        inventing a focus the user did not ask for.
        """
        sectors = self.sectors_in_mandate(custom_prompt)
        return sectors[run_index % len(sectors)] if sectors else ""

    def window_for_run(self, base_days: int, run_index: int) -> int:
        """Rotate the recency window, never widening beyond the configured base."""
        if run_index <= 0:
            return base_days
        candidates = [w for w in self._WINDOW_ROTATION if w <= base_days] or [base_days]
        return candidates[run_index % len(candidates)]

    def build_query(self, custom_prompt: Any | None = None,
                    recency_days: int = 120,
                    exclude_names: list[str] | None = None,
                    sector_focus: str = "") -> str:
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

        if sector_focus:
            parts.append(f"On this pass, concentrate on {sector_focus}.")

        if exclude_names:
            shown = [str(n) for n in list(exclude_names)[:60] if str(n).strip()]
            if shown:
                parts.append("Do NOT return any of these, they have already been reviewed: "
                             + ", ".join(shown) + ". Find different companies.")

        parts.append(
            "For every company you return you MUST cite the page you found it on. "
            "If you cannot cite a page for a company, leave it out.")
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

    def _live_harvest(self, query: str, max_items: int, provider: Any) -> tuple[list[dict], int]:
        """One grounded call. Returns (rows, searches_used).

        Calls the provider directly. The previous version imported
        app.json_contract, which does not exist in this codebase, so this method
        raised ModuleNotFoundError on its first line and research_job.py swallowed
        it into job["errors"], leaving the harvester permanently on fixtures.

        Temperature is 1.0 because the grounding documentation recommends it, and a
        near-deterministic call returns a near-identical list every run.
        """
        system = (
            "You are a research analyst building an investment pipeline. Use web search to find "
            "companies matching the request. Return ONLY a JSON array, no prose.\n\n"
            "Each object: name, city, country, press_signal, employees_band, website, source_url.\n\n"
            "source_url must be the page you actually found the company on, in this search. "
            "Omit any company you cannot cite. It is correct to return fewer companies than "
            "asked for. It is not acceptable to include one you did not read on a page."
        )
        try:
            res = provider.generate(
                system=system,
                user=f"{query}\n\nReturn at most {max_items} companies as a JSON array.",
                use_web=True,
                max_web=6,
                temperature=1.0,
                timeout_s=90,
            )
        except Exception:
            return [], 0

        searches = int(getattr(res, "searches_used", 0) or 0)
        return self._parse_array(getattr(res, "text", "") or ""), searches

    @staticmethod
    def _parse_array(text: str) -> list[dict]:
        """Parse a JSON array from model output, tolerating fences and wrappers."""
        import json as _json
        import re as _re
        cleaned = _re.sub(r"^```(?:json)?|```$", "", text or "", flags=_re.MULTILINE).strip()
        data = None
        try:
            data = _json.loads(cleaned)
        except Exception:
            start, end = cleaned.find("["), cleaned.rfind("]")
            if start != -1 and end != -1 and end > start:
                try:
                    data = _json.loads(cleaned[start:end + 1])
                except Exception:
                    return []
        if isinstance(data, dict):
            for key in ("companies", "results", "items", "candidates"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
