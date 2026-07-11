"""Native Bauer runtime adapter."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from .base import RuntimeAdapterError


class BauerNativeRuntimeAdapter:
    """Adapter for Bauer's current local Python execution path."""

    name = "bauer_native"

    def create_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(spec.get("id") or spec.get("name") or f"agent-{uuid4()}")
        return {
            "status": "created",
            "runtime_adapter": self.name,
            "agent_id": agent_id,
            "spec": dict(spec),
        }

    def run_agent(self, request: dict[str, Any]) -> dict[str, Any]:
        run_id = str(request.get("run_id") or f"run-{uuid4()}")
        chunks: list[str] = []
        last_event: dict[str, Any] = {}
        for event in self.stream_agent({**request, "run_id": run_id}):
            last_event = event
            if event.get("event") == "message.delta":
                chunks.append(str(event.get("content", "")))
            elif event.get("event") == "run.failed":
                return event
        return {
            "status": "completed",
            "event": "run.completed",
            "run_id": run_id,
            "runtime_adapter": self.name,
            "output": "".join(chunks),
            "metadata": last_event.get("metadata", {}),
        }

    def stream_agent(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        run_id = str(request.get("run_id") or f"run-{uuid4()}")
        yield {
            "event": "run.started",
            "status": "running",
            "run_id": run_id,
            "runtime_adapter": self.name,
        }

        client = request.get("client")
        if client is None or not hasattr(client, "chat_stream"):
            raise RuntimeAdapterError("BauerNativeRuntimeAdapter requires a client with chat_stream().")

        model = str(request.get("model") or request.get("model_name") or "")
        messages = request.get("messages")
        if not isinstance(messages, list):
            task = str(request.get("task") or request.get("input") or "")
            if not task:
                raise RuntimeAdapterError("Native runtime request requires messages or task.")
            messages = [{"role": "user", "content": task}]

        try:
            for chunk in client.chat_stream(model, messages):
                yield {
                    "event": "message.delta",
                    "status": "running",
                    "run_id": run_id,
                    "runtime_adapter": self.name,
                    "content": chunk,
                }
        except Exception as exc:  # noqa: BLE001
            yield {
                "event": "run.failed",
                "status": "failed",
                "run_id": run_id,
                "runtime_adapter": self.name,
                "error": str(exc),
            }
            return

        yield {
            "event": "run.completed",
            "status": "completed",
            "run_id": run_id,
            "runtime_adapter": self.name,
        }

    def stop_run(self, run_id: str) -> dict[str, Any]:
        return {
            "status": "unsupported",
            "run_id": run_id,
            "runtime_adapter": self.name,
            "message": "In-process native runs cannot be stopped through the Sprint 1 adapter yet.",
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        return {
            "status": "unknown",
            "run_id": run_id,
            "runtime_adapter": self.name,
        }

    def list_sessions(self) -> list[dict[str, Any]]:
        return []

    def healthcheck(self) -> dict[str, Any]:
        """Runtime in-process: saudável por definição se o import funcionou."""
        return {"status": "healthy", "runtime_adapter": self.name, "mode": "in-process"}
