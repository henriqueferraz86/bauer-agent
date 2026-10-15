"""Read-only public projection of governed runs and agent lifecycle events."""

from __future__ import annotations

import re
from typing import Any

from .core.runtime.run_manager import RUN_STATUSES, TERMINAL_RUN_STATUSES, Run
from .privacy import redact_text

MAX_RUNS = 5
MAX_AGENTS = 25
MAX_EVENTS_PER_RUN = 400
MAX_TASK_TEXT = 320
_MEMBER_STATUSES = {*RUN_STATUSES, "delegated", "unknown"}
_LIFECYCLE = {
    "agent.started": "running", "agent.paused": "paused", "agent.resumed": "running",
    "agent.completed": "completed", "agent.failed": "failed", "agent.cancelled": "cancelled",
    "agent.retry": "retrying",
}
_ACTIVITIES = {
    "created": "Criado", "planning": "Planejando", "policy_check": "Verificando política",
    "queued": "Aguardando execução", "running": "Executando tarefa",
    "waiting_approval": "Aguardando aprovação", "evaluating": "Validando resultado",
    "retrying": "Tentando novamente", "paused": "Pausado", "completed": "Concluído",
    "failed": "Falhou", "cancelled": "Cancelado", "delegated": "Tarefa delegada",
    "unknown": "Estado não informado",
}


def _text(value: Any, *, limit: int = 128, fallback: str = "") -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    text = redact_text(value)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return " ".join(text.split())[:limit]


def _task(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("task", "objective", "message", "goal"):
        value = _text(data.get(key), limit=MAX_TASK_TEXT)
        if value:
            return value
    return ""


def _card(agent_id: str, name: str, task: str, status: str, updated_at: str) -> dict[str, Any]:
    return {"agent_id": agent_id, "name": name, "task": task, "status": status,
            "activity": _ACTIVITIES[status], "tool_name": None, "updated_at": updated_at}


def _project_run(run: Run, event_bus: Any) -> dict[str, Any]:
    parent_id = _text(run.agent_id, fallback="default")
    payload = run.input if isinstance(run.input, dict) else {}
    task = _task(payload) or "Tarefa não informada"
    spec = payload.get("agent_spec")
    name = "Bauer" if parent_id in {"serve.stream", "serve.chat", "serve.loop"} else parent_id
    if isinstance(spec, dict) and spec.get("id") == run.agent_id:
        name = _text(spec.get("name"), fallback=name)
    status = run.status if run.status in RUN_STATUSES else "unknown"
    parent = _card(parent_id, name, task, status, _text(run.updated_at))
    agents = {parent_id: parent}
    events = event_bus.list_events(run_id=run.id, limit=MAX_EVENTS_PER_RUN)
    latest_timestamp = _text(run.updated_at)
    for event in events:
        # Legacy lifecycle events may omit session_id; the authoritative run
        # association still prevents crossing sessions. Explicit mismatch drops.
        if event.run_id != run.id or (event.session_id and event.session_id != run.session_id):
            continue
        kind = event.event_type
        data = event.data if isinstance(event.data, dict) else {}
        if not (kind.startswith("agent.") or kind.startswith("tool.") or kind == "task.delegated"):
            continue
        raw_id = event.agent_id
        if kind == "task.delegated" and not raw_id:
            raw_id = data.get("assignee") or data.get("to_agent_id")
            if not raw_id:
                continue
        # A named agent can receive several delegations concurrently. The
        # delegation identity separates those task instances while name stays
        # the actual resolved agent name supplied by its lifecycle emitter.
        agent_id = _text(data.get("delegation_id")) or _text(raw_id, fallback=parent_id)
        member = agents.get(agent_id)
        timestamp = _text(event.timestamp, fallback=_text(run.updated_at))
        latest_timestamp = max(latest_timestamp, timestamp)
        if member is None:
            if len(agents) >= MAX_AGENTS:
                continue
            member = _card(agent_id, agent_id, "Tarefa não informada", "unknown", timestamp)
            agents[agent_id] = member
        member["name"] = _text(data.get("agent_name"), fallback=member["name"])
        member["updated_at"] = timestamp
        if kind == "task.delegated":
            delegated_task = _task(data) or _text(event.message, limit=MAX_TASK_TEXT)
            if delegated_task:
                member["task"] = delegated_task
            member["status"] = event.status if event.status in _MEMBER_STATUSES else "delegated"
            member["activity"] = _ACTIVITIES[member["status"]]
            member["tool_name"] = None
        elif kind in _LIFECYCLE or kind == "agent.finished":
            member["status"] = _LIFECYCLE.get(kind, event.status if event.status in _MEMBER_STATUSES else "unknown")
            member["activity"] = _ACTIVITIES[member["status"]]
            member["tool_name"] = None
        elif kind == "agent.message.sent":
            member["activity"] = "Respondendo"
        elif kind == "tool.call.requested":
            member["tool_name"] = _text(event.tool_name) or None
            member["activity"] = "Executando ferramenta"
        elif kind in {"tool.call.completed", "tool.call.failed", "tool.denied"}:
            tool = _text(event.tool_name) or None
            if member["tool_name"] is None or tool is None or member["tool_name"] == tool:
                member["tool_name"] = None
                member["activity"] = "Ferramenta concluída" if kind == "tool.call.completed" else "Ferramenta bloqueada" if kind == "tool.denied" else "Ferramenta falhou"
    # RunManager has custody of the parent outcome, even if an adapter declares
    # completion before the evaluator. Never declare an unfinished child done.
    parent["status"] = status
    parent["updated_at"] = max(parent["updated_at"], _text(run.updated_at))
    if status != "running":
        parent["activity"] = _ACTIVITIES[status]
        parent["tool_name"] = None
    if status in TERMINAL_RUN_STATUSES:
        for member in agents.values():
            member["tool_name"] = None
            if member is not parent and member["status"] not in TERMINAL_RUN_STATUSES:
                member["status"] = "unknown"
                member["activity"] = "Execução encerrada; estado do agente não informado"
    return {
        "run_id": _text(run.id), "task": task, "status": status,
        "agent_id": parent_id, "agent_name": parent["name"],
        "runtime": _text(run.runtime_adapter, fallback="unknown"),
        "started_at": _text(run.started_at), "updated_at": latest_timestamp,
        "agents": list(agents.values()),
    }


def build_chat_activity(run_manager: Any, event_bus: Any, session_id: str | None,
                        *, run_id: str | None = None) -> dict[str, Any]:
    """Return active runs plus the session's latest run, or a specific SSE run."""
    sid = session_id.strip() if isinstance(session_id, str) else ""
    envelope: dict[str, Any] = {"session_id": _text(sid) or None, "runs": []}
    if not sid:
        return envelope
    if run_id is not None:
        run = run_manager.get_run(run_id)
        selected = [run] if run is not None and run.session_id == sid else []
    else:
        runs = sorted((run for run in run_manager.list_runs() if run.session_id == sid),
                      key=lambda run: (run.started_at, run.id), reverse=True)
        selected = [run for index, run in enumerate(runs)
                    if index == 0 or run.status not in TERMINAL_RUN_STATUSES][:MAX_RUNS]
    envelope["runs"] = [_project_run(run, event_bus) for run in selected]
    return envelope
