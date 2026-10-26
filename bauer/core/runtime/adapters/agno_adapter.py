"""Agno runtime adapter MVP."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..agent_spec import AgentSpec, agent_spec_from_mapping, agno_agent_spec_from_bauer
from .base import RuntimeAdapterError
from ....execution_control import checkpoint_execution
from ....provider_retry import ProviderRetryInterrupted


_ACTIVE_AGNO_SESSION_ID: ContextVar[str] = ContextVar("bauer_agno_session_id", default="")


def _in_agno_session(session_id: str, events: Iterator[Any]) -> Iterator[Any]:
    token = _ACTIVE_AGNO_SESSION_ID.set(session_id)
    try:
        yield from events
    finally:
        _ACTIVE_AGNO_SESSION_ID.reset(token)


def _checkpointed_events(events: Iterator[Any]) -> Iterator[Any]:
    """Check governance before asking the SDK stream to advance again."""
    iterator = iter(events)
    while True:
        checkpoint_execution()
        try:
            event = next(iterator)
        except StopIteration:
            # A tool may cancel while Agno is advancing internally and the SDK
            # may close its generator without a final event. Re-check before
            # treating that exhaustion as successful completion.
            checkpoint_execution()
            return
        # The SDK may have run a tool or advanced an LLM call while producing
        # this event. Do not let a terminal event mask cancellation raised
        # inside that same advancement.
        checkpoint_execution()
        yield event


def _agno_cancelled_exception(exc: Exception | None = None) -> Exception:
    """Use Agno's cancellation signal so a tool stop cannot become tool output."""
    try:
        from agno.exceptions import RunCancelledException
    except (ImportError, ModuleNotFoundError):
        return RuntimeAdapterError("Agno execution was cancelled.")
    return RunCancelledException(str(exc or "Bauer execution cancelled"))


def _guard_bauer_tool(tool: Callable[..., Any]) -> Callable[..., Any]:
    """Keep Bauer cancellation typed across Agno's tool execution boundary."""
    @wraps(tool)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            checkpoint_execution()
            return tool(*args, **kwargs)
        except ProviderRetryInterrupted as exc:
            raise _agno_cancelled_exception(exc) from exc

    return guarded


def _is_agno_cancellation(exc: Exception) -> bool:
    try:
        from agno.exceptions import RunCancelledException
    except (ImportError, ModuleNotFoundError):
        return False
    return isinstance(exc, RunCancelledException)


