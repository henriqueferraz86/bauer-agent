"""Persistent lifecycle and mailbox primitives for runtime subagents."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from ..events import EventBus
from .agent_registry import RuntimeAgentRegistry
from .run_manager import RunManager
from .session_manager import SessionManager
from .state_store import RuntimeStateStore, SqliteStateStore

AGENT_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
AGENT_ACTIVE_STATUSES = {"queued", "running", "paused"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AgentSession:
    id: str
    agent_id: str
    memory: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    workspace: str = ""
    events: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)


@dataclass
class AgentProcess:
    id: str
    agent_id: str
    parent_agent_id: str | None
    session_id: str
    run_id: str
    status: str
    model: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    workspace: str = ""
    events: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    error: str | None = None


@dataclass
class AgentMessage:
    id: str
    from_agent_id: str
    to_agent_id: str
    message: str
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    delivered_at: str | None = None


class AgentMailbox:
    """Durable point-to-point mailbox backed by the runtime store."""

    def __init__(self, store: RuntimeStateStore):
        self.store = store

    def send(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        if not message.strip():
            raise ValueError("message must not be empty")
        item = AgentMessage(
            id=f"msg-{uuid4()}",
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            message=message,
            metadata=metadata or {},
        )
        self.store.append("agent_messages", item)
        return item

    def receive(self, agent_id: str, *, unread_only: bool = False) -> list[AgentMessage]:
        records = self.store.list("agent_messages")
        messages = [AgentMessage(**item) for item in records if item.get("to_agent_id") == agent_id]
        if unread_only:
            messages = [item for item in messages if item.status == "pending"]
        return messages

    def acknowledge(self, message_id: str) -> AgentMessage:
        records = self.store.list("agent_messages")
        for item in reversed(records):
            if item.get("id") == message_id:
                message = AgentMessage(**item)
                if message.status == "acknowledged":
                    return message
                message.status = "acknowledged"
                message.delivered_at = _now_iso()
                self.store.append("agent_messages", message)
                return message
        raise KeyError(f"Message not found: {message_id}")


class AgentSupervisor:
    """Read-only views over process state and the parent/child hierarchy."""

    def __init__(self, manager: AgentManager):
        self.manager = manager

    def running(self) -> list[AgentProcess]:
        return [
            process
            for process in self.manager.list_agents()
            if process.status in AGENT_ACTIVE_STATUSES
        ]

    def tree(self) -> list[dict[str, Any]]:
        processes = {process.id: process for process in self.manager.list_agents()}
        children: dict[str | None, list[AgentProcess]] = {}
        for process in processes.values():
            children.setdefault(process.parent_agent_id, []).append(process)
        for items in children.values():
            items.sort(key=lambda item: (item.created_at, item.id))

        def branch(process: AgentProcess) -> dict[str, Any]:
            return {
                "id": process.id,
                "agent_id": process.agent_id,
                "status": process.status,
                "parent_agent_id": process.parent_agent_id,
                "children": [branch(child) for child in children.get(process.id, [])],
            }

        return [branch(process) for process in children.get(None, [])]


class AgentManager:
    """Facade for persistent subagent lifecycle operations."""

    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        store: RuntimeStateStore | None = None,
        agent_registry: RuntimeAgentRegistry | None = None,
        run_manager: RunManager | None = None,
        session_manager: SessionManager | None = None,
        event_bus: EventBus | None = None,
    ):
        self.store = store or SqliteStateStore(root)
        self.event_bus = event_bus or EventBus(store=self.store)
        self.agent_registry = agent_registry or RuntimeAgentRegistry()
        self.run_manager = run_manager or RunManager(
            store=self.store,
            event_bus=self.event_bus,
            agent_registry=self.agent_registry,
        )
        self.session_manager = session_manager or SessionManager(store=self.store)
        self.mailbox = AgentMailbox(self.store)
        self.supervisor = AgentSupervisor(self)

    def spawn_agent(
        self,
        *,
        agent_id: str,
        message: str = "",
        parent_agent_id: str | None = None,
        version: str | None = None,
        model: dict[str, Any] | None = None,
        budget: dict[str, Any] | None = None,
        tools: list[str] | None = None,
        permissions: list[str] | None = None,
        workspace: str | Path | None = None,
        memory: dict[str, Any] | None = None,
    ) -> AgentProcess:
        spec = self.agent_registry.get(agent_id, version=version)
        if spec is None:
            raise KeyError(f"Agent not found: {agent_id}")
        if parent_agent_id:
            parent = self.inspect_agent(parent_agent_id)
            if parent is None:
                raise KeyError(f"Parent agent not found: {parent_agent_id}")
            if parent.status in AGENT_TERMINAL_STATUSES:
                raise ValueError("parent agent is terminal")

        selected_model = dict(model or spec.model_spec())
        selected_budget = dict(budget or spec.limits)
        selected_tools = list(tools if tools is not None else spec.tools)
        selected_permissions = list(permissions if permissions is not None else spec.permissions)
        selected_workspace = "" if workspace is None else str(workspace)
        session = self.session_manager.create_session(
            user_id="local",
            agent_id=spec.id,
            state={"message": message, "parent_agent_id": parent_agent_id},
        )
        agent_session = AgentSession(
            id=session.id,
            agent_id=spec.id,
            memory=dict(memory or spec.memory),
            model=selected_model,
            budget=selected_budget,
            tools=selected_tools,
            permissions=selected_permissions,
            workspace=selected_workspace,
        )
        self.store.append("agent_sessions", agent_session)
        run = self.run_manager.create_run_for_agent(
            agent_id=spec.id,
            version=spec.version,
            session_id=session.id,
            input={
                "message": message,
                "model": selected_model,
                "budget": selected_budget,
                "tools": selected_tools,
                "permissions": selected_permissions,
                "workspace": selected_workspace,
                "parent_agent_id": parent_agent_id,
            },
            status="queued",
        )
        process = AgentProcess(
            id=f"agent-{uuid4()}",
            agent_id=spec.id,
            parent_agent_id=parent_agent_id,
            session_id=session.id,
            run_id=run.id,
            status="queued",
            model=selected_model,
            budget=selected_budget,
            tools=selected_tools,
            permissions=selected_permissions,
            workspace=selected_workspace,
        )
        self.store.append("agent_processes", process)
        self.run_manager.start_run(run.id)
        return self._transition(process, "running", event="agent.started")

    def send_agent(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentMessage:
        target = self.inspect_agent(to_agent_id)
        if target is None:
            raise KeyError(f"Agent process not found: {to_agent_id}")
        if target.status in AGENT_TERMINAL_STATUSES:
            raise ValueError("cannot send to terminal agent")
        result = self.mailbox.send(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            message=message,
            metadata=metadata,
        )
        self.event_bus.publish(
            "agent.message.sent",  # type: ignore[arg-type]
            session_id=target.session_id,
            agent_id=target.agent_id,
            status=result.status,
            message=message,
            data={"message_id": result.id, "from_agent_id": from_agent_id},
        )
        return result

    def wait_agent(self, process_id: str, *, timeout: float = 0.0) -> AgentProcess | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            process = self.inspect_agent(process_id)
            if process is None or process.status in AGENT_TERMINAL_STATUSES:
                return process
            if time.monotonic() >= deadline:
                return process
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def cancel_agent(self, process_id: str) -> AgentProcess:
        return self._change_status(process_id, "cancelled", event="agent.cancelled")

    def pause_agent(self, process_id: str) -> AgentProcess:
        return self._change_status(process_id, "paused", event="agent.paused")

    def resume_agent(self, process_id: str) -> AgentProcess:
        return self._change_status(process_id, "running", event="agent.resumed")

    def set_parent(self, process_id: str, parent_agent_id: str | None) -> AgentProcess:
        process = self.inspect_agent(process_id)
        if process is None:
            raise KeyError(f"Agent process not found: {process_id}")
        if parent_agent_id == process_id:
            raise ValueError("parent relationship would create a cycle")
        if parent_agent_id is not None and self.inspect_agent(parent_agent_id) is None:
            raise KeyError(f"Parent agent not found: {parent_agent_id}")
        current = parent_agent_id
        while current is not None:
            if current == process_id:
                raise ValueError("parent relationship would create a cycle")
            ancestor = self.inspect_agent(current)
            current = None if ancestor is None else ancestor.parent_agent_id
        process.parent_agent_id = parent_agent_id
        return self._save_process(process)

    def inspect_agent(self, process_id: str) -> AgentProcess | None:
        data = self.store.latest("agent_processes", process_id)
        return AgentProcess(**data) if data else None

    def list_agents(self) -> list[AgentProcess]:
        records = cast(list[dict[str, Any]], self.store.list_latest("agent_processes"))
        return [AgentProcess(**item) for item in records]

    def _change_status(self, process_id: str, status: str, *, event: str) -> AgentProcess:
        process = self.inspect_agent(process_id)
        if process is None:
            raise KeyError(f"Agent process not found: {process_id}")
        if process.status in AGENT_TERMINAL_STATUSES:
            return process
        if status == "paused" and process.status != "running":
            raise ValueError("only a running agent can be paused")
        if status == "running" and process.status != "paused":
            raise ValueError("only a paused agent can be resumed")
        updated = self._transition(process, status, event=event)
        if status == "cancelled":
            self.run_manager.cancel_run(process.run_id)
        elif status == "paused":
            self.run_manager.update_run(process.run_id, status="paused")
        elif status == "running":
            self.run_manager.start_run(process.run_id)
        return updated

    def _transition(self, process: AgentProcess, status: str, *, event: str) -> AgentProcess:
        process.status = status
        process.events.append(event)
        updated = self._save_process(process)
        self.event_bus.publish(
            event,  # type: ignore[arg-type]
            run_id=process.run_id,
            session_id=process.session_id,
            agent_id=process.agent_id,
            status=status,
            data={"agent_process_id": process.id, "parent_agent_id": process.parent_agent_id},
        )
        return updated

    def _save_process(self, process: AgentProcess) -> AgentProcess:
        process.updated_at = _now_iso()
        self.store.append("agent_processes", process)
        return process
