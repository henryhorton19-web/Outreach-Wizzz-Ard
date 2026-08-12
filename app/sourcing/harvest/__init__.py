"""Harvest adapters package."""
from __future__ import annotations

from .techeu_funding_feed import TechEuFundingFeed
from .grounded_search import GroundedSearchHarvester
from .franceinvest_directory import FranceInvestDirectoryHarvester

# Only harvesters that can perform a live search are registered.
#
# techeu_funding_feed and franceinvest_directory return hardcoded rows and cannot accept
# a provider, so they can never return a real company. A source that returns invented
# companies is worse than an absent one: the run report counts them as harvested, so the
# failure reads as a screening problem rather than a supply problem, which is exactly how
# a broken sourcer went unnoticed.
#
# The classes are kept, unregistered, so a real implementation can be dropped in without
# rebuilding the module.
AVAILABLE_HARVESTERS = {
    "grounded_search": GroundedSearchHarvester(),
}

# Retained for reference and for tests that exercise the offline path directly.
FIXTURE_ONLY_HARVESTERS = {
    "techeu_funding_feed": TechEuFundingFeed(),
    "franceinvest_directory": FranceInvestDirectoryHarvester(),
}