class AgnoRuntimeAdapter:
    """Adapter that runs Bauer agent requests through the Agno Python SDK."""

    name = "agno"

    def __init__(
        self,
        config: Any | None = None,
        adapter_config: dict[str, Any] | None = None,
        chatgpt_client: Any | None = None,
        tool_router: Any | None = None,
        activity_sink: Callable[[dict[str, Any]], None] | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        runtime_event_bus: Any | None = None,
        runtime_policy_root: str | Path | None = None,
        runtime_policy_mode: str | None = None,
    ):
        self.config = config
        self.adapter_config = adapter_config or self._adapter_config_from(config)
        self.mode = str(self.adapter_config.get("mode") or "sdk").strip().lower()
        self.base_url = str(self.adapter_config.get("base_url") or "http://localhost:7777")
        self.timeout_s = int(self.adapter_config.get("timeout_s") or self.adapter_config.get("timeout_seconds") or 120)
        self.db_file = Path(str(self.adapter_config.get("db_file") or "memory/runtime/agno/sessions.db"))
        workspace = (
            self.adapter_config.get("workspace")
            or getattr(getattr(config, "agent", None), "workspace", None)
            or "workspace"
        )
        if not isinstance(workspace, (str, Path)):
            raise RuntimeAdapterError("Agno workspace must be a path string or Path.")
        self.workspace = Path(workspace).resolve()
        self.tool_context = str(self.adapter_config.get("tool_context") or "worker")
        self.tool_policy_path = self.adapter_config.get("tool_policy_path")
        tools_config = getattr(config, "tools", None)
        self.shell_safe_mode = bool(self.adapter_config.get(
            "shell_safe_mode", getattr(tools_config, "safe_mode", True)
        ))
        self.shell_timeout_s = int(self.adapter_config.get(
            "shell_timeout_s", getattr(tools_config, "timeout_seconds", self.timeout_s)
        ))
        self.shell_max_output_bytes = int(self.adapter_config.get(
            "shell_max_output_kb", getattr(tools_config, "max_output_kb", 50)
        )) * 1024
        extra_commands = self.adapter_config.get(
            "extra_allowed_commands",
            getattr(tools_config, "extra_allowed_commands", []),
        ) or []
        if isinstance(extra_commands, str):
            extra_commands = [item.strip() for item in extra_commands.split(",") if item.strip()]
        self.shell_extra_allowed_commands = list(extra_commands)
        self.chatgpt_client = chatgpt_client
        self._tool_router = tool_router
        self._activity_sink = activity_sink
        self._event_sink = event_sink
        # The server creates a short-lived Agno adapter per turn. Keep the
        # governance objects explicit so routers created lazily for Agno tools
        # use the same approval store and event stream as /stream.
        self.runtime_event_bus = runtime_event_bus
        self.runtime_policy_root = runtime_policy_root
        self.runtime_policy_mode = runtime_policy_mode
        self._agents: dict[str, Any] = {}
        self._teams: dict[str, Any] = {}
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
            if self._event_sink is not None:
                try:
                    self._event_sink(event)
                except Exception:
                    logging.getLogger("bauer.agno_adapter").debug("event sink failed", exc_info=True)
            if self._activity_sink is not None and (
                str(event.get("event", "")).startswith("agent.") or event.get("event") == "task.delegated"
                or (event.get("event") in {"tool.call.requested", "tool.call.completed", "tool.call.failed"}
                    and event.get("agent_id") and event.get("data", {}).get("member_event"))
            ):
                try:
                    self._activity_sink(event)
                except Exception:
                    logging.getLogger("bauer.agno_adapter").debug("activity sink failed", exc_info=True)
            if event.get("event") == "message.delta":
                chunks.append(str(event.get("content", "")))
            elif event.get("event") in {"run.failed", "run.cancelled"}:
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

    def create_team(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Build an Agno Team from normalized Bauer member specs.

        The SDK is intentionally hidden behind this adapter.  The Kernel and
        the team orchestrator deal only in Bauer dictionaries and events, so a
        future runtime SDK can be added without spreading vendor imports.
        """
        normalized = self._normalize_team_spec(spec)
        team_id = str(normalized.get("id") or normalized.get("name") or f"agno-team-{uuid4()}")
        team = self._build_team({**normalized, "id": team_id})
        self._teams[team_id] = team
        return {
            "status": "created",
            "runtime_adapter": self.name,
            "team_id": team_id,
            "mode": self.mode,
            "members": [str(member.get("id") or member.get("name")) for member in normalized["members"]],
            "spec": normalized,
        }

    def run_team(self, request: dict[str, Any]) -> dict[str, Any]:
        run_id = str(request.get("run_id") or f"run-{uuid4()}")
        chunks: list[str] = []
        last_event: dict[str, Any] = {}
        for event in self.stream_team({**request, "run_id": run_id}):
            last_event = event
            if event.get("event") == "message.delta":
                chunks.append(str(event.get("content", "")))
            elif event.get("event") in {"run.failed", "run.cancelled"}:
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

    def stream_team(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        run_id = str(request.get("run_id") or f"run-{uuid4()}")
        session_id = str(request.get("session_id") or f"session-{uuid4()}")
        user_id = str(request.get("user_id") or "local-user")
        yield {
            "event": "run.started",
            "status": "running",
            "run_id": run_id,
            "session_id": session_id,
            "runtime_adapter": self.name,
            "mode": "team",
        }
        try:
            team = self._legacy_team_for_request(request, session_id=session_id, user_id=user_id)
            prompt = self._prompt_from_request(request)
            final_content = ""
            agno_run_id = None
            member_events: list[dict[str, Any]] = []
            for event in _checkpointed_events(_in_agno_session(session_id, team.run(
                prompt,
                stream=True,
                run_id=run_id,
                session_id=session_id,
                user_id=user_id,
            ))):
                agno_run_id = getattr(event, "run_id", agno_run_id)
                event_name = str(getattr(event, "event", "") or "").lower()
                if "error" in event_name or "cancel" in event_name:
                    failed = {
                        "event": "run.failed" if "error" in event_name else "run.cancelled",
                        "status": "failed" if "error" in event_name else "cancelled",
                        "run_id": run_id,
                        "session_id": session_id,
                        "runtime_adapter": self.name,
                        "error": str(getattr(event, "error", None) or getattr(event, "content", None) or event_name),
                    }
                    self._runs[run_id] = failed
                    yield failed
                    return
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
                member_responses = getattr(event, "member_responses", None)
                if member_responses:
                    member_events.append({"count": len(member_responses)})
        except Exception as exc:  # noqa: BLE001 — normalize SDK boundary outcomes
            cancelled = isinstance(exc, ProviderRetryInterrupted) or _is_agno_cancellation(exc)
            failed = {
                "event": "run.cancelled" if cancelled else "run.failed",
                "status": "cancelled" if cancelled else "failed",
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
                "mode": "coordinate",
                "member_events": member_events,
            },
        }
        self._runs[run_id] = completed
        yield completed

    def stream_agent(self, request: dict[str, Any]) -> Iterator[dict[str, Any]]:
        run_id = str(request.get("run_id") or f"run-{uuid4()}")
        session_id = str(request.get("session_id") or f"session-{uuid4()}")
        user_id = str(request.get("user_id") or "local-user")
        tools: list[dict[str, Any]] = []
        tool_positions: dict[str, int] = {}

        def remember_tools(items: Any, *, error: str | None = None) -> None:
            for item in self._serialize_tools(items):
                if error:
                    item["status"] = "failed"
                    item["result"] = item["result"] or error
                key = str(item.get("tool_call_id") or json.dumps({
                    "name": item["tool_name"], "args": item.get("tool_args"),
                    "result": item["result"],
                }, sort_keys=True, default=str))
                if key in tool_positions:
                    previous = tools[tool_positions[key]]
                    previous.update({field: value for field, value in item.items() if value is not None})
                else:
                    tool_positions[key] = len(tools)
                    tools.append(item)

        yield {
            "event": "run.started",
            "status": "running",
            "run_id": run_id,
            "session_id": session_id,
            "runtime_adapter": self.name,
            "mode": self.mode,
        }

        try:
            agent = self._runtime_for_request(request, session_id=session_id, user_id=user_id)
            prompt = self._prompt_from_request(request)
            final_content = ""
            agno_run_id = None
            team_request = self._is_team_request(request)
            observed_member_messages: set[str] = set()
            for event in _checkpointed_events(_in_agno_session(session_id, agent.run(
                prompt, stream=True, stream_events=True,
                run_id=run_id, session_id=session_id, user_id=user_id,
            ))):
                agno_run_id = getattr(event, "run_id", agno_run_id)
                agno_event = self._agno_event_name(event)
                singular_tool = getattr(event, "tool", None)
                if singular_tool is not None and agno_event in {"ToolCallCompleted", "ToolCallError"}:
                    remember_tools([singular_tool], error=str(getattr(event, "error", "") or "") or None)
                event_tools = getattr(event, "tools", None)
                if event_tools and agno_event != "ToolCallStarted":
                    remember_tools(event_tools)
                member_id = str(getattr(event, "agent_id", "") or "").strip()
                member_name = str(getattr(event, "agent_name", "") or "").strip()
                parent_run_id = getattr(event, "parent_run_id", None)
                is_member_event = team_request and not agno_event.startswith("Team") and bool(
                    member_id or parent_run_id
                )

                normalized = self._normalize_agent_event(
                    event,
                    run_id=run_id,
                    session_id=session_id,
                    team_id=self._decision_value(request, "team_id"),
                    member_event=is_member_event,
                )
                if normalized:
                    event_key = (member_id or member_name or "").lower()
                    if normalized["event"] == "agent.message.sent":
                        if event_key in observed_member_messages:
                            normalized = None
                        else:
                            observed_member_messages.add(event_key)
                    if normalized:
                        yield normalized

                if not is_member_event and agno_event in {
                    "RunError", "TeamRunError", "RunCancelled", "TeamRunCancelled",
                }:
                    outcome = "cancelled" if agno_event.endswith("Cancelled") else "failed"
                    if outcome == "cancelled":
                        raise _agno_cancelled_exception()
                    raise RuntimeAdapterError("Agno reported run failed.")

                content = getattr(event, "content", None)
                is_team_content = agno_event == "TeamRunContent"
                is_final_content = is_team_content if team_request else agno_event == "RunContent"
                if content and (not is_member_event) and is_final_content:
                    final_content += str(content)
                    yield {
                        "event": "message.delta",
                        "status": "running",
                        "run_id": run_id,
                        "session_id": session_id,
                        "runtime_adapter": self.name,
                        "content": str(content),
                    }
        except Exception as exc:  # noqa: BLE001 — normalize cancellation at the adapter boundary
            cancelled = isinstance(exc, ProviderRetryInterrupted) or _is_agno_cancellation(exc)
            failed = {
                "event": "run.cancelled" if cancelled else "run.failed",
                "status": "cancelled" if cancelled else "failed",
                "run_id": run_id,
                "session_id": session_id,
                "runtime_adapter": self.name,
                "error": str(exc),
                "metadata": {"tools": tools},
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
                "runtime_kind": "team" if self._is_team_request(request) else "agent",
                "team_id": self._decision_value(request, "team_id"),
                "agent_id": self._decision_value(request, "agent_id") or request.get("agent_id", ""),
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
        agent_id = str(
            self._decision_value(request, "agent_id")
            or request.get("agent_id")
            or request.get("agent", "")
        ).strip()
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

    def _runtime_for_request(self, request: dict[str, Any], *, session_id: str, user_id: str) -> Any:
        """Resolve a decisão para um Team Agno ou para um Agent individual.

        A decisão chega ao adapter somente depois do preflight do Kernel. O
        adapter não decide permissões: ele apenas materializa a escolha que já
        foi validada e registrada. Sem ``team_id`` o caminho individual segue
        exatamente o comportamento anterior.
        """
        team_id = self._decision_value(request, "team_id")
        if not team_id:
            return self._agent_for_request(request, session_id=session_id, user_id=user_id)
        return self._team_for_request(
            request,
            team_id=team_id,
            session_id=session_id,
            user_id=user_id,
        )

    def _team_for_request(
        self,
        request: dict[str, Any],
        *,
        team_id: str,
        session_id: str,
        user_id: str,
    ) -> Any:
        Team, TeamMode = self._require_agno_team()
        from ..agent_registry import RuntimeAgentRegistry
        from ..team_registry import TeamRegistry

        package_root = Path(__file__).resolve().parents[3]
        agent_roots: list[str | Path] = [package_root / "data" / "agent_specs"]
        workspace_agents = self.workspace / "agents.yaml"
        if workspace_agents.exists():
            agent_roots.insert(0, workspace_agents)
        agents = RuntimeAgentRegistry(roots=agent_roots)
        team_roots: list[str | Path] = [package_root / "data" / "team_specs"]
        workspace_teams = self.workspace / "teams"
        if workspace_teams.exists():
            team_roots.insert(0, workspace_teams)
        team = TeamRegistry(roots=team_roots, agent_registry=agents).get(team_id)
        if team is None:
            raise RuntimeAdapterError(f"Agno team not found: {team_id}")

        requested_agents = 0
        try:
            requested_agents = int(self._decision_value(request, "requested_agents") or 0)
        except ValueError:
            requested_agents = 0
        member_ids = list(team.agents)
        if requested_agents >= 2:
            requested_agents = min(requested_agents, len(member_ids))
            coordinator_id = team.coordinator or member_ids[0]
            member_ids = [
                coordinator_id,
                *(member_id for member_id in member_ids if member_id != coordinator_id),
            ][:requested_agents]

        members = []
        for member_id in member_ids:
            spec = agents.get(member_id)
            if spec is None:
                raise RuntimeAdapterError(f"Agno team member not found: {member_id}")
            member_spec = spec.to_dict()
            member_spec.update({"session_id": session_id, "user_id": user_id})
            members.append(self._build_agent(member_spec))

        coordination = team.coordination or {}
        mode_name = str(coordination.get("mode") or "coordinate").strip().lower()
        # Bauer uses "supervisor" for a governed coordinator; Agno's closest
        # native mode is coordinate. Keep the mapping explicit for auditability.
        mode_name = {"supervisor": "coordinate"}.get(mode_name, mode_name)
        mode = getattr(TeamMode, mode_name, TeamMode.coordinate)
        coordinator = next(
            (member for member in members if getattr(member, "id", "") == team.coordinator),
            members[0],
        )
        instructions = [
            f"Coordene o time {team.name} conforme a política do Bauer.",
            "Respeite as permissões e ferramentas de cada agente membro.",
            "Delegue cada tarefa somente aos especialistas relevantes e consolide as evidências.",
        ]
        configured_instructions = coordination.get("instructions", [])
        if isinstance(configured_instructions, str):
            configured_instructions = [configured_instructions]
        instructions.extend(
            str(item).strip()
            for item in configured_instructions
            if str(item).strip()
        )
        return Team(
            id=team.id,
            name=team.name,
            model=getattr(coordinator, "model", None),
            db=self._build_db(),
            members=members,
            mode=mode,
            instructions=instructions,
            determine_input_for_members=True,
            delegate_to_all_members=requested_agents >= 2,
            stream_member_events=True,
            add_history_to_context=True,
            num_history_runs=3,
        )

    @staticmethod
    def _decision_value(request: dict[str, Any], key: str) -> str:
        decision = request.get("decision")
        if isinstance(decision, dict):
            value = decision.get(key)
            if value:
                return str(value).strip()
        value = request.get(key)
        return str(value).strip() if value else ""

    @classmethod
    def _is_team_request(cls, request: dict[str, Any]) -> bool:
        return bool(cls._decision_value(request, "team_id"))

    @staticmethod
    def _agno_event_name(event: Any) -> str:
        value = getattr(event, "event", None)
        if value is None:
            raw = type(event).__name__
        else:
            raw = str(getattr(value, "value", value)).split(".")[-1]
        aliases = {
            "run_started": "RunStarted", "run_content": "RunContent",
            "run_intermediate_content": "RunIntermediateContent",
            "run_completed": "RunCompleted", "run_error": "RunError",
            "run_cancelled": "RunCancelled", "tool_call_started": "ToolCallStarted",
            "tool_call_completed": "ToolCallCompleted", "tool_call_error": "ToolCallError",
            "team_task_created": "TeamTaskCreated", "team_task_updated": "TeamTaskUpdated",
            "teamtaskcreated": "TeamTaskCreated", "teamtaskupdated": "TeamTaskUpdated",
            "team_tool_call_started": "ToolCallStarted",
            "team_tool_call_completed": "ToolCallCompleted",
            "team_tool_call_error": "ToolCallError",
            # Agno 3.x enum values are CamelCase strings (for example,
            # TeamToolCallStarted), while older versions exposed snake_case.
            "teamtoolcallstarted": "ToolCallStarted",
            "teamtoolcallcompleted": "ToolCallCompleted",
            "teamtoolcallerror": "ToolCallError",
        }
        return aliases.get(raw.lower(), raw)

    @classmethod
    def _normalize_agent_event(
        cls,
        event: Any,
        *,
        run_id: str,
        session_id: str,
        team_id: str,
        member_event: bool,
    ) -> dict[str, Any] | None:
        """Map Agno lifecycle/tool events to a privacy-safe Bauer event."""
        name = cls._agno_event_name(event)
        agent_id = str(getattr(event, "agent_id", "") or "").strip()
        agent_name = str(getattr(event, "agent_name", "") or "").strip()
        agno_run_id = str(getattr(event, "run_id", "") or "")
        data: dict[str, Any] = {
            "agno_event": name,
            "team_id": team_id or None,
            "member_run_id": agno_run_id or None,
            "parent_run_id": str(getattr(event, "parent_run_id", "") or "") or None,
            "agent_name": agent_name or None,
        }
        common = {
            "run_id": run_id,
            "session_id": session_id,
            "agent_id": agent_id or None,
            "data": data,
        }

        if name == "RunStarted" and (member_event or agent_id):
            return {"event": "agent.started", "status": "running", **common}
        if name in {"RunContent", "RunIntermediateContent"} and member_event:
            return {"event": "agent.message.sent", "status": "working", **common}
        if name == "RunCompleted" and (member_event or agent_id):
            return {"event": "agent.completed", "status": "completed", **common}
        if name == "RunCancelled" and (member_event or agent_id):
            return {"event": "agent.cancelled", "status": "cancelled", **common}
        if name == "RunError" and (member_event or agent_id):
            return {"event": "agent.failed", "status": "failed", **common}

        if name in {"ToolCallStarted", "ToolCallCompleted", "ToolCallError"}:
            tool = getattr(event, "tool", None)
            tool_name = str(
                getattr(tool, "tool_name", None) or getattr(tool, "name", None) or "tool"
            )
            event_type = {
                "ToolCallStarted": "tool.call.requested",
                "ToolCallCompleted": "tool.call.completed",
                "ToolCallError": "tool.call.failed",
            }[name]
            data["member_event"] = member_event
            return {
                "event": event_type,
                "status": "running" if name == "ToolCallStarted" else "failed" if name == "ToolCallError" else "completed",
                "tool_name": tool_name,
                **common,
            }

        if name in {"TeamTaskCreated", "TeamTaskUpdated"}:
            assignee = str(getattr(event, "assignee", "") or "").strip()
            if assignee:
                data.update({"task_id": getattr(event, "task_id", None), "assignee": assignee})
                task = str(getattr(event, "description", "") or getattr(event, "title", "") or "").strip()
                if task:
                    data["task"] = task
                task_status = str(getattr(event, "status", "") or "")
                status = {"pending": "queued", "in_progress": "running", "blocked": "paused",
                          "completed": "completed", "failed": "failed"}.get(task_status, "delegated")
                return {"event": "task.delegated", "status": status, "agent_id": assignee,
                        "run_id": run_id, "session_id": session_id, "data": data}
        return None

    def _build_agent(self, spec: dict[str, Any]) -> Any:
        Agent, _, _ = self._require_agno()
        instructions = self._instructions_from_spec(spec)
        model = self._build_model(spec)
        return Agent(
            id=str(spec.get("id") or spec.get("name") or f"agno-agent-{uuid4()}"),
            name=str(spec.get("name") or "Agno Bauer Agent"),
            model=model,
            db=self._build_db(),
            # The browser OAuth Responses adapter currently emits text only;
            # do not advertise Bauer tools to Agno until function-call parsing
            # is available for that backend.
            tools=[] if getattr(model, "supports_native_tools", True) is False else self._map_tools(spec.get("tools")),
            user_id=str(spec.get("user_id") or "local-user"),
            session_id=str(spec.get("session_id") or f"session-{uuid4()}"),
            instructions=instructions or None,
            add_history_to_context=True,
            num_history_runs=int(spec.get("num_history_runs") or 3),
        )

    def _build_team(self, spec: dict[str, Any]) -> Any:
        _, _, _ = self._require_agno()
        try:
            from agno.team import Team
            from agno.team.mode import TeamMode
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeAdapterError("Agno Team support requires a current 'agno' installation.") from exc

        members = [self._build_agent(member) for member in spec.get("members", [])]
        supervisor = dict(spec.get("supervisor") or {})
        mode = str(spec.get("mode") or "coordinate").strip().lower()
        team_mode = getattr(TeamMode, mode, TeamMode.coordinate)
        instructions = self._instructions_from_spec(supervisor)
        team_model = self._build_model(supervisor)
        return Team(
            id=str(spec.get("id") or f"agno-team-{uuid4()}"),
            name=str(spec.get("name") or "Bauer Agno Team"),
            role=str(spec.get("role") or "Coordenador do time Bauer"),
            mode=team_mode,
            members=members,
            model=team_model,
            db=self._build_db(),
            user_id=str(spec.get("user_id") or "local-user"),
            session_id=str(spec.get("session_id") or f"session-{uuid4()}"),
            instructions=instructions or None,
            determine_input_for_members=bool(spec.get("determine_input_for_members", True)),
            delegate_to_all_members=bool(spec.get("delegate_to_all_members", False)),
            max_iterations=int(spec.get("max_iterations") or 10),
            add_history_to_context=True,
            num_history_runs=int(spec.get("num_history_runs") or 3),
            stream_member_events=True,
        )

    def _legacy_team_for_request(self, request: dict[str, Any], *, session_id: str, user_id: str) -> Any:
        team_id = str(request.get("team_id") or "").strip()
        if team_id and team_id in self._teams:
            return self._teams[team_id]
        raw = dict(request.get("team_spec") or {})
        raw.setdefault("id", team_id or f"agno-team-{uuid4()}")
        raw.setdefault("session_id", session_id)
        raw.setdefault("user_id", user_id)
        members = [dict(member) for member in request.get("agent_specs", []) if isinstance(member, dict)]
        raw["members"] = members
        raw.setdefault("supervisor", request.get("supervisor_spec") or (members[0] if members else {}))
        team = self._build_team(self._normalize_team_spec(raw))
        if team_id:
            self._teams[team_id] = team
        return team

    @staticmethod
    def _normalize_team_spec(spec: dict[str, Any]) -> dict[str, Any]:
        members = []
        for member in spec.get("members", []) or []:
            normalized = AgnoRuntimeAdapter._normalize_spec(member)
            normalized["id"] = str(normalized.get("id") or normalized.get("name") or f"agno-agent-{uuid4()}")
            members.append(normalized)
        supervisor = AgnoRuntimeAdapter._normalize_spec(spec.get("supervisor") or (members[0] if members else {}))
        return {
            "id": str(spec.get("id") or ""),
            "name": str(spec.get("name") or "Bauer Agno Team"),
            "mode": str((spec.get("coordination") or {}).get("mode") or spec.get("mode") or "coordinate"),
            "members": members,
            "supervisor": supervisor,
            "max_iterations": int(spec.get("max_iterations") or (spec.get("limits") or {}).get("max_iterations") or 10),
            "determine_input_for_members": bool(spec.get("determine_input_for_members", True)),
            "delegate_to_all_members": bool(spec.get("delegate_to_all_members", False)),
            "session_id": spec.get("session_id"),
            "user_id": spec.get("user_id"),
        }

    def _build_model(self, spec: dict[str, Any]) -> Any:
        if spec.get("agno_model") is not None:
            return spec["agno_model"]
        provider, model_name = self._effective_model_spec(spec)
        if not model_name or provider in {"", "local", "offline"} or model_name.startswith("offline"):
            return OfflineAgnoModel(id=model_name or "offline-echo")
        if provider == "openai":
            oauth_client = self.chatgpt_client or self._chatgpt_oauth_client()
            if oauth_client is not None:
                from .chatgpt_oauth_model import build_chatgpt_oauth_model

                return build_chatgpt_oauth_model(oauth_client, model_name)
        try:
            from agno.models.openai import OpenAIChat
        except (ImportError, ModuleNotFoundError) as exc:
            raise RuntimeAdapterError("Agno provider support requires the 'openai' package.") from exc
        base_url, api_key = self._provider_connection(provider)
        kwargs: dict[str, Any] = {"id": model_name}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAIChat(**kwargs)

    def _chatgpt_oauth_client(self) -> Any | None:
        """Resolve the same browser OAuth token used by Bauer CLI/Serve."""
        try:
            from ....auth import AuthManager
            from ....chatgpt_backend import ChatGPTBackendClient, DEFAULT_CHATGPT_BASE

            token = AuthManager().store.load("openai")
            if token is None or token.api_key or not token.access_token:
                return None
            if token.is_expired and token.refresh_token:
                token = AuthManager().refresh("openai") or token
            if token.is_expired or not token.access_token:
                return None
            cfg_openai = getattr(self.config, "openai", None)
            base_url = getattr(cfg_openai, "chatgpt_base_url", "") or DEFAULT_CHATGPT_BASE
            timeout = int(getattr(cfg_openai, "timeout_seconds", 60) or 60)
            return ChatGPTBackendClient(
                access_token=token.access_token,
                account_id=token.extra.get("chatgpt_account_id") or "",
                base_url=base_url,
                timeout_seconds=timeout,
                model=str(getattr(getattr(self.config, "model", None), "name", "") or ""),
            )
        except Exception:
            return None

    def _effective_model_spec(self, spec: dict[str, Any]) -> tuple[str, str]:
        model_value = str(spec.get("model") or "").strip()
        provider = str(spec.get("provider") or "").strip().lower()
        model_cfg = getattr(self.config, "model", None)
        config_provider = str(getattr(model_cfg, "provider", "") or "").strip().lower()
        config_model = str(getattr(model_cfg, "name", "") or "").strip()
        if not provider or provider in {"local", "openrouter"} and model_value in {"", "auto"}:
            provider = config_provider
        if not model_value or model_value == "auto":
            model_value = config_model
        return provider, model_value

    def _provider_connection(self, provider: str) -> tuple[str, str]:
        cfg = self.config
        section = getattr(cfg, provider, None)
        key = str(getattr(section, "api_key", "") or "")
        if provider == "openrouter":
            return "https://openrouter.ai/api/v1", key
        if provider == "ollama":
            host = str(getattr(section, "host", "http://127.0.0.1:11434") or "http://127.0.0.1:11434").rstrip("/")
            return f"{host}/v1", key or "ollama"
        host = str(getattr(section, "host", "") or "").strip().rstrip("/")
        if host and not host.endswith("/v1"):
            host = f"{host}/v1"
        return host, key

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
    def _require_agno_team() -> tuple[Any, Any]:
        try:
            from agno.team import Team, TeamMode
        except ModuleNotFoundError as exc:
            raise RuntimeAdapterError("Agno team execution requires the installed 'agno' package with Team support.") from exc
        return Team, TeamMode

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
        from agno.tools.function import Function
        from ..agno_catalog import AgnoCapabilityCatalog

        # A file read performed by one callable must be visible to the write
        # callable created for the same Agent. Building a ToolRouter inside
        # _bauer_tool_callable gave each function its own read-before-write
        # tracker, so a legitimate read -> overwrite sequence was rejected.
        # Keep routers local to this mapped tool set and session. An Agent may
        # be cached and reused across sessions; a single router would then let
        # a read in an older conversation authorize a later overwrite.
        routers_by_session: dict[str, Any] = {}

        def router_for_session() -> Any:
            session_id = _ACTIVE_AGNO_SESSION_ID.get()
            if session_id not in routers_by_session:
                routers_by_session[session_id] = self._build_tool_router()
            return routers_by_session[session_id]

        mapped: list[Any] = []
        for tool in tools:
            if isinstance(tool, Function):
                mapped.append(tool)
            elif callable(tool):
                mapped.append(tool)
            elif isinstance(tool, dict) and callable(tool.get("callable")):
                mapped.append(tool["callable"])
            elif isinstance(tool, dict) and callable(tool.get("function")):
                mapped.append(tool["function"])
            elif isinstance(tool, str):
                capability = AgnoCapabilityCatalog.find(tool) or AgnoCapabilityCatalog.resolve_bauer_tool(tool)
                if capability is not None and not capability.factory_registered:
                    raise RuntimeAdapterError(
                        f"Agno capability '{capability.id}' is cataloged but has no Bauer adapter yet."
                    )
                mapped_tool = self._bauer_tool_callable(tool, router_factory=router_for_session)
                if mapped_tool is None:
                    if capability is None:
                        raise RuntimeAdapterError(
                            f"Agno tool '{tool}' is not registered in the Bauer capability catalog "
                            "or authorized by the Bauer ToolRouter."
                        )
                    raise RuntimeAdapterError(
                        f"Agno capability '{capability.id}' is not authorized by the Bauer ToolRouter."
                    )
                mapped.append(_guard_bauer_tool(mapped_tool) if callable(mapped_tool) else mapped_tool)
            else:
                raise RuntimeAdapterError("Agno tools must be registered names or trusted callables.")
        return mapped

    def _bauer_tool_callable(
        self,
        tool_name: str,
        *,
        router: Any | None = None,
        router_factory: Any | None = None,
    ) -> Any | None:
        # Direct callers remain supported; _map_tools supplies a session-aware
        # factory shared by all Bauer callables belonging to the same Agent.
        if router_factory is not None:
            get_router = router_factory
        elif router is not None:
            def get_router() -> Any:
                return router
        else:
            get_router = self._build_tool_router
        normalized = tool_name.strip().removeprefix("bauer.")
        schemas = get_router().get_tool_schemas()
        schema = next((item.get("function") for item in schemas
                       if isinstance(item, dict) and isinstance(item.get("function"), dict)
                       and item["function"].get("name") == normalized), None)
        if schema is None:
            return None

        if normalized == "read_file":
            def read_file(path: str, offset: int = 1, limit: int = 0) -> str:
                args = {"path": path, "offset": offset}
                if limit:
                    args["limit"] = limit
                return get_router().execute_native_call("read_file", args)
            return read_file

        if normalized == "write_file":
            def write_file(path: str, content: str, overwrite: bool = False) -> str:
                return get_router().execute_native_call("write_file", {"path": path, "content": content, "overwrite": overwrite})
            return write_file

        if normalized == "list_dir":
            def list_dir(path: str = ".") -> str:
                return get_router().execute_native_call("list_dir", {"path": path})
            return list_dir

        if normalized == "search_text":
            def search_text(pattern: str, path: str = ".") -> str:
                return get_router().execute_native_call(
                    "search_text",
                    {"pattern": pattern, "path": path},
                )
            return search_text

        if normalized == "run_command":
            def run_command(command: str, confirm: bool = False, background: bool = False) -> str:
                """Execute one command with shell=False in the configured workspace.

                Paths resolve from that workspace, not the repository root.
                Inspect list_dir('.') first; use explicit paths or command flags
                such as git -C and docker compose --project-directory/-f.
                No cd &&, chaining, pipes or redirects: use separate tool calls.
                Quote arguments containing spaces or Python -c code. Parsing uses
                POSIX quoting even on Windows; prefer quoted forward-slash paths.
                Use background=True for long-running processes, not a trailing &.
                confirm does not bypass policy approval, denylist or other guards.

                In Docker, localhost and socket binds refer to this container.
                A successful bind here does not prove a host port is free. The
                Docker socket controls the daemon host; published ports and bind
                mount source paths belong to that host. Check docker compose
                version, inspect port mappings, and verify host access separately
                (host.docker.internal on Docker Desktop when available). A healthy
                container or HTTP 200 alone does not validate the application:
                check the expected app identity/content at the intended host URL.
                Report host access as unverified when it cannot be tested.

                Args:
                    command: A single executable and its quoted arguments.
                    confirm: Explicit confirmation for the safe-mode risk gate.
                    background: Start a tracked process without waiting for exit.
                """
                return get_router().execute_native_call(
                    "run_command",
                    {"command": command, "confirm": confirm, "background": background},
                )
            return run_command

        if normalized == "web_search":
            def web_search(query: str, max_results: int = 5) -> str:
                return get_router().execute_native_call("web_search", {"query": query, "max_results": max_results})
            return web_search

        if normalized == "memory":
            def memory(action: str, key: str = "", value: str = "") -> str:
                args = {"action": action}
                if key:
                    args["key"] = key
                if value:
                    args["value"] = value
                return get_router().execute_native_call("memory", args)
            return memory

        from copy import deepcopy
        from agno.tools.function import Function

        def execute_bauer_tool(**kwargs: Any) -> str:
            return get_router().execute_native_call(normalized, kwargs)

        return Function(
            name=normalized,
            description=str(schema.get("description") or ""),
            parameters=deepcopy(schema.get("parameters") or {"type": "object", "properties": {}}),
            entrypoint=_guard_bauer_tool(execute_bauer_tool),
            skip_entrypoint_processing=True,
        )

    def _build_tool_router(self) -> Any:
        if self._tool_router is not None:
            return self._tool_router
        from bauer.shell_runner import ShellRunner
        from bauer.tool_router import ToolRouter

        shell_runner = ShellRunner(
            workspace=self.workspace,
            safe_mode=self.shell_safe_mode,
            timeout=self.shell_timeout_s,
            max_output_bytes=self.shell_max_output_bytes,
            extra_allowed_commands=self.shell_extra_allowed_commands,
        )
        router = ToolRouter(
            workspace=self.workspace,
            shell_runner=shell_runner,
            web_enabled=True,
            max_tool_calls=500,
            tool_context=self.tool_context,
            tool_policy_path=self.tool_policy_path,
            policy_enabled=True,
            policy_rules_path=self.adapter_config.get("policy_rules_path"),
            policy_root=self.adapter_config.get("policy_root", "memory/runtime"),
        )
        if self.runtime_event_bus is not None:
            router._event_bus = self.runtime_event_bus  # type: ignore[attr-defined]
        if self.runtime_policy_root is not None:
            router._policy_root = Path(self.runtime_policy_root)  # type: ignore[attr-defined]
        if self.runtime_policy_mode is not None:
            router._policy_mode = self.runtime_policy_mode  # type: ignore[attr-defined]
            router._policy_enabled = self.runtime_policy_mode != "off"  # type: ignore[attr-defined]
            router._wait_for_policy_approval = self.runtime_policy_mode == "enforce"  # type: ignore[attr-defined]
        return router

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
                    "tool_call_id": data.get("tool_call_id") or data.get("id"),
                    "tool_args": data.get("tool_args"),
                    "result": data.get("result"),
                    "status": data.get("status") or ("failed" if data.get("tool_call_error") else "completed"),
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
