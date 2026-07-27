"""Single import seam for the untouched engine.

The engine files live in ../engine and are a flat module set (draft_engine imports `config`),
not a package. We add that dir to sys.path here so the rest of the app can `from .engine_bridge
import de, engine_config` without every module repeating the path hack. The engine is imported
exactly as shipped; we never modify it.
"""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import draft_engine as de          # noqa: E402
import config as engine_config     # noqa: E402

__all__ = ["de", "engine_config", "ENGINE_DIR"]
