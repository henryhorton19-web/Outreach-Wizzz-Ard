"""Gemini provider (default). Uses the google-genai SDK's generate_content with Google Search
grounding for research, and no tools for compose.

Notes on current API surface (verify at build time; the SDK is `google-genai`, imported as
`from google import genai`):
  - Grounding for current models uses the `google_search` tool:
        config=GenerateContentConfig(tools=[Tool(google_search=GoogleSearch())], ...)
    (Older models used `google_search_retrieval`; do not use it for current models.)
  - Source URLs come back on candidate.grounding_metadata.grounding_chunks[].web.uri .
  - Gemini 3.x bills per search query; 2.5 bills per prompt. Either way `max_web` here is a
    best-effort cap we surface to the model in the prompt and enforce loosely; the SDK does not
    expose a hard per-call search ceiling the way Anthropic's max_uses does, so cost control on
    Gemini is primarily model choice + prompt discipline.
"""
from __future__ import annotations

import time

from .base import GenResult, ProviderError


class GeminiProvider:
    name = "gemini"
    is_stub = False

    def __init__(self, api_key: str | None, model: str = "gemini-2.5-flash"):
        if not api_key:
            raise ProviderError("no Gemini API key configured")
        try:
            from google import genai  # lazy: app must import without google-genai installed
        except Exception as e:  # pragma: no cover
            raise ProviderError(
                "google-genai is not installed. `pip install google-genai` to use the "
                "Gemini provider (or switch provider to Anthropic in Settings).") from e
        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        self.model = model

    def _build_thinking_config(self, model_id: str, thinking_level: str | None, thinking_budget: int | None):
        from google.genai import types
        cfg_kwargs = {}
        if "gemini-3" in model_id and thinking_level:
            cfg_kwargs["thinking_level"] = thinking_level.upper()
        elif thinking_budget is not None:
            cfg_kwargs["thinking_budget"] = thinking_budget
            
        if cfg_kwargs:
            try:
                return types.ThinkingConfig(**cfg_kwargs)
            except Exception:
                pass
        return None

    def generate(self, *, system, user, use_web=False, max_web=None, allowed_domains=None,
                 temperature=0.4, timeout_s=120, max_retries=4,
                 model=None, thinking_budget=None, thinking_level=None, max_output_tokens=0,
                 response_schema=None) -> GenResult:
        from google.genai import types  # lazy

        tools = None
        if use_web:
            gs = types.GoogleSearch()
            # scope the search to named sources when the caller asks (e.g. email-format lookup)
            if allowed_domains:
                try:
                    gs = types.GoogleSearch(
                        # some SDK versions expose include/exclude domains; guard for absence
                        **({"include_domains": list(allowed_domains)}))
                except TypeError:
                    gs = types.GoogleSearch()
            tools = [types.Tool(google_search=gs)]

        cfg_kwargs = dict(
            system_instruction=system,
            temperature=temperature,
            tools=tools,
            http_options=types.HttpOptions(timeout=timeout_s * 1000),  # ms
        )
        # Optional output cap. On Gemini 3 this bounds thinking+output combined, so callers must
        # pass headroom; 0 means leave it unset (model default).
        if max_output_tokens and max_output_tokens > 0:
            cfg_kwargs["max_output_tokens"] = max_output_tokens
        model_id = model or self.model

        # Minor: drop temperature for gemini-3.x models
        if "gemini-3" in model_id:
            cfg_kwargs.pop("temperature", None)

        tc = self._build_thinking_config(model_id, thinking_level, thinking_budget)
        if tc is not None:
            cfg_kwargs["thinking_config"] = tc

        # Structured output: only when no web tool is active (schema + grounding can conflict).
        has_schema = False
        if response_schema is not None and not use_web:
            cfg_kwargs["response_mime_type"] = "application/json"
            cfg_kwargs["response_schema"] = response_schema
            has_schema = True

        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                try:
                    cfg = types.GenerateContentConfig(**cfg_kwargs)
                except Exception as config_err:
                    if has_schema:
                        # Fallback for SDKs that don't support response_schema
                        has_schema = False
                        cfg_kwargs.pop("response_mime_type", None)
                        cfg_kwargs.pop("response_schema", None)
                        cfg = types.GenerateContentConfig(**cfg_kwargs)
                    else:
                        raise

                resp = self._client.models.generate_content(
                    model=model_id, contents=user, config=cfg)
                text = self._extract_text(resp)
                urls = self._extract_sources(resp)
                if not text.strip():
                    raise ProviderError("empty response text from Gemini")
                
                um = getattr(resp, "usage_metadata", None)
                input_tokens = getattr(um, "prompt_token_count", 0) or 0
                cached_tokens = getattr(um, "cached_content_token_count", 0) or 0
                output_tokens = getattr(um, "candidates_token_count", 0) or 0
                thoughts_tokens = getattr(um, "thoughts_token_count", 0) or 0
                total_tokens = getattr(um, "total_token_count", 0) or 0
                
                queries = 0
                for cand in (getattr(resp, "candidates", None) or []):
                    gm = getattr(cand, "grounding_metadata", None)
                    qs = getattr(gm, "web_search_queries", None)
                    if qs:
                        queries += len(qs)

                print(f"[Gemini:{model_id}] tokens - in: {input_tokens}, cached: {cached_tokens}, "
                      f"out: {output_tokens}, thoughts: {thoughts_tokens}, total: {total_tokens}, webSearchQueries: {queries}")

                return GenResult(
                    text=text, 
                    source_urls=urls,
                    searches_used=len(urls),
                    input_tokens=input_tokens,
                    cached_tokens=cached_tokens,
                    output_tokens=output_tokens
                )
            except Exception as e:  # includes rate limits / transient errors
                # Fallback if API rejects schema (e.g. 400 Invalid Argument)
                if has_schema and not self._is_retryable(e):
                    err_str = str(e).lower()
                    if "schema" in err_str or "mime" in err_str or "400" in err_str or "invalid argument" in err_str:
                        has_schema = False
                        cfg_kwargs.pop("response_mime_type", None)
                        cfg_kwargs.pop("response_schema", None)
                        continue  # immediate retry without schema

                last_err = e
                if self._is_retryable(e) and attempt < max_retries - 1:
                    time.sleep(min(2 ** attempt, 20))
                    continue
                break
                
        # CAPACITY FALLBACK: exhausted retries on a capacity error — try the stable fallback
        # once with a shorter 60s timeout so it degrades fast rather than adding another 120s.
        if last_err and self._is_retryable(last_err):
            try:
                import app.settings as _settings_mod
                st = _settings_mod.load_settings()
                fallback = getattr(st, "fallback_model", "")
                if fallback and fallback != model_id:
                    print(f"[Gemini] {model_id} unavailable after retries; falling back to {fallback}")

                    # Shorter timeout for the fallback call so failure is fast.
                    cfg_kwargs["http_options"] = types.HttpOptions(timeout=60000)

                    # Build a fresh thinking config for the fallback model.
                    # Flash-tier 2.5 models accept 0 to disable thinking; do not use 0 on Pro.
                    fb_budget = 0 if "flash" in fallback.lower() else None
                    tc_fallback = self._build_thinking_config(fallback, None, thinking_budget=fb_budget)
                    if tc_fallback is not None:
                        cfg_kwargs["thinking_config"] = tc_fallback
                    else:
                        cfg_kwargs.pop("thinking_config", None)

                    # Restore temperature if the fallback is not a 3.x model.
                    if "gemini-3" not in fallback:
                        cfg_kwargs["temperature"] = temperature

                    # Single attempt only — fail fast if the fallback is also unavailable.
                    try:
                        cfg = types.GenerateContentConfig(**cfg_kwargs)
                        resp = self._client.models.generate_content(model=fallback, contents=user, config=cfg)
                        text = self._extract_text(resp)
                        urls = self._extract_sources(resp)
                        if not text.strip():
                            raise ProviderError(f"empty response from fallback {fallback}")

                        um = getattr(resp, "usage_metadata", None)
                        in_t = getattr(um, "prompt_token_count", 0) or 0
                        out_t = getattr(um, "candidates_token_count", 0) or 0
                        print(f"[Gemini:{fallback}] FALLBACK tokens - in: {in_t}, out: {out_t}")
                        return GenResult(
                            text=text, source_urls=urls, searches_used=len(urls),
                            input_tokens=in_t, output_tokens=out_t
                        )
                    except Exception as fb_e:
                        raise ProviderError(f"Gemini fallback {fallback} failed: {fb_e}") from fb_e
            except ProviderError:
                raise
            except Exception:
                # if fallback lookup crashes, fall through to raising the original error
                pass

        raise ProviderError(f"Gemini generate failed: {last_err}") from last_err

    # ---- response parsing (defensive; SDK shapes vary across versions) ----

    @staticmethod
    def _extract_text(resp) -> str:
        # Preferred convenience accessor
        t = getattr(resp, "text", None)
        if t:
            return t
        # Fallback: walk candidates -> content.parts[].text
        out = []
        for cand in (getattr(resp, "candidates", None) or []):
            content = getattr(cand, "content", None)
            for part in (getattr(content, "parts", None) or []):
                pt = getattr(part, "text", None)
                if pt:
                    out.append(pt)
        return "\n".join(out)

    @staticmethod
    def _extract_sources(resp) -> list[str]:
        urls: list[str] = []
        for cand in (getattr(resp, "candidates", None) or []):
            gm = getattr(cand, "grounding_metadata", None)
            for chunk in (getattr(gm, "grounding_chunks", None) or []):
                web = getattr(chunk, "web", None)
                uri = getattr(web, "uri", None)
                if uri and uri not in urls:
                    urls.append(uri)
        return urls

    @staticmethod
    def _is_retryable(e: Exception) -> bool:
        s = f"{type(e).__name__} {e}".lower()
        return any(k in s for k in
                   ("429", "rate", "quota", "resource_exhausted", "503", "unavailable",
                    "500", "internal", "timeout", "deadline"))
