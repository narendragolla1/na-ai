"""Backend adapters: translate an EngineConfig into a concrete server launch.

Each adapter knows four things about its backend:
  1. how to build the CLI command that starts the server,
  2. what environment the server process needs (template method: the base
     class handles placement/log plumbing, subclasses add backend flags),
  3. how to health-check the OpenAI-compatible HTTP endpoint it exposes,
  4. how to hot-load and unload LoRA adapters through its management API.

New backends plug in via :func:`register_backend` without touching the
engine (open/closed): ``register_backend("tgi", TGIAdapter)``.
"""

from __future__ import annotations

import abc
import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import IO, Any

import httpx

from omniai.engine.config import EngineConfig
from omniai.logging import ProcessError, get_logger

logger = get_logger(__name__)


class BackendAdapter(abc.ABC):
    """Manages a serving backend as a background subprocess."""

    def __init__(self, config: EngineConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._log_file: IO[bytes] | None = None

    @abc.abstractmethod
    def build_command(self) -> list[str]:
        """CLI command that launches this backend with the mapped flags."""

    @abc.abstractmethod
    def lora_load_endpoint(self) -> str:
        """Path of the backend's dynamic LoRA-load API."""

    @abc.abstractmethod
    def lora_load_payload(self, name: str, path: str) -> dict[str, Any]:
        """Request body for loading a LoRA adapter."""

    @abc.abstractmethod
    def lora_unload_endpoint(self) -> str:
        """Path of the backend's dynamic LoRA-unload API."""

    @abc.abstractmethod
    def lora_unload_payload(self, name: str) -> dict[str, Any]:
        """Request body for unloading a LoRA adapter."""

    def _backend_env(self) -> dict[str, str]:
        """Backend-specific env vars; override in subclasses."""
        return {}

    def build_env(self) -> dict[str, str]:
        """Full environment for the server process.

        Inherits the parent env, then applies GPU placement
        (``config.devices`` -> CUDA_VISIBLE_DEVICES), backend-specific vars,
        and finally the user's ``config.env`` overrides — in that order, so
        the user always wins.
        """
        env = dict(os.environ)
        if self.config.devices is not None:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(d) for d in self.config.devices)
        env.update(self._backend_env())
        env.update(self.config.env)
        return env

    def _extra_flags(self) -> list[str]:
        flags: list[str] = []
        for key, value in self.config.extra_args.items():
            flag = f"--{key.replace('_', '-')}"
            if value is True:
                flags.append(flag)
            else:
                flags.extend([flag, str(value)])
        return flags

    def _open_log(self) -> int | IO[bytes]:
        if self.config.log_dir is None:
            return subprocess.DEVNULL
        log_dir = Path(self.config.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        # Held open for the subprocess's lifetime; closed in stop().
        self._log_file = open(  # noqa: SIM115
            log_dir / f"{self.config.backend_name}-{self.config.port}.log", "ab"
        )
        return self._log_file

    def start(self) -> subprocess.Popen:
        """Launch the backend server in its own process group.

        ``start_new_session=True`` puts the server and any tensor-parallel
        workers it spawns into one process group, so :meth:`stop` can kill
        the whole tree and release GPU memory reliably.
        """
        logger.info(f"🚀 Starting {self.config.backend_name} backend on port {self.config.port}")
        try:
            sink = self._open_log()
            cmd = self.build_command()
            logger.debug(f"📋 Command: {' '.join(cmd)}")
            self.process = subprocess.Popen(
                cmd,
                stdout=sink,
                stderr=subprocess.STDOUT if sink is not subprocess.DEVNULL else subprocess.DEVNULL,
                env=self.build_env(),
                start_new_session=True,
            )
            logger.info(f"✅ Backend process spawned | pid={self.process.pid}")
            return self.process
        except Exception as exc:
            logger.error(f"❌ Failed to start backend: {type(exc).__name__}: {exc}")
            raise ProcessError(
                f"Failed to spawn {self.config.backend_name} process",
                context=f"Port: {self.config.port}, Model: {self.config.model}",
                suggestion="Check system resources and model availability",
            ) from exc

    def _signal_group(self, sig: signal.Signals) -> None:
        assert self.process is not None
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(self.process.pid), sig)
            else:  # pragma: no cover - non-POSIX fallback
                self.process.send_signal(sig)
            logger.debug(f"📍 Sent signal {sig.name} to process group")
        except ProcessLookupError:
            logger.debug("⚠️  Process already terminated")

    def stop(self) -> None:
        logger.info("🛑 Stopping backend process")
        if self.process is not None:
            try:
                logger.debug(f"📍 Sending SIGTERM to process {self.process.pid}")
                self._signal_group(signal.SIGTERM)
                try:
                    self.process.wait(timeout=15)
                    logger.info("✅ Process terminated gracefully")
                except subprocess.TimeoutExpired:
                    logger.warning("⚠️  Process did not respond to SIGTERM, sending SIGKILL")
                    self._signal_group(signal.SIGKILL)
                    self.process.wait(timeout=5)
                    logger.info("✅ Process killed forcefully")
            except Exception as exc:
                logger.error(f"❌ Error stopping process: {type(exc).__name__}: {exc}")
            finally:
                self.process = None
        if self._log_file is not None:
            logger.debug("📋 Closing log file")
            self._log_file.close()
            self._log_file = None

    def is_alive(self) -> bool:
        """True while the server process exists and has not exited."""
        alive = self.process is not None and self.process.poll() is None
        logger.debug(f"📊 Backend alive check | alive={alive}")
        return alive

    async def wait_ready(self, timeout: float = 300.0, interval: float = 2.0) -> bool:
        """Poll the health endpoint until the server answers or timeout."""
        logger.info(
            f"⏳ Waiting for backend to be ready | timeout={timeout}s | interval={interval}s"
        )
        deadline = asyncio.get_event_loop().time() + timeout
        attempt = 0
        async with httpx.AsyncClient() as client:
            while asyncio.get_event_loop().time() < deadline:
                attempt += 1
                try:
                    resp = await client.get(f"{self.config.base_url}/health")
                    if resp.status_code == 200:
                        logger.info(f"✅ Backend is ready | attempts={attempt}")
                        return True
                except httpx.HTTPError as exc:
                    logger.debug(f"⏳ Health check attempt {attempt} failed: {type(exc).__name__}")
                await asyncio.sleep(interval)
        logger.error(f"❌ Backend did not become ready after {timeout}s | attempts={attempt}")
        return False


