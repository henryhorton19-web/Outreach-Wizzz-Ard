"""Stub provider: no network, deterministic. Used by the test suite and the offline demo.

It never calls .generate() for real work — the research/compose services detect is_stub and
build deterministic output themselves (research: a hand cache; compose: engine mock_email),
because that domain logic belongs in the services, not the provider. .generate() is present
only to satisfy the Provider protocol and raises if ever called, so a stub can never silently
masquerade as a real model call.
"""
from __future__ import annotations

from .base import GenResult


class StubProvider:
    name = "stub"
    is_stub = True

    def generate(self, **kwargs) -> GenResult:  # pragma: no cover - must never be reached
        raise RuntimeError(
            "StubProvider.generate() called — the stub path must be handled in the service "
            "(is_stub branch), never by making a fake model call.")
