"""Runtime adapter contract."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol


class RuntimeAdapterError(RuntimeError):
    """Raised when a runtime adapter cannot satisfy a request."""


class RuntimeAdapter(Protocol):
    """Common interface implemented by all Bauer runtime backends."""

    name: str

    def create_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        ...

    def run_agent(self, request: dict[str, Any]) -> dict[str, Any]:
        ...

    def stream_agent(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        ...

    def stop_run(self, run_id: str) -> dict[str, Any]:
        ...

    def get_run(self, run_id: str) -> dict[str, Any]:
        ...

    def list_sessions(self) -> list[dict[str, Any]]:
        ...


# ── Extensão OPCIONAL do contrato (Kernel Sprint 2) ───────────────────────────
# healthcheck/pause_run/resume_run NÃO entram no Protocol obrigatório —
# adapters existentes (e de terceiros) continuam válidos sem implementá-los.
# Use os helpers abaixo: degradam para "unsupported" quando o adapter não tem
# o método, em vez de AttributeError.


def adapter_healthcheck(adapter: Any) -> dict[str, Any]:
    """healthcheck() do adapter, ou um resultado neutro se não implementado."""
    fn = getattr(adapter, "healthcheck", None)
    if not callable(fn):
        return {"status": "unknown", "runtime_adapter": getattr(adapter, "name", "?"),
                "message": "adapter does not implement healthcheck()"}
    try:
        return dict(fn())
    except Exception as exc:  # noqa: BLE001 — health degradado é resultado, não crash
        return {"status": "unhealthy", "runtime_adapter": getattr(adapter, "name", "?"),
                "error": str(exc)}


def adapter_pause(adapter: Any, run_id: str) -> dict[str, Any]:
    """pause_run() do adapter, ou "unsupported" (o estado no Kernel ainda muda)."""
    fn = getattr(adapter, "pause_run", None)
    if not callable(fn):
        return {"status": "unsupported", "run_id": run_id,
                "runtime_adapter": getattr(adapter, "name", "?")}
    return dict(fn(run_id))


def adapter_resume(adapter: Any, run_id: str) -> dict[str, Any]:
    """resume_run() do adapter, ou "unsupported" (o estado no Kernel ainda muda)."""
    fn = getattr(adapter, "resume_run", None)
    if not callable(fn):
        return {"status": "unsupported", "run_id": run_id,
                "runtime_adapter": getattr(adapter, "name", "?")}
    return dict(fn(run_id))
