"""Anthropic provider (optional). Messages API via the `anthropic` SDK; the web_search tool
for research, no tools for compose.

Response `content` is a list of mixed blocks (text, server_tool_use, web_search_tool_result).
We concatenate the text blocks for the model's answer and pull source URLs out of the
web_search_tool_result blocks (and any url citations on text blocks).
"""
from __future__ import annotations

import time

from .base import GenResult, ProviderError


class AnthropicProvider:
    name = "anthropic"
    is_stub = False

    def __init__(self, api_key: str | None, model: str = "claude-sonnet-4.6",
                 web_tool: str = "web_search_20250305"):
        if not api_key:
            raise ProviderError("no Anthropic API key configured")
        try:
            import anthropic  # lazy
        except Exception as e:  # pragma: no cover
            raise ProviderError(
                "anthropic SDK is not installed. `pip install anthropic` to use the Anthropic "
                "provider (or switch provider to Gemini in Settings).") from e
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.web_tool = web_tool

    def generate(self, *, system, user, use_web=False, max_web=None, allowed_domains=None,
                 temperature=0.4, timeout_s=120, max_retries=4,
                 model=None, thinking_budget=None, thinking_level=None, max_output_tokens=0,
                 response_schema=None) -> GenResult:
        # thinking_budget and response_schema are Gemini-specific knobs; Anthropic keeps its
        # existing prompt-driven JSON path and ignores them. model / max_output_tokens are honored.
        tools = None
        if use_web:
            tool: dict = {"type": self.web_tool, "name": "web_search"}
            if max_web:
                tool["max_uses"] = int(max_web)          # hard per-call search cap (cost control)
            if allowed_domains:
                tool["allowed_domains"] = list(allowed_domains)
            tools = [tool]

        kwargs = dict(
            model=model or self.model,
            max_tokens=(max_output_tokens if (max_output_tokens and max_output_tokens > 0) else 4096),
            temperature=temperature,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        if tools:
            kwargs["tools"] = tools

        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                resp = self._client.messages.create(**kwargs)
                text = self._extract_text(resp)
                urls = self._extract_sources(resp)
                if not text.strip():
                    raise ProviderError("empty response text from Anthropic")
                
                u = getattr(resp, "usage", None)
                input_tokens = getattr(u, "input_tokens", 0) or 0
                cached_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
                output_tokens = getattr(u, "output_tokens", 0) or 0
                print(f"[Anthropic] tokens - in: {input_tokens}, cached: {cached_tokens}, out: {output_tokens}")

                return GenResult(
                    text=text, 
                    source_urls=urls, 
                    searches_used=len(urls),
                    input_tokens=input_tokens,
                    cached_tokens=cached_tokens,
                    output_tokens=output_tokens
                )
            except Exception as e:
                last_err = e
                if self._is_retryable(e) and attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt, 20))
                    continue
                break
                
        # CAPACITY FALLBACK: if we exhausted retries on a capacity error, try the stable fallback once
        if last_err and self._is_retryable(last_err):
            try:
                st = __import__("app.settings").settings.load_settings()
                fallback = getattr(st, "fallback_model", "")
                if fallback and fallback != (model or self.model):
                    print(f"[Anthropic] {(model or self.model)} unavailable after retries; falling back to {fallback}")
                    kwargs["model"] = fallback
                    for attempt in range(2):
                        try:
                            resp = self._client.messages.create(**kwargs)
                            text = self._extract_text(resp)
                            urls = self._extract_sources(resp)
                            if not text.strip():
                                raise ProviderError(f"empty response from fallback {fallback}")
                            
                            u = getattr(resp, "usage", None)
                            in_t = getattr(u, "input_tokens", 0) or 0
                            out_t = getattr(u, "output_tokens", 0) or 0
                            print(f"[Anthropic:{fallback}] FALLBACK tokens - in: {in_t}, out: {out_t}")
                            return GenResult(
                                text=text, source_urls=urls, searches_used=len(urls),
                                input_tokens=in_t, output_tokens=out_t
                            )
                        except Exception as fb_e:
                            if self._is_retryable(fb_e) and attempt < 1:
                                time.sleep(1)
                                continue
                            raise ProviderError(f"Anthropic fallback {fallback} failed: {fb_e}") from fb_e
            except Exception as outer_e:
                pass

        raise ProviderError(f"Anthropic generate failed: {last_err}") from last_err

    # ---- mixed-block parsing ----

    @staticmethod
    def _extract_text(resp) -> str:
        out = []
        for block in (getattr(resp, "content", None) or []):
            if getattr(block, "type", None) == "text":
                out.append(getattr(block, "text", "") or "")
        return "\n".join(t for t in out if t)

    @staticmethod
    def _extract_sources(resp) -> list[str]:
        urls: list[str] = []

        def _add(u):
            if u and u not in urls:
                urls.append(u)

        for block in (getattr(resp, "content", None) or []):
            btype = getattr(block, "type", None)
            # web_search_tool_result blocks carry the retrieved pages
            if btype == "web_search_tool_result":
                content = getattr(block, "content", None) or []
                for item in content:
                    _add(getattr(item, "url", None) or (item.get("url") if isinstance(item, dict) else None))
            # url citations attached to text blocks
            if btype == "text":
                for cit in (getattr(block, "citations", None) or []):
                    _add(getattr(cit, "url", None) or (cit.get("url") if isinstance(cit, dict) else None))
        return urls

    @staticmethod
    def _is_retryable(e: Exception) -> bool:
        s = f"{type(e).__name__} {e}".lower()
        return any(k in s for k in
                   ("429", "rate", "overloaded", "503", "500", "timeout", "connection",
                    "internal_server"))
