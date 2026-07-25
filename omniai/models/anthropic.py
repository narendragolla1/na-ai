"""ChatModel adapter for the Anthropic Messages API.

Translates the canonical OpenAI-shaped history into Anthropic's format:
system turns become the ``system`` parameter, assistant tool calls become
``tool_use`` content blocks, and tool results become ``tool_result`` blocks
inside a user turn (consecutive same-role turns are merged, as the API
requires strict user/assistant alternation).
"""

from __future__ import annotations

from typing import Any

import httpx

from omniai.engine.resilience import with_retries
from omniai.graph.tools import Tool
from omniai.logging import ModelError, get_logger
from omniai.models.base import ChatModel, ChatResult
from omniai.protocol import ToolCall

logger = get_logger(__name__)

API_VERSION = "2023-06-01"


def _to_anthropic(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split canonical messages into (system_prompt, anthropic_messages)."""
    logger.debug(f"🔄 Converting {len(messages)} messages to Anthropic format")

    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    tool_calls_count = 0

    for idx, message in enumerate(messages):
        try:
            role = message["role"]
            if role == "system":
                system_parts.append(message.get("content") or "")
                continue
            if role == "tool":
                blocks: list[dict[str, Any]] = [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.get("tool_call_id", ""),
                        "content": message.get("content") or "",
                    }
                ]
                converted.append({"role": "user", "content": blocks})
                continue
            if role == "assistant" and message.get("tool_calls"):
                tool_calls_count += len(message["tool_calls"])
                blocks = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for tc in message["tool_calls"]:
                    fn = tc.get("function", {})
                    import json

                    raw_args = fn.get("arguments") or "{}"
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": fn.get("name", ""),
                            "input": arguments,
                        }
                    )
                converted.append({"role": "assistant", "content": blocks})
                continue
            converted.append({"role": role, "content": message.get("content") or ""})
        except Exception as exc:
            logger.error(f"❌ Failed to convert message {idx}: {type(exc).__name__}: {exc}")
            raise

    # Merge consecutive same-role turns (strict alternation is required).
    merged: list[dict[str, Any]] = []
    for message in converted:
        if merged and merged[-1]["role"] == message["role"]:
            prev, curr = merged[-1]["content"], message["content"]
            as_blocks = lambda c: c if isinstance(c, list) else [{"type": "text", "text": c}]  # noqa: E731
            merged[-1]["content"] = as_blocks(prev) + as_blocks(curr)
        else:
            merged.append(dict(message))

    system = "\n\n".join(part for part in system_parts if part)
    logger.debug(
        f"✅ Converted to Anthropic format | merged_messages={len(merged)} | "
        f"system_prompt_len={len(system)} | tool_calls={tool_calls_count}"
    )
    return system, merged


class AnthropicChatModel(ChatModel):
    def __init__(
        self,
        model: str,
        api_key: str = "",
        base_url: str = "https://api.anthropic.com",
        max_tokens: int = 1024,
        timeout: float = 120.0,
        retries: int = 3,
        client: httpx.AsyncClient | None = None,
        **default_params: Any,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self.retries = retries
        self.default_params = default_params
        headers = {"anthropic-version": API_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        self.client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )
        # Injected clients (tests, custom transports) still need the
        # provider's mandatory headers.
        self.client.headers.update(headers)
        logger.info(
            f"🔧 Anthropic ChatModel initialized | model={model} | base_url={base_url} | "
            f"max_tokens={max_tokens} | retries={retries}"
        )

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[Tool] | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        model = kwargs.pop("model", self.model)
        max_tokens = kwargs.pop("max_tokens", self.max_tokens)

        system, converted = _to_anthropic(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": converted,
            **self.default_params,
            **kwargs,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.json_schema}
                for t in tools
            ]

        logger.debug(
            f"📤 Anthropic API request | model={model} | messages={len(converted)} | "
            f"tools={len(tools) if tools else 0} | max_tokens={max_tokens}"
        )

        async def attempt() -> httpx.Response:
            resp = await self.client.post("/v1/messages", json=payload)
            resp.raise_for_status()
            return resp

        try:
            response = await with_retries(attempt, attempts=self.retries)
            data = response.json()
            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=block.get("input") or {},
                        )
                    )
            usage = data.get("usage") or {}
            stop_reason = data.get("stop_reason")
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            tool_calls_count = len(tool_calls)

            logger.debug(
                f"✅ Anthropic | model={model} | reason={stop_reason} | "
                f"in={input_tokens} | out={output_tokens} | tools={tool_calls_count}"
            )

            return ChatResult(
                content="".join(text_parts),
                tool_calls=tool_calls,
                usage={
                    "prompt_tokens": usage.get("input_tokens", 0),
                    "completion_tokens": usage.get("output_tokens", 0),
                },
                stop_reason=data.get("stop_reason"),
                raw=data,
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"❌ Anthropic API error: {exc.response.status_code} | "
                f"message={exc.response.text[:200] if hasattr(exc.response, 'text') else str(exc)}"
            )
            raise ModelError(
                f"Anthropic API request failed with status {exc.response.status_code}",
                context=f"Model: {model}, Messages: {len(converted)}",
                suggestion="Check API key, rate limits, and model availability",
            ) from exc
        except Exception as exc:
            logger.error(f"❌ Anthropic API request failed: {type(exc).__name__}: {exc}")
            raise