class VLLMAdapter(BackendAdapter):
    """Adapter for vLLM's OpenAI-compatible server (``vllm serve``)."""

    def _backend_env(self) -> dict[str, str]:
        env = {}
        if self.config.enable_lora:
            # Gate for the /v1/load_lora_adapter runtime API.
            env["VLLM_ALLOW_RUNTIME_LORA_UPDATING"] = "True"
            logger.debug("🔧 vLLM LoRA updates enabled")
        return env

    def build_command(self) -> list[str]:
        cfg = self.config
        logger.debug(
            f"🔧 Building vLLM command | model={cfg.model} | quantization={cfg.quantization}"
        )
        cmd = [
            "vllm",
            "serve",
            cfg.model,
            "--host",
            cfg.host,
            "--port",
            str(cfg.port),
        ]
        if cfg.quantization:
            cmd.extend(["--quantization", cfg.quantization])
            if cfg.quantization == "fp8":
                cmd.extend(["--kv-cache-dtype", "fp8"])
                logger.debug("🔧 vLLM fp8 quantization with fp8 KV cache")
        # PagedAttention is vLLM's native KV-cache layout; no flag needed,
        # but reject a request for a cache strategy vLLM cannot provide.
        if cfg.kv_cache not in (None, "paged_attention"):
            logger.error(f"❌ vLLM does not support kv_cache={cfg.kv_cache}")
            raise ValueError(f"vLLM does not support kv_cache={cfg.kv_cache!r}")
        if cfg.prefix_caching:
            cmd.append("--enable-prefix-caching")
            logger.debug("🔧 vLLM prefix caching enabled")
        if cfg.tensor_parallel_size > 1:
            cmd.extend(["--tensor-parallel-size", str(cfg.tensor_parallel_size)])
            logger.debug(f"🔧 vLLM tensor parallelism: {cfg.tensor_parallel_size}")
        if cfg.gpu_memory_utilization is not None:
            cmd.extend(["--gpu-memory-utilization", str(cfg.gpu_memory_utilization)])
            logger.debug(f"🔧 vLLM GPU memory: {cfg.gpu_memory_utilization:.1%}")
        if cfg.max_model_len is not None:
            cmd.extend(["--max-model-len", str(cfg.max_model_len)])
            logger.debug(f"🔧 vLLM max context length: {cfg.max_model_len}")
        if cfg.enable_lora:
            cmd.extend(["--enable-lora", "--max-loras", str(cfg.max_loras)])
            logger.debug(f"🔧 vLLM LoRA enabled | max_adapters={cfg.max_loras}")
        cmd.extend(self._extra_flags())
        return cmd

    def lora_load_endpoint(self) -> str:
        return "/v1/load_lora_adapter"

    def lora_load_payload(self, name: str, path: str) -> dict[str, Any]:
        return {"lora_name": name, "lora_path": path}

    def lora_unload_endpoint(self) -> str:
        return "/v1/unload_lora_adapter"

    def lora_unload_payload(self, name: str) -> dict[str, Any]:
        return {"lora_name": name}


