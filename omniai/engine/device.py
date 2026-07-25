"""Best-effort hardware detection for picking a default serving accelerator.

This only informs a *default* choice of accelerator per OS (NVIDIA CUDA on
Linux/Windows, Apple Silicon on macOS, otherwise CPU) — it never overrides
an explicit ``EngineConfig.devices`` or ``extra_args`` the caller set.
Detection is dependency-free (no ``torch`` import) so it works in the
gateway process where torch isn't installed; backends that need torch
(vLLM, SGLang, training) still do their own authoritative device checks at
process start.

Caveat: as of this writing, neither vLLM nor SGLang has a stable Apple
Metal (MPS) backend, so :func:`detect_device_type` returning ``MPS`` is a
*hardware* fact, not a guarantee that the configured serving backend can
use it — callers should fall back to ``CPU``-appropriate backends (e.g.
llama.cpp server) on Apple Silicon until that changes.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from enum import StrEnum


class DeviceType(StrEnum):
    CUDA = "cuda"
    ROCM = "rocm"
    MPS = "mps"
    CPU = "cpu"


def _nvidia_gpu_present() -> bool:
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _rocm_gpu_present() -> bool:
    return shutil.which("rocm-smi") is not None


def _apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def detect_device_type() -> DeviceType:
    """Pick the best serving accelerator available on this host.

    Checked in order: NVIDIA CUDA, AMD ROCm, Apple Silicon (MPS), then CPU.
    """
    if _nvidia_gpu_present():
        return DeviceType.CUDA
    if _rocm_gpu_present():
        return DeviceType.ROCM
    if _apple_silicon():
        return DeviceType.MPS
    return DeviceType.CPU
