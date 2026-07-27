"""Provider abstraction — the ONLY place in the app that talks to a model or the web.

The engine never calls a model; the research/compose services never embed SDK specifics.
A provider owns exactly one thing: given a system prompt and a user message, produce text
(optionally grounded in a web search, returning the source URLs it used). Prompt construction
and JSON parsing live in the services (research.py / compose.py), shared across providers, so
adding a provider is just SDK + web mechanics, never duplicated parsing.

Backends:
  gemini    (default) google-genai, Google Search grounding
  anthropic (option)  anthropic SDK, web_search tool
  stub                no network; used by tests and the offline demo loop
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class GenResult:
    text: str
    source_urls: list[str] = field(default_factory=list)
    searches_used: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0


@runtime_checkable
class Provider(Protocol):
    name: str
    is_stub: bool

    def generate(
        self,
        *,
        system: str,
        user: str,
        use_web: bool = False,
        max_web: int | None = None,
        allowed_domains: list[str] | None = None,
        temperature: float = 0.4,
        timeout_s: int = 120,
        max_retries: int = 4,
        model: str | None = None,          # per-call model override (else the provider's base model)
        thinking_budget: int | None = None,  # None = leave default; 0 disables (Flash); >0 caps
        thinking_level: str | None = None,   # None = leave default
        max_output_tokens: int = 0,        # 0 = do not set
        response_schema: object | None = None,  # pydantic model / dict for structured JSON output
    ) -> GenResult:
        ...


class ProviderError(RuntimeError):
    """Raised when a provider cannot produce a usable result (auth, rate limit exhausted,
    network). The service turns this into a per-row error state, never a batch crash."""


def make_provider(name: str, api_key: str | None, model: str | None = None):
    """Instantiate a provider by name. Imports the heavy SDK lazily so the app starts (and the
    stub/tests run) without google-genai / anthropic installed. `model`, when given, is the base
    model id from settings; the provider falls back to its own default if it is None/empty."""
    name = (name or "gemini").lower()
    if name == "stub":
        from .stub import StubProvider
        return StubProvider()
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(api_key=api_key, model=model) if model else GeminiProvider(api_key=api_key)
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, model=model) if model else AnthropicProvider(api_key=api_key)
    raise ValueError(f"unknown provider '{name}'")
