"""ModelEngine: factory facade over the serving backends.

The engine owns the backend subprocess lifecycle and exposes a single async
OpenAI-compatible client interface (``chat``) plus a managed LoRA lifecycle
(load / unload / activate / rollback via :class:`~omniai.engine.lora.LoRARegistry`),
so the rest of the framework never touches backend-specific details.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx

from omniai.engine.backends import ADAPTERS, BackendAdapter
from omniai.engine.config import EngineConfig
from omniai.engine.lora import LoRARegistry
from omniai.engine.resilience import (
    CircuitBreaker,
    EngineSupervisor,
    EngineUnavailable,
    with_retries,
)
from omniai.logging import EngineError, ErrorMessages, Suggestions, get_logger, trace_operation
from omniai.telemetry import traced_span

logger = get_logger(__name__)


class ModelEngine:
    """Unified serving facade. Create via :meth:`ModelEngine.create`."""

    def __init__(
        self,
        config: EngineConfig,
        adapter: BackendAdapter,
        lora_registry: LoRARegistry | None = None,
    ):
        self.config = config
        self.adapter = adapter
        self.system_prompt: str | None = None
        self.lora = lora_registry or LoRARegistry()
        self.breaker = CircuitBreaker(
            failure_threshold=config.breaker_failure_threshold,
            reset_timeout=config.breaker_reset_s,
        )
        self.supervisor: EngineSupervisor | None = None
        # Observability hook: called with (prompt_tokens, completion_tokens).
        self.on_usage: Any = None
        self.in_flight = 0
        self._client: httpx.AsyncClient | None = None
        self._semaphore: asyncio.Semaphore | None = None

    @property
    def semaphore(self) -> asyncio.Semaphore:
        # Created lazily so the engine can be constructed outside a loop.
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)
        return self._semaphore

    @classmethod
    def create(cls, config: EngineConfig | dict[str, Any], **kwargs: Any) -> ModelEngine:
        """Factory: build the engine with the adapter for ``config.backend``."""
        if isinstance(config, dict):
            config = EngineConfig(**config)

        logger.info(f"🔧 Creating ModelEngine | backend={config.backend_name} | model={config.model}")

        adapter_cls = ADAPTERS.get(config.backend_name)
        if adapter_cls is None:
            error = EngineError(
                ErrorMessages.UNSUPPORTED_MODEL_TYPE.format(model_type=config.backend_name),
                context=f"Unknown backend: {config.backend_name}",
                suggestion=f"Supported backends: {', '.join(ADAPTERS.keys())}",
            )
            logger.error(str(error))
            raise ValueError(str(error))

        logger.debug(f"✅ ModelEngine created successfully")
        return cls(config, adapter_cls(config), **kwargs)

    @property
    def active_lora(self) -> str | None:
        return self.lora.active

    @property
    def active_lora_path(self) -> str | None:
        """Path of the active adapter, or None on the base model.

        Kept alongside :attr:`active_lora` for the supervisor's restart path,
        which re-applies both without reaching into the registry directly.
        """
        if self.lora.active is None:
            return None
        return self.lora.loaded[self.lora.active].path

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url, timeout=self.config.request_timeout_s
            )
        return self._client

    async def start(
        self, wait: bool = True, timeout: float = 300.0, supervise: bool = False
    ) -> None:
        """Launch (or attach to) the backend; optionally supervise it.

        In unmanaged mode (``config.managed=False``) no subprocess is
        spawned — the engine attaches to the server at
        ``config.external_base_url`` and only health-checks it.
        """
        mode = "unmanaged (external)" if not self.config.managed else "managed (local)"
        with trace_operation(logger, f"Starting engine ({mode})", {"timeout": f"{timeout}s", "supervise": supervise}):
            if not self.config.managed:
                logger.info(f"📍 Connecting to external engine at {self.config.base_url}")
                if wait and not await self.adapter.wait_ready(timeout=timeout):
                    error = EngineError(
                        ErrorMessages.ENGINE_UNAVAILABLE,
                        context=f"External engine at {self.config.base_url} not responding",
                        suggestion=Suggestions.CHECK_CONNECTIVITY,
                    )
                    logger.error(str(error))
                    raise EngineUnavailable(str(error)) from None
                logger.info(f"✅ Connected to external engine at {self.config.base_url}")
                return

            logger.info(f"🚀 Spawning {self.config.backend_name} backend process on port {self.config.port}")
            try:
                self.adapter.start()
                logger.debug(f"📊 Backend process spawned (PID: {self.adapter.process.pid if self.adapter.process else 'unknown'})")
            except Exception as exc:
                error = EngineError(
                    ErrorMessages.PROCESS_SPAWN_FAILED,
                    context=str(exc),
                    suggestion=Suggestions.CHECK_DEVICE_ID,
                )
                logger.error(str(error))
                raise

            if wait:
                logger.info(f"⏳ Waiting for backend to be ready (timeout: {timeout}s)")
                ready = await self.adapter.wait_ready(timeout=timeout)
                if not ready:
                    self.adapter.stop()
                    error = EngineError(
                        ErrorMessages.ENGINE_TIMEOUT.format(timeout=timeout),
                        context=f"{self.config.backend_name} server failed to respond to health checks",
                        suggestion=Suggestions.CHECK_GPU,
                    )
                    logger.error(str(error))
                    raise EngineUnavailable(str(error)) from None
                logger.info(f"✅ Backend is ready and responsive")

            if supervise:
                logger.info(f"👁️  Starting process supervisor (max_restarts: {self.config.supervisor_max_restarts})")
                self.supervisor = EngineSupervisor(self, max_restarts=self.config.supervisor_max_restarts)
                self.supervisor.start()
                logger.debug(f"✅ Supervisor started")

    async def stop(self) -> None:
        """Stop the engine and clean up resources."""
        logger.info("🛑 Stopping engine")

        if self.supervisor is not None:
            logger.debug("Stopping supervisor")
            await self.supervisor.stop()
            self.supervisor = None

        if self.config.managed:
            logger.info("Terminating backend process")
            self.adapter.stop()
            logger.debug("Backend process terminated")

        if self._client is not None:
            logger.debug("Closing HTTP client")
            await self._client.aclose()
            self._client = None

        logger.info("✅ Engine stopped")

    async def health(self) -> dict[str, Any]:
        """Liveness of the managed process and the HTTP endpoint."""
        logger.debug("📊 Checking engine health")
        server_ok = False
        try:
            resp = await self.client.get("/health")
            server_ok = resp.status_code == 200
        except httpx.HTTPError as exc:
            logger.debug(f"⚠️  Health check failed: {type(exc).__name__}")

        health_status = {
            "process": self.adapter.is_alive(),
            "server": server_ok,
            "active_lora": self.active_lora,
        }

        # Log health summary
        status_emoji = "✅" if all(health_status.values()) else "⚠️"
        logger.debug(
            f"{status_emoji} Engine health: process={health_status['process']} | "
            f"server={health_status['server']} | active_lora={health_status['active_lora']}"
        )
        return health_status

    async def warmup(self) -> bool:
        """One tiny generation so CUDA graphs/caches are primed before real
        traffic; returns False instead of raising on failure."""
        try:
            await self.chat_text([{"role": "user", "content": "ping"}], max_tokens=1)
            return True
        except (httpx.HTTPError, KeyError):
            return False

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        """POST with retry + circuit breaker; raises EngineUnavailable when down."""
        logger.debug(f"📤 Posting to {path} | in_flight={self.in_flight}")

        async def attempt() -> httpx.Response:
            resp = await self.client.post(path, json=payload)
            resp.raise_for_status()
            return resp

        async def guarded() -> httpx.Response:
            return await with_retries(attempt, attempts=self.config.retries)

        try:
            async with self.semaphore:  # backpressure toward the backend
                self.in_flight += 1
                logger.debug(f"📊 In-flight requests: {self.in_flight}")
                try:
                    response = await self.breaker.call(guarded)
                    logger.debug(f"✅ Request succeeded | status={response.status_code}")
                    return response
                finally:
                    self.in_flight -= 1
        except EngineUnavailable as exc:
            logger.error(f"❌ Engine unavailable: {exc}")
            raise
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                logger.warning(f"⚠️  Client error: {exc.response.status_code} {exc}")
                raise  # client errors are the caller's bug, not availability
            logger.error(f"❌ Network error: {type(exc).__name__}: {exc}")
            error = EngineError(
                ErrorMessages.ENGINE_UNAVAILABLE,
                context=f"Request to {path} failed: {type(exc).__name__}",
                suggestion=Suggestions.RETRY_LATER,
            )
            raise EngineUnavailable(str(error)) from exc

    def set_system_prompt(self, prompt: str) -> None:
        """Pre-cache a system prompt prepended to every conversation.

        With SGLang this shared prefix is served from the RadixAttention
        cache, so long skill prompts cost prefill only once.
        """
        self.system_prompt = prompt

    def _build_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        if self.system_prompt and (not messages or messages[0].get("role") != "system"):
            return [{"role": "system", "content": self.system_prompt}, *messages]
        return list(messages)

    async def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """OpenAI-compatible chat completion against the live backend."""
        payload: dict[str, Any] = {
            "model": self.active_lora or self.config.model,
            "messages": self._build_messages(messages),
            **kwargs,
        }
        with traced_span(
            "engine.chat", {"model": payload["model"], "backend": self.config.backend_name}
        ) as span:
            resp = await self._post("/v1/chat/completions", payload)
            data = resp.json()
            usage = data.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            span.set_attributes(
                {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
            )
            if self.on_usage is not None:
                self.on_usage(prompt_tokens, completion_tokens)
            return data

    async def chat_text(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Convenience wrapper returning just the first choice's content."""
        data = await self.chat(messages, **kwargs)
        return data["choices"][0]["message"]["content"]

    async def stream_chat(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> AsyncIterator[str]:
        """Stream completion deltas as they arrive (SSE)."""
        payload: dict[str, Any] = {
            "model": self.active_lora or self.config.model,
            "messages": self._build_messages(messages),
            "stream": True,
            **kwargs,
        }
        async with self.client.stream("POST", "/v1/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line.removeprefix("data:").strip()
                if chunk == "[DONE]":
                    break
                import json

                delta = json.loads(chunk)["choices"][0].get("delta", {})
                if content := delta.get("content"):
                    yield content

    # -- LoRA lifecycle ------------------------------------------------------

    async def load_lora_adapter(self, name: str, path: str, activate: bool = True) -> bool:
        """Load an adapter into the running server, zero downtime.

        When the server's adapter slots (``config.max_loras``) are full, the
        oldest loaded adapter that is neither active nor the rollback target
        is evicted first, so the continuous-learning loop never stalls on a
        full server.
        """
        victim = self.lora.eviction_candidate(self.config.max_loras)
        if victim is not None:
            await self.unload_lora_adapter(victim)
        with traced_span("engine.load_lora", {"adapter": name}):
            await self._post(
                self.adapter.lora_load_endpoint(), self.adapter.lora_load_payload(name, path)
            )
        self.lora.register(name, path)
        if activate:
            self.lora.activate(name)
        return True

    async def unload_lora_adapter(self, name: str) -> bool:
        """Remove an adapter from the server and the registry."""
        with traced_span("engine.unload_lora", {"adapter": name}):
            await self._post(
                self.adapter.lora_unload_endpoint(), self.adapter.lora_unload_payload(name)
            )
        self.lora.remove(name)
        return True

    async def rollback_lora(self) -> str | None:
        """Reactivate the previously active adapter (or the base model).

        Returns the adapter now active, or None when back on the base model.
        The rolled-back-from adapter stays loaded, so rolling forward again
        is equally cheap.
        """
        target = self.lora.previous
        if target is None:
            self.lora.deactivate()
            return None
        self.lora.activate(target)
        return target

    async def reapply_active_lora(self) -> bool:
        """Re-load the registry's active adapter into a restarted server."""
        if self.lora.active is None:
            return False
        record = self.lora.loaded[self.lora.active]
        await self._post(
            self.adapter.lora_load_endpoint(),
            self.adapter.lora_load_payload(record.name, record.path),
        )
        return True
