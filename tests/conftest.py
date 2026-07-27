"""Ensure the app package and the engine module dir are importable during tests."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "engine")):
    if p not in sys.path:
        sys.path.insert(0, p)

# tests default to the stub provider and an isolated data dir unless a test overrides it
os.environ.setdefault("PARIS_PROVIDER", "stub")
os.environ.setdefault("PARIS_DATA_DIR", os.path.join(ROOT, ".test_data"))
