"""Auditoria estruturada de UMA run — lê run + eventos e monta um RunAudit.

Read-only sobre dados já persistidos (RunManager + EventBus). Deriva
skills/tools/commands/files/policy dos eventos do run_id."""

from __future__ import annotations

from pathlib import Path
from dataclasses import asdict

from ._common import (
    COMMAND_TOOLS,
    FILE_MUTATING_TOOLS,
    duration_ms,
    event_arg,
)
from .schemas import PolicyDecision, RunAudit


def audit_run(
    runtime_root: str | Path,
    run_id: str,
    *,
    include_events: bool = False,
    include_tools: bool = False,
    include_policy: bool = True,
) -> RunAudit | None:
    """RunAudit de `run_id`, ou None se a run não existe."""
    from ..events import EventBus
    from ..runtime.run_manager import RunManager

    run = RunManager(root=runtime_root).get_run(run_id)
    if run is None:
        return None

    events = EventBus(root=runtime_root).list_events(run_id=run_id)

    audit = RunAudit(
        run_id=run.id,
        status=run.status,
        agent_id=run.agent_id,
        runtime_adapter=run.runtime_adapter,
        started_at=run.started_at,
        finished_at=getattr(run, "finished_at", None),
        duration_ms=duration_ms(
            run.started_at, getattr(run, "finished_at", None), getattr(run, "updated_at", None)
        ),
        prompt=str((run.input or {}).get("message", "")),
        final_answer=str((run.output or {}).get("response", "")) if run.output else "",
        error=run.error,
        cost_estimate=getattr(run, "cost_estimate", None),
        events_total=len(events),
    )

    seen_skills: set[str] = set()
    seen_tools: set[str] = set()
    for ev in events:
        et = ev.event_type
        event_payload = asdict(ev)
        if include_events:
            audit.event_details.append(event_payload)
        if et in ("skill.selected", "skill.executed") and ev.skill_id:
            if ev.skill_id not in seen_skills:
                seen_skills.add(ev.skill_id)
                audit.skills_used.append(ev.skill_id)
        elif et in ("tool.call.completed", "tool.call.failed") and ev.tool_name:
            if include_tools:
                audit.tool_call_details.append(event_payload)
            if ev.tool_name not in seen_tools:
                seen_tools.add(ev.tool_name)
                audit.tools_used.append(ev.tool_name)
            data = ev.data or {}
            if ev.tool_name in COMMAND_TOOLS:
                cmd = event_arg(data, "command")
                if cmd:
                    audit.commands_executed.append(cmd)
            if ev.tool_name in FILE_MUTATING_TOOLS:
                path = event_arg(data, "path") or event_arg(data, "dst")
                if path:
                    audit.files_changed.append(path)
        elif et == "policy.evaluated":
            if not include_policy:
                continue
            data = ev.data or {}
            audit.policy_decisions.append(PolicyDecision(
                operation=str(data.get("operation", "")),
                action=str(ev.status or ""),
                risk_level=str(data.get("risk_level", "")),
                tool_name=str(ev.tool_name or ""),
            ))
        elif et in ("approval.requested", "approval.accepted", "approval.denied"):
            audit.approvals.append({
                "type": et,
                "tool_name": ev.tool_name or "",
                "status": ev.status or "",
                "message": ev.message or "",
            })

    return audit
