"""A real provider must reach the harvester, and packaging must include seed data.

research_job read getattr(settings, "provider_instance", None). Settings has no such
attribute, so it was always None, the harvester always used offline sample data, and
grounded search could never run regardless of configuration. The server never passed
one either, though _provider_optional() sits in the same file.
"""
import inspect
import pathlib
import re

import pytest


def test_settings_has_no_provider_instance_attribute():
    """Documents why the old code could never work. If this ever fails, someone added
    the attribute and the wiring below should be reconsidered."""
    from app.settings import Settings
    assert not hasattr(Settings(), "provider_instance")


def test_research_job_does_not_read_a_phantom_attribute():
    src = pathlib.Path("app/sourcing/research_job.py").read_text(encoding="utf-8")
    assert 'getattr(settings, "provider_instance"' not in src, \
        "still reading an attribute that does not exist on Settings"


def test_start_sourcing_job_accepts_a_provider():
    from app.sourcing.research_job import start_sourcing_job
    assert "provider" in inspect.signature(start_sourcing_job).parameters


def test_the_server_passes_a_provider_to_the_sourcing_job():
    src = pathlib.Path("app/server.py").read_text(encoding="utf-8")
    assert "provider=_provider_optional()" in src, \
        "the server never supplies a provider, so the harvester cannot go live"


def test_the_provider_reaches_the_harvester():
    """End to end through the real job, with a fake provider that records calls."""
    from app.settings import Settings
    import app.sourcing.research_job as rj

    class FakeProvider:
        is_stub = False
        provider = "gemini"

        def __init__(self):
            self.calls = 0

        def generate(self, **kwargs):
            self.calls += 1
            payload = ('[{"name":"LiveCo","city":"Berlin","country":"Germany",'
                       '"press_signal":"raised","employees_band":"51-200",'
                       '"website":"https://live.example","source_url":"https://news.example/x"}]')

            class Result:
                text = payload
                searches_used = 2
                source_urls = ["https://news.example/x"]
            return Result()

    prov = FakeProvider()
    rj.start_sourcing_job(settings=Settings(), target_n=2, max_candidates=5,
                          recency_days=180, sources=["grounded_search"], provider=prov)
    assert prov.calls >= 1, "the harvester never called the provider"


def test_counters_are_initialised_before_they_are_written():
    src = pathlib.Path("app/sourcing/research_job.py").read_text(encoding="utf-8")
    for key in ("excluded", "suppressed"):
        assert f'"{key}": 0' in src, f'counts["{key}"] is written but never initialised'


def test_build_spec_has_no_dead_references():
    spec = pathlib.Path("build.spec").read_text(encoding="utf-8")
    refs = re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', spec)
    missing = [s for s, _ in refs if "*" not in s and not pathlib.Path(s).exists()]
    assert not missing, f"build.spec references files that do not exist: {missing}"


@pytest.mark.parametrize("needed", [
    "app/seed_followup_voices",
    "app/seed_sourcing_prompts",
    "app/prompts",
])
def test_build_spec_bundles_every_seed_directory(needed):
    """A directory that is not bundled works from source and is silently missing from
    the built executable, which passes every test and fails after distribution."""
    spec = pathlib.Path("build.spec").read_text(encoding="utf-8")
    assert needed in spec, f"build.spec does not bundle {needed}"
