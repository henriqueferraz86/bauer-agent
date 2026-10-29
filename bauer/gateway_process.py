"""Helpers for identifying the Bauer Gateway runtime process."""

from __future__ import annotations

from collections.abc import Sequence


_GATEWAY_COMMAND = ("-m", "bauer.cli", "gateway", "start")


def is_gateway_runtime_cmdline(command: Sequence[str]) -> bool:
    """Return whether a process command line is the foreground Gateway runtime."""
    args = tuple(str(part) for part in command)
    width = len(_GATEWAY_COMMAND)
    return any(
        args[index:index + width] == _GATEWAY_COMMAND
        for index in range(len(args) - width + 1)
    )


def is_gateway_runtime_process(pid: int) -> bool:
    """Check PID identity, not just existence (PIDs can be reused after restart)."""
    try:
        import psutil
    except ImportError:
        return False
    try:
        return is_gateway_runtime_cmdline(psutil.Process(pid).cmdline())
    except (psutil.Error, OSError, ValueError):
        return False
