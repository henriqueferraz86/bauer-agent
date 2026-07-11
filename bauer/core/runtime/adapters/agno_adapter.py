"""Agno runtime adapter MVP."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..agent_spec import AgentSpec, agent_spec_from_mapping, agno_agent_spec_from_bauer
from .base import RuntimeAdapterError


class AgnoRuntimeAdapter:
    """Adapter that runs Bauer agent requests through the Agno Python SDK."""

    name = "agno"

    def __init__(self, config: Any | None = None, adapter_config: dict[str, Any] | None = None):
        self.config = config
        self.adapter_config = adapter_config or self._adapter_config_from(config)
        self.mode = str(self.adapter_config.get("mode") or "sdk").strip().lower()
        self.base_url = str(self.adapter_config.get("base_url") or "http://localhost:7777")
        self.timeout_s = int(self.adapter_config.get("timeout_s") or self.adapter_config.get("timeout_seconds") or 120)
        self.db_file = Path(str(self.adapter_config.get("db_file") or "memory/runtime/agno/sessions.db"))
        self.workspace = Path(str(self.adapter_config.get("workspace") or "workspace"))
        self.tool_context = str(self.adapter_config.get("tool_context") or "worker")
        self.tool_policy_path = self.adapter_config.get("tool_policy_path")
        self._agents: dict[str, Any] = {}
        self._runs: dict[str, dict[str, Any]] = {}

    def create_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize_spec(spec)
        agent_id = str(normalized.get("id") or normalized.get("name") or f"agno-agent-{uuid4()}")
        agent = self._build_agent(spec={**normalized, "id": agent_id})
        self._agents[agent_id] = agent
        return {
            "status": "created",
            "runtime_adapter": self.name,
            "agent_id": agent_id,
            "mode": self.mode,
            "spec": normalized,
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
                self._runs[run_id] = event
                return event

        result = {
            "status": "completed",
            "event": "run.completed",
            "run_id": run_id,
            "runtime_adapter": self.name,
            "output": "".join(chunks) or str(last_event.get("output") or ""),
            "metadata": last_event.get("metadata", {}),
        }
        self._runs[run_id] = result
        return result

    def stream_agent(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        run_id = str(request.get("run_id") or f"run-{uuid4()}")
        session_id = str(request.get("session_id") or f"session-{uuid4()}")
        user_id = str(request.get("user_id") or "local-user")

        yield {
            "event": "run.started",
            "status": "running",
            "run_id": run_id,
            "session_id": session_id,
            "runtime_adapter": self.name,
            "mode": self.mode,
        }

        try:
            agent = self._agent_for_request(request, session_id=session_id, user_id=user_id)
            prompt = self._prompt_from_request(request)
            final_content = ""
            agno_run_id = None
            tools: list[dict[str, Any]] = []
            for event in agent.run(prompt, stream=True, run_id=run_id, session_id=session_id, user_id=user_id):
                agno_run_id = getattr(event, "run_id", agno_run_id)
                content = getattr(event, "content", None)
                if content:
                    final_content += str(content)
                    yield {
                        "event": "message.delta",
                        "status": "running",
                        "run_id": run_id,
                        "session_id": session_id,
                        "runtime_adapter": self.name,
                        "content": str(content),
                    }
                event_tools = getattr(event, "tools", None)
                if event_tools:
                    tools.extend(self._serialize_tools(event_tools))
        except Exception as exc:  # noqa: BLE001
            failed = {
                "event": "run.failed",
                "status": "failed",
                "run_id": run_id,
                "session_id": session_id,
                "runtime_adapter": self.name,
                "error": str(exc),
            }
            self._runs[run_id] = failed
            yield failed
            return

        completed = {
            "event": "run.completed",
            "status": "completed",
            "run_id": run_id,
            "session_id": session_id,
            "runtime_adapter": self.name,
            "output": final_content,
            "metadata": {
                "agno_run_id": agno_run_id or run_id,
                "mode": self.mode,
                "tools": tools,
            },
        }
        self._runs[run_id] = completed
        yield completed

    def stop_run(self, run_id: str) -> dict[str, Any]:
        return {
            "status": "unsupported",
            "run_id": run_id,
            "runtime_adapter": self.name,
            "message": "Agno SDK runs are synchronous in the MVP adapter.",
        }

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._runs.get(
            run_id,
            {
                "status": "unknown",
                "run_id": run_id,
                "runtime_adapter": self.name,
            },
        )

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        try:
            db = self._build_db()
            raw_sessions = db.get_sessions(limit=100)
        except Exception:
            raw_sessions = []

        if isinstance(raw_sessions, tuple):
            raw_sessions = raw_sessions[0]

        for session in raw_sessions or []:
            data = self._object_to_dict(session)
            sessions.append(
                {
                    "id": data.get("session_id") or data.get("id"),
                    "user_id": data.get("user_id"),
                    "agent_id": data.get("agent_id") or data.get("component_id"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "runtime_adapter": self.name,
                }
            )
        return sessions

    def healthcheck(self) -> dict[str, Any]:
        """Verifica se o SDK do Agno está importável (sem tocar a rede)."""
        try:
            self._require_agno()
        except RuntimeAdapterError as exc:
            return {"status": "unhealthy", "runtime_adapter": self.name,
                    "mode": self.mode, "error": str(exc)}
        return {"status": "healthy", "runtime_adapter": self.name, "mode": self.mode}

    @classmethod
    def from_config(cls, config: Any | None = None) -> "AgnoRuntimeAdapter":
        return cls(config=config)

    @staticmethod
    def _adapter_config_from(config: Any | None) -> dict[str, Any]:
        runtime = getattr(config, "runtime", None)
        adapters = getattr(runtime, "adapters", {}) or {}
        raw = adapters.get("agno", {}) if isinstance(adapters, dict) else {}
        return dict(raw)

    def _agent_for_request(self, request: dict[str, Any], *, session_id: str, user_id: str) -> Any:
        agent_id = str(request.get("agent_id") or request.get("agent", "")).strip()
        if agent_id and agent_id in self._agents:
            return self._agents[agent_id]

        spec = self._normalize_spec(request.get("agent_spec") or {})
        if agent_id:
            spec.setdefault("id", agent_id)
        spec.setdefault("name", agent_id or "Agno Bauer Agent")
        spec.setdefault("session_id", session_id)
        spec.setdefault("user_id", user_id)
        agent = self._build_agent(spec)
        if agent_id:
            self._agents[agent_id] = agent
        return agent

    def _build_agent(self, spec: dict[str, Any]) -> Any:
        Agent, _, _ = self._require_agno()
        instructions = self._instructions_from_spec(spec)
        return Agent(
            id=str(spec.get("id") or spec.get("name") or f"agno-agent-{uuid4()}"),
            name=str(spec.get("name") or "Agno Bauer Agent"),
            model=spec.get("agno_model") or OfflineAgnoModel(id=str(spec.get("model") or "offline-echo")),
            db=self._build_db(),
            tools=self._map_tools(spec.get("tools")),
            user_id=str(spec.get("user_id") or "local-user"),
            session_id=str(spec.get("session_id") or f"session-{uuid4()}"),
            instructions=instructions or None,
            add_history_to_context=True,
            num_history_runs=int(spec.get("num_history_runs") or 3),
        )

    def _build_db(self) -> Any:
        _, SqliteDb, _ = self._require_agno()
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        return SqliteDb(db_file=str(self.db_file))

    def _require_agno(self) -> tuple[Any, Any, Any]:
        try:
            from agno.agent import Agent
            from agno.db.sqlite import SqliteDb
            from agno.models.response import ModelResponse
        except ModuleNotFoundError as exc:
            raise RuntimeAdapterError("Agno adapter requires 'agno' and 'sqlalchemy' installed in the environment.") from exc
        return Agent, SqliteDb, ModelResponse

    @staticmethod
    def _instructions_from_spec(spec: dict[str, Any]) -> list[str]:
        instructions: list[str] = []
        for key in ("system", "system_prompt", "instructions"):
            value = spec.get(key)
            if isinstance(value, str) and value.strip():
                instructions.append(value.strip())
            elif isinstance(value, list):
                instructions.extend(str(item) for item in value if str(item).strip())
        return instructions

    def _map_tools(self, tools: Any) -> list[Any]:
        if not tools:
            return []
        mapped: list[Any] = []
        for tool in tools:
            if callable(tool):
                mapped.append(tool)
            elif isinstance(tool, dict) and callable(tool.get("callable")):
                mapped.append(tool["callable"])
            elif isinstance(tool, dict) and callable(tool.get("function")):
                mapped.append(tool["function"])
            elif isinstance(tool, str):
                mapped_tool = self._bauer_tool_callable(tool)
                if mapped_tool is not None:
                    mapped.append(mapped_tool)
        return mapped

    def _bauer_tool_callable(self, tool_name: str) -> Any | None:
        router = self._build_tool_router()
        normalized = tool_name.strip()

        if normalized == "read_file":
            def read_file(path: str, offset: int = 1, limit: int = 0) -> str:
                args = {"path": path, "offset": offset}
                if limit:
                    args["limit"] = limit
                return router.execute_native_call("read_file", args)
            return read_file

        if normalized == "write_file":
            def write_file(path: str, content: str, overwrite: bool = False) -> str:
                return router.execute_native_call("write_file", {"path": path, "content": content, "overwrite": overwrite})
            return write_file

        if normalized == "list_dir":
            def list_dir(path: str = ".") -> str:
                return router.execute_native_call("list_dir", {"path": path})
            return list_dir

        if normalized == "run_command":
            def run_command(command: str, confirm: bool = False, background: bool = False) -> str:
                return router.execute_native_call(
                    "run_command",
                    {"command": command, "confirm": confirm, "background": background},
                )
            return run_command

        if normalized == "web_search":
            def web_search(query: str, max_results: int = 5) -> str:
                return router.execute_native_call("web_search", {"query": query, "max_results": max_results})
            return web_search

        if normalized == "memory":
            def memory(action: str, key: str = "", value: str = "") -> str:
                args = {"action": action}
                if key:
                    args["key"] = key
                if value:
                    args["value"] = value
                return router.execute_native_call("memory", args)
            return memory

        return None

    def _build_tool_router(self) -> Any:
        from bauer.shell_runner import ShellRunner
        from bauer.tool_router import ToolRouter

        shell_runner = ShellRunner(workspace=self.workspace, safe_mode=True, timeout=self.timeout_s)
        return ToolRouter(
            workspace=self.workspace,
            shell_runner=shell_runner,
            web_enabled=True,
            max_tool_calls=500,
            tool_context=self.tool_context,
            tool_policy_path=self.tool_policy_path,
            policy_enabled=True,
            policy_rules_path=self.adapter_config.get("policy_rules_path"),
            policy_root=self.adapter_config.get("policy_root", "memory/runtime"),
            tool_allowlist=["read_file", "write_file", "list_dir", "run_command", "web_search", "memory"],
        )

    @staticmethod
    def _normalize_spec(spec: Any) -> dict[str, Any]:
        if isinstance(spec, AgentSpec):
            return agno_agent_spec_from_bauer(spec)
        if isinstance(spec, dict):
            normalized = agno_agent_spec_from_bauer(agent_spec_from_mapping(spec))
            for key in ("agno_model", "num_history_runs", "session_id", "user_id"):
                if key in spec:
                    normalized[key] = spec[key]
            if any(not isinstance(tool, str) for tool in spec.get("tools", []) or []):
                normalized["tools"] = spec.get("tools", [])
            return normalized
        return {}

    @staticmethod
    def _prompt_from_request(request: dict[str, Any]) -> str:
        messages = request.get("messages")
        if isinstance(messages, list):
            parts = [
                str(message.get("content", ""))
                for message in messages
                if isinstance(message, dict) and message.get("role") != "system" and message.get("content")
            ]
            if parts:
                return "\n".join(parts)
        value = request.get("task", request.get("input", ""))
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @staticmethod
    def _serialize_tools(tools: Any) -> list[dict[str, Any]]:
        serialized = []
        for tool in tools:
            data = AgnoRuntimeAdapter._object_to_dict(tool)
            serialized.append(
                {
                    "tool_name": data.get("tool_name") or data.get("name"),
                    "result": data.get("result"),
                    "status": data.get("status"),
                }
            )
        return serialized

    @staticmethod
    def _object_to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if hasattr(value, "model_dump"):
            return dict(value.model_dump())
        if hasattr(value, "__dict__"):
            return dict(value.__dict__)
        return {}


class OfflineAgnoModel:  # pragma: no cover - exercised through Agno integration tests
    """Small local Agno model for SDK smoke tests without provider credentials."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        from agno.models.base import Model
        from agno.models.message import Message
        from agno.models.response import ModelResponse

        class _OfflineAgnoModel(Model):
            def invoke(self, *invoke_args: Any, **invoke_kwargs: Any) -> ModelResponse:
                messages = invoke_kwargs.get("messages") or []
                if messages and messages[-1].role == "tool":
                    return ModelResponse(role="assistant", content=f"tool result received: {messages[-1].content}")
                return ModelResponse(role="assistant", content=self._echo(messages))

            async def ainvoke(self, *invoke_args: Any, **invoke_kwargs: Any) -> ModelResponse:
                return self.invoke(*invoke_args, **invoke_kwargs)

            def invoke_stream(self, *invoke_args: Any, **invoke_kwargs: Any) -> Iterator[ModelResponse]:
                response = self.invoke(*invoke_args, **invoke_kwargs)
                content = str(response.content or "")
                for index in range(0, len(content), 18):
                    yield ModelResponse(role="assistant", content=content[index : index + 18])

            async def ainvoke_stream(self, *invoke_args: Any, **invoke_kwargs: Any) -> Any:
                for chunk in self.invoke_stream(*invoke_args, **invoke_kwargs):
                    yield chunk

            def _parse_provider_response(self, response: Any, **parse_kwargs: Any) -> ModelResponse:
                if isinstance(response, ModelResponse):
                    return response
                return ModelResponse(role="assistant", content=str(response))

            def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
                return self._parse_provider_response(response)

            @staticmethod
            def _echo(messages: list[Message]) -> str:
                visible = [
                    f"{message.role}:{message.content}"
                    for message in messages
                    if message.role in {"user", "assistant", "tool"} and message.content
                ]
                return " | ".join(visible[-4:])

        return _OfflineAgnoModel(*args, **kwargs)
