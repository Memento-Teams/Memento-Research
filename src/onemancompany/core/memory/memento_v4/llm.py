"""Sync LLM client used by the causal-memory finalize path.

Thin wrapper around the OpenAI SDK that points at any OpenAI-compatible
endpoint (OpenRouter, ppapi.ai, direct OpenAI, vLLM, etc.).

base_url / api_key precedence:
  1. constructor kwargs
  2. OPENROUTER_BASE_URL / OPENROUTER_API_KEY env vars
  3. OpenRouter public endpoint (no key default — must be provided)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    from openai import OpenAI
except Exception as exc:
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_IMPORT_ERROR = exc


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMMessage:
    content: list[dict[str, Any]]
    usage: LLMUsage | None = None


DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class LLMClient:
    """OpenAI-compatible sync client.

    Used for LLM-heavy ingest-time calls (finalize, classify, conflict detect,
    reflect). Bench QA/judge use a separate async httpx client (benchmarks.llm)
    because the existing benchmark harness is async.
    """

    def __init__(
        self,
        provider: str = "openai",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 1,
    ):
        """Construct the sync client.

        Args:
            timeout: per-request timeout in seconds. Default 60s is tight —
                prevents ppapi.ai / similar proxies from hanging the ingest
                pipeline. Raise for very-long context tasks.
            max_retries: how many times the OpenAI SDK should auto-retry
                before raising. Default 1 (so at most 2 attempts per call).
                Combined with the benchmark-level retry (3 attempts), a
                single failing call maxes out at ~6 tries over ~2 min.
        """
        if OpenAI is None:
            raise RuntimeError("openai package is not installed: pip install openai")
        if provider != "openai":
            raise ValueError(
                f"Only 'openai' provider supported in this build, got {provider}"
            )
        self.provider = provider
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = (
            base_url
            or os.environ.get("OPENROUTER_BASE_URL")
            or DEFAULT_BASE_URL
        )
        if not self._api_key:
            raise ValueError(
                "No API key — set OPENROUTER_API_KEY or pass api_key explicitly."
            )
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    # ── Primary API ─────────────────────────────────────────────────────────

    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        effort: str | None = None,
    ) -> LLMMessage:
        """Send a chat completion request. Returns normalized LLMMessage."""
        chat_messages: list[dict[str, Any]] = []
        if system:
            chat_messages.append({"role": "system", "content": system})
        chat_messages.extend(messages)

        params: dict[str, Any] = {
            "model": model,
            "messages": chat_messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
        }
        if tools:
            params["tools"] = tools
        if effort:
            # OpenAI reasoning-mode models (o1/o3/gpt-5) accept `reasoning_effort`.
            params["reasoning_effort"] = effort

        response = self._client.chat.completions.create(**params)
        choice = response.choices[0].message if response.choices else None

        # Coerce to a list-of-blocks shape so downstream code can treat
        # anthropic and openai responses uniformly.
        content_blocks: list[dict[str, Any]] = []
        if choice is not None:
            text = getattr(choice, "content", None) or ""
            if text:
                content_blocks.append({"type": "text", "text": text})

        usage_obj = getattr(response, "usage", None)
        usage = None
        if usage_obj is not None:
            usage = LLMUsage(
                input_tokens=getattr(usage_obj, "prompt_tokens", 0) or 0,
                output_tokens=getattr(usage_obj, "completion_tokens", 0) or 0,
            )
        return LLMMessage(content=content_blocks, usage=usage)
