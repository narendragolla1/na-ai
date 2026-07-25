"""ChatModel adapter for OpenAI and any OpenAI-compatible endpoint.

Covers api.openai.com, Azure-style gateways, and self-hosted OpenAI-clone
servers (vLLM, SGLang, Ollama, ...) — pass the matching ``base_url``.
"""

from __future__ import annotations

from typing import Any

import httpx

from omniai.engine.resilience import with_retries
from omniai.graph.tools import Tool
from omniai.logging import ModelError, get_logger
from omniai.models.base import ChatModel, ChatResult, parse_openai_tool_calls

logger = get_logger(__name__)


class OpenAIChatModel(ChatModel):
    def __init__(
        self,
        model: str,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 120.0,
        retries: int = 3,
        client: httpx.AsyncClient | None = None,
        **default_params: Any,
    ):
        self.model = model
        self.retries = retries
        self.default_params = default_params
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )
        # Injected clients (tests, custom transports) still get auth headers.
        self.client.headers.update(headers)
        logger.info(f"🔧 OpenAI ChatModel initialized | model={model} | base_url={base_url} | retries={retries}")

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        model = kwargs.pop("model", self.model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **self.default_params,
            **kwargs,
        }
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]

        logger.debug(
            f"📤 OpenAI API request | model={model} | messages={len(messages)} | tools={len(tools) if tools else 0}"
        )

        async def attempt() -> httpx.Response:
            resp = await self.client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            return resp

        try:
            response = await with_retries(attempt, attempts=self.retries)
            data = response.json()
            choice = data["choices"][0]
            message = choice.get("message", {})

            usage = data.get("usage") or {}
            stop_reason = choice.get("finish_reason")
            tool_calls = parse_openai_tool_calls(message)

            logger.debug(
                f"✅ OpenAI response | model={model} | stop_reason={stop_reason} | "
                f"prompt_tokens={usage.get('prompt_tokens', 0)} | completion_tokens={usage.get('completion_tokens', 0)} | "
                f"tool_calls={len(tool_calls)}"
            )

            return ChatResult(
                content=message.get("content") or "",
                tool_calls=tool_calls,
                usage=usage,
                stop_reason=stop_reason,
                raw=data,
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"❌ OpenAI API error: {exc.response.status_code} | "
                f"message={exc.response.text[:200] if hasattr(exc.response, 'text') else str(exc)}"
            )
            raise ModelError(
                f"OpenAI API request failed with status {exc.response.status_code}",
                context=f"Model: {model}, Messages: {len(messages)}",
                suggestion="Check API key, rate limits, and model availability",
            ) from exc
        except Exception as exc:
            logger.error(f"❌ OpenAI API request failed: {type(exc).__name__}: {exc}")
            raise
