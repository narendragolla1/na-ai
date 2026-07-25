"""Cross-platform process-group lifecycle for backend subprocesses.

POSIX (Linux/macOS) puts the launched server into its own session via
``start_new_session=True`` and signals the whole tree with ``killpg`` — this
is how a tensor-parallel vLLM/SGLang launch and its worker processes all
stop together. Windows has no process groups or POSIX signals; the nearest
equivalents are ``CREATE_NEW_PROCESS_GROUP`` (lets ``CTRL_BREAK_EVENT`` reach
the console group) for a graceful stop, and ``taskkill /T`` for a hard kill
that walks the same process tree.

The Windows-only constants are read via ``getattr`` with their real numeric
values as fallback, so this module imports and unit-tests cleanly on
Linux/macOS even though the constants only exist in the stdlib on Windows.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"

# Real values from the Windows API / CPython's signal module; used as
# getattr fallbacks so this file is importable and unit-testable on POSIX.
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CTRL_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", 1)


def new_group_popen_kwargs() -> dict:
    """Popen kwargs that put the child (and any children it spawns) into
    its own process group, so the whole tree can be signaled together."""
    if IS_WINDOWS:
        return {"creationflags": _CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def terminate_group(process: subprocess.Popen) -> None:
    """Ask the backend's whole process group to shut down gracefully."""
    if IS_WINDOWS:
        with contextlib.suppress(ProcessLookupError, OSError):
            process.send_signal(_CTRL_BREAK_EVENT)
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:  # pragma: no cover - non-POSIX fallback
            process.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        pass


def kill_group(process: subprocess.Popen) -> None:
    """Force-kill the backend's whole process group after a graceful
    shutdown timed out."""
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            process.send_signal(signal.SIGKILL)
    except ProcessLookupError:
        pass
