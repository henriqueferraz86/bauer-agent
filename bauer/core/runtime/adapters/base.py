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
