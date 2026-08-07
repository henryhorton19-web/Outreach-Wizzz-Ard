"""Ensure the app package and the engine module dir are importable during tests."""
import os
import sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

# tests default to the stub provider and an isolated data dir unless a test overrides it
os.environ.setdefault("PARIS_PROVIDER", "stub")
os.environ.setdefault("PARIS_DATA_DIR", os.path.join(ROOT, ".test_data"))
os.environ.setdefault("WIZZARD_PROFILE_SOURCE", "fixture")

import engine.config as _C  # noqa: E402
from tests.fixtures.profile import FIXTURE_PROFILE  # noqa: E402
_C.CANDIDATE_PROFILE = FIXTURE_PROFILE

@pytest.fixture(autouse=True)
def _reset_fixture_profile():
    if os.environ.get("WIZZARD_PROFILE_SOURCE") == "fixture":
        import sys
        from tests.fixtures.profile import FIXTURE_PROFILE
        for mod in ("config", "engine.config"):
            if mod in sys.modules:
                setattr(sys.modules[mod], "CANDIDATE_PROFILE", FIXTURE_PROFILE)
