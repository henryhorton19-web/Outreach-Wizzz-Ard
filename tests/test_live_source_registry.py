"""Only harvesters that can actually search may be registered.

techeu_funding_feed and franceinvest_directory return hardcoded rows and cannot
accept a provider. A source that returns invented companies is worse than an absent
one, because the run report counts them as harvested and the failure reads as a
screening problem rather than a supply problem.
"""
import inspect

from app.sourcing.harvest import AVAILABLE_HARVESTERS


def test_every_registered_harvester_can_accept_a_provider():
    broken = []
    for source_id, adapter in AVAILABLE_HARVESTERS.items():
        params = inspect.signature(adapter.harvest).parameters
        if "provider" not in params:
            broken.append(source_id)
    assert not broken, f"registered but cannot ever be live: {broken}"


def test_grounded_search_is_registered():
    assert "grounded_search" in AVAILABLE_HARVESTERS