class SGLangAdapter(BackendAdapter):
    """Adapter for SGLang's server (``python -m sglang.launch_server``)."""

    def build_command(self) -> list[str]:
        cfg = self.config
        cmd = [
            sys.executable,
            "-m",
            "sglang.launch_server",
            "--model-path",
            cfg.model,
            "--host",
            cfg.host,
            "--port",
            str(cfg.port),
        ]
        if cfg.quantization:
            cmd.extend(["--quantization", cfg.quantization])
        # RadixAttention (prefix caching) is SGLang's default; only an explicit
        # opt-out disables it.
        if cfg.kv_cache == "disable_radix" or not cfg.prefix_caching:
            cmd.append("--disable-radix-cache")
        if cfg.tensor_parallel_size > 1:
            cmd.extend(["--tp", str(cfg.tensor_parallel_size)])
        if cfg.gpu_memory_utilization is not None:
            cmd.extend(["--mem-fraction-static", str(cfg.gpu_memory_utilization)])
        if cfg.max_model_len is not None:
            cmd.extend(["--context-length", str(cfg.max_model_len)])
        if cfg.enable_lora:
            cmd.extend(["--enable-lora", "--max-loras-per-batch", str(cfg.max_loras)])
        cmd.extend(self._extra_flags())
        return cmd

    def lora_load_endpoint(self) -> str:
        return "/load_lora_adapter"

    def lora_load_payload(self, name: str, path: str) -> dict[str, Any]:
        return {"lora_name": name, "lora_path": path}

    def lora_unload_endpoint(self) -> str:
        return "/unload_lora_adapter"

    def lora_unload_payload(self, name: str) -> dict[str, Any]:
        return {"lora_name": name}


ADAPTERS: dict[str, type[BackendAdapter]] = {
    "vllm": VLLMAdapter,
    "sglang": SGLangAdapter,
}


def register_backend(name: str, adapter_cls: type[BackendAdapter]) -> None:
    """Plug a custom backend into the ModelEngine factory."""
    ADAPTERS[name] = adapter_cls
