"""Relatório geral de auditoria — agrega runs, eventos, approvals e custo.

Read-only sobre dados já persistidos. `since` filtra runs por started_at."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path

from ._common import duration_ms, parse_iso
from .schemas import AuditReport

_TERMINAL = {"completed", "failed", "cancelled"}


def build_report(
    runtime_root: str | Path,
    *,
    since: datetime | None = None,
    window_label: str = "all",
    top_n: int = 5,
) -> AuditReport:
    """Monta o AuditReport lendo runs/eventos/approvals do runtime_root."""
    from ..events import EventBus
    from ..policy import ApprovalManager
    from ..runtime.run_manager import RunManager

    runs = RunManager(root=runtime_root).list_runs()
    if since is not None:
        runs = [r for r in runs if _after(r.started_at, since)]

    report = AuditReport(window=window_label, runs_total=len(runs))

    durations: list[float] = []
    skills: Counter[str] = Counter()
    failed_skills: Counter[str] = Counter()
    agents: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    adapters: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    run_ids: set[str] = set()

    for r in runs:
        run_ids.add(r.id)
        if r.status == "completed":
            report.runs_completed += 1
        elif r.status == "failed":
            report.runs_failed += 1
            if r.error:
                errors[_error_bucket(r.error)] += 1
        elif r.status == "cancelled":
            report.runs_cancelled += 1
        elif r.status == "waiting_approval":
            report.runs_waiting_approval += 1

        if r.agent_id:
            agents[r.agent_id] += 1
        if r.runtime_adapter:
            adapters[r.runtime_adapter] += 1
        if getattr(r, "cost_estimate", None):
            report.estimated_cost_usd += float(r.cost_estimate or 0.0)

        d = duration_ms(r.started_at, getattr(r, "finished_at", None), getattr(r, "updated_at", None))
        if d is not None and r.status in _TERMINAL:
            durations.append(d)

    terminal = report.runs_completed + report.runs_failed + report.runs_cancelled
    report.success_rate = round(report.runs_completed / terminal, 4) if terminal else 0.0
    report.average_duration_ms = round(sum(durations) / len(durations), 2) if durations else None
    report.estimated_cost_usd = round(report.estimated_cost_usd, 6)

    # Eventos: policy allow/ask/deny + skills/tools usados (escopados às runs da janela).
    for ev in EventBus(root=runtime_root).list_events():
        if since is not None and ev.run_id is not None and ev.run_id not in run_ids:
            continue
        et = ev.event_type
        if et == "policy.evaluated":
            action = (ev.status or "").lower()
            if action == "allow":
                report.policy_allow += 1
            elif action == "ask":
                report.policy_ask += 1
            elif action == "deny":
                report.policy_deny += 1
        elif et in ("skill.selected", "skill.executed") and ev.skill_id:
            skills[ev.skill_id] += 1
            if et == "skill.executed" and (ev.status or "").lower() == "failed":
                failed_skills[ev.skill_id] += 1
                if ev.message:
                    errors[_error_bucket(ev.message)] += 1
        elif et == "tool.call.failed":
            if ev.skill_id:
                failed_skills[ev.skill_id] += 1
            if ev.tool_name:
                tools[ev.tool_name] += 1
            if ev.message:
                errors[_error_bucket(ev.message)] += 1
        elif et == "tool.call.completed" and ev.tool_name:
            tools[ev.tool_name] += 1

    try:
        report.approvals_pending = len(ApprovalManager(root=runtime_root).list(status="pending"))
    except Exception:  # noqa: BLE001
        report.approvals_pending = 0

    report.most_used_skills = skills.most_common(top_n)
    report.most_failed_skills = failed_skills.most_common(top_n)
    report.most_used_agents = agents.most_common(top_n)
    report.most_used_tools = tools.most_common(top_n)
    report.runtime_adapters = adapters.most_common(top_n)
    report.top_errors = errors.most_common(top_n)
    return report


def _after(ts: str, since: datetime) -> bool:
    parsed = parse_iso(ts)
    if parsed is None:
        return True  # sem timestamp legível → não filtra fora (fail-open)
    # since pode ser naive; compara em UTC-agnóstico removendo tzinfo se necessário
    if parsed.tzinfo is not None and since.tzinfo is None:
        parsed = parsed.replace(tzinfo=None)
    elif parsed.tzinfo is None and since.tzinfo is not None:
        since = since.replace(tzinfo=None)
    return parsed >= since


def _error_bucket(error: str) -> str:
    """Agrupa erros parecidos por um prefixo curto (evita 1 bucket por mensagem única)."""
    e = str(error).strip().splitlines()[0] if error else "erro"
    return e[:60]
