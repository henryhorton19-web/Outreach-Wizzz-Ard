"""Sourcing package for Paris Outreach ("Find new targets")."""
from __future__ import annotations

from typing import Any, Protocol


class Source(Protocol):
    source_id: str

    def harvest(self, recency_days: int = 120, max_items: int = 40,
                custom_prompt: Any | None = None) -> list[dict]:
        """Harvest raw candidate records."""
        ...
