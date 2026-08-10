"""Outreach Wizz-ard — orchestration + UI layer around the pure engine.

The deterministic engine (engine/draft_engine.py) makes NO web or model calls, ever.
Everything in this package is the thin layer that: takes pasted company names, makes the
model calls (research with web, compose without), writes SOURCED results into caches, and
hands them to the untouched engine. Provenance is the product: a fact cannot reach a draft
unless research wrote it into the cache with a source.
"""
__version__ = "1.0.0"
