"""Harvest adapters package."""
from __future__ import annotations

from .techeu_funding_feed import TechEuFundingFeed
from .grounded_search import GroundedSearchHarvester
from .franceinvest_directory import FranceInvestDirectoryHarvester

AVAILABLE_HARVESTERS = {
    "techeu_funding_feed": TechEuFundingFeed(),
    "grounded_search": GroundedSearchHarvester(),
    "franceinvest_directory": FranceInvestDirectoryHarvester(),
}
