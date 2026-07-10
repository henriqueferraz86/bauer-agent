"""Comandos `bauer audit` — auditoria e score de runs (Fase 11 MVP).

Read-only: agrega dados já persistidos pelo runtime. Não altera execução.
  bauer audit report [--last 24h] [--format table|json] [--output f]
  bauer audit run <run_id> [--format table|json]
  bauer audit score <run_id> [--format table|json]
  bauer audit architecture [--since main] [--changed-files] [--format json]
  bauer audit weekly [--last 7d] [--output reports/weekly.md]
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.table import Table

from ._common import console

audit_app = typer.Typer(help="Auditoria de runs: relatório geral, detalhe e score.")

_DEFAULT_STATE_DIR = Path("memory/runtime")


def _parse_last(last: str) -> "datetime | None":
    """'24h' / '7d' / '30m' / '2w' → datetime de corte (naive local). Vazio → None."""
    if not last:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", last.lower())
    if not m:
        raise typer.BadParameter("Use formatos como 24h, 7d, 30m, 2w.")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
             "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return datetime.now() - delta


def _emit(payload: dict, fmt: str, output: "Path | None") -> None:
    """Saída JSON comum (usada por --format json e --output)."""
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]Salvo em[/green] {output}")
    else:
        console.file.write(text + "\n")
        console.file.flush()


@audit_app.command("report")
def audit_report(
    last: str = typer.Option("", "--last", help="Janela: 24h, 7d, 30m, 2w (vazio = tudo)."),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    output: Path = typer.Option(None, "--output", help="Salva JSON no arquivo."),
    state_dir: Path = typer.Option(_DEFAULT_STATE_DIR, "--state-dir"),
):
    """Relatório geral de auditoria das runs."""
    from ..core.audit import build_report

    since = _parse_last(last)
    report = build_report(state_dir, since=since, window_label=last or "all")

    if fmt == "json" or output is not None:
        _emit(asdict(report), fmt, output)
        return

    console.print(f"[bold]Bauer Audit Report[/bold] — janela: {report.window}\n")

    t = Table(show_header=False, box=None)
    t.add_row("Runs total", str(report.runs_total))
    t.add_row("  completed", str(report.runs_completed))
    t.add_row("  failed", str(report.runs_failed))
    t.add_row("  cancelled", str(report.runs_cancelled))
    t.add_row("  waiting_approval", str(report.runs_waiting_approval))
    t.add_row("Success rate", f"{report.success_rate * 100:.1f}%")
    if report.average_duration_ms is not None:
        t.add_row("Duração média", f"{report.average_duration_ms / 1000:.2f}s")
    t.add_row("Custo estimado", f"${report.estimated_cost_usd:.4f}")
    t.add_row("Approvals pendentes", str(report.approvals_pending))
    t.add_row("Policy allow/ask/deny", f"{report.policy_allow}/{report.policy_ask}/{report.policy_deny}")
    console.print(t)

    _print_top(report.most_used_skills, "Skills mais usadas")
    _print_top(report.most_failed_skills, "Skills com falha")
    _print_top(report.most_used_tools, "Tools mais usadas")
    _print_top(report.most_used_agents, "Agents mais usados")
    _print_top(report.top_errors, "Falhas principais")


def _print_top(items: list, title: str) -> None:
    if not items:
        return
    console.print(f"\n[bold]{title}[/bold]")
    for name, count in items:
        console.print(f"  {count:>3}  {name}")


@audit_app.command("run")
def audit_run_cmd(
    run_id: str = typer.Argument(...),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    include_events: bool = typer.Option(False, "--include-events", help="Inclui eventos brutos."),
    include_tools: bool = typer.Option(False, "--include-tools", help="Inclui tool calls detalhadas."),
    include_policy: bool = typer.Option(True, "--include-policy/--no-include-policy", help="Inclui decisoes de policy."),
    state_dir: Path = typer.Option(_DEFAULT_STATE_DIR, "--state-dir"),
):
    """Auditoria estruturada de uma run específica (+ score)."""
    from ..core.audit import audit_run, score_run

    audit = audit_run(
        state_dir,
        run_id,
        include_events=include_events,
        include_tools=include_tools,
        include_policy=include_policy,
    )
    if audit is None:
        console.print(f"[red]Run não encontrada:[/red] {run_id}")
        raise typer.Exit(code=1)
    score = score_run(audit)

    if fmt == "json":
        payload = asdict(audit)
        payload["score"] = asdict(score)
        _emit(payload, fmt, None)
        return

    console.print(f"[bold]Run[/bold] {audit.run_id}  [dim]({audit.status})[/dim]")
    console.print(f"  agent: {audit.agent_id or '-'}   adapter: {audit.runtime_adapter or '-'}")
    if audit.duration_ms is not None:
        console.print(f"  duração: {audit.duration_ms / 1000:.2f}s   custo: ${audit.cost_estimate or 0:.4f}")
    if audit.prompt:
        console.print(f"\n[bold]Prompt[/bold]\n  {audit.prompt[:300]}")
    _print_list(audit.skills_used, "Skills")
    _print_list(audit.tools_used, "Tools")
    _print_list(audit.commands_executed, "Comandos")
    _print_list(audit.files_changed, "Arquivos alterados")
    if audit.policy_decisions:
        console.print("\n[bold]Policy[/bold]")
        for d in audit.policy_decisions:
            console.print(f"  {d.action:>5}  {d.operation or d.tool_name}  [dim]({d.risk_level})[/dim]")
    if audit.approvals:
        console.print("\n[bold]Approvals[/bold]")
        for a in audit.approvals:
            console.print(f"  {a['type']}  {a['tool_name']}  {a['status']}")
    if audit.error:
        console.print(f"\n[red]Erro:[/red] {audit.error}")
    if audit.final_answer:
        console.print(f"\n[bold]Resposta final[/bold]\n  {audit.final_answer[:400]}")
    if audit.tool_call_details:
        console.print("\n[bold]Tool calls detalhadas[/bold]")
        for item in audit.tool_call_details:
            console.print(f"  {item.get('event_type')}  {item.get('tool_name') or '-'}  {item.get('status') or '-'}")
    if audit.event_details:
        console.print("\n[bold]Eventos[/bold]")
        for item in audit.event_details:
            console.print(f"  {item.get('timestamp')}  {item.get('event_type')}  {item.get('status') or ''}")

    _print_score(score)


@audit_app.command("score")
def audit_score_cmd(
    run_id: str = typer.Argument(...),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    state_dir: Path = typer.Option(_DEFAULT_STATE_DIR, "--state-dir"),
):
    """Nota heurística 0–5 de uma run (sem LLM)."""
    from ..core.audit import score_run_by_id

    score = score_run_by_id(state_dir, run_id)
    if score is None:
        console.print(f"[red]Run não encontrada:[/red] {run_id}")
        raise typer.Exit(code=1)
    if fmt == "json":
        _emit(asdict(score), fmt, None)
        return
    _print_score(score)


@audit_app.command("architecture")
def audit_architecture_cmd(
    since: str = typer.Option("", "--since", help="Base git para diff, ex.: main."),
    changed_files: bool = typer.Option(False, "--changed-files", help="Audita apenas arquivos alterados no git diff."),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    project_root: Path = typer.Option(Path("."), "--project-root"),
):
    """Auditoria arquitetural estatica: regras simples, apenas alertas."""
    from ..core.audit import audit_architecture

    report = audit_architecture(project_root, since=since, changed_files_only=changed_files)
    if fmt == "json":
        _emit(asdict(report), fmt, None)
        return

    tone = "green" if report.status == "approved" else "yellow"
    console.print(f"[bold]Bauer Architecture Audit[/bold]\nStatus: [{tone}]{report.status}[/{tone}]")
    console.print(f"[dim]Arquivos analisados: {len(report.scanned_files)}[/dim]")

    if report.critical:
        console.print("\n[bold red]Critical[/bold red]")
        for finding in report.critical:
            _print_finding(finding)
    if report.warnings:
        console.print("\n[bold yellow]Warnings[/bold yellow]")
        for finding in report.warnings:
            _print_finding(finding)
    if report.recommendations:
        console.print("\n[bold]Recomendações[/bold]")
        for rec in report.recommendations:
            console.print(f"  - {rec}")
    if not report.critical and not report.warnings:
        console.print("\n[green]Nenhum risco arquitetural encontrado pelas regras atuais.[/green]")


@audit_app.command("weekly")
def audit_weekly_cmd(
    last: str = typer.Option("7d", "--last", help="Janela: 24h, 7d, 2w, 30d."),
    output: Path = typer.Option(None, "--output", help="Salva o relatorio Markdown."),
    state_dir: Path = typer.Option(_DEFAULT_STATE_DIR, "--state-dir"),
):
    """Gera a revisao semanal de governanca em Markdown."""
    from ..core.audit import build_weekly_report

    markdown = build_weekly_report(
        state_dir,
        since=_parse_last(last),
        window_label=last,
    )
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown, encoding="utf-8")
        console.print(f"[green]Salvo em[/green] {output}")
        return
    console.file.write(markdown)
    console.file.flush()


def _print_list(items: list, title: str) -> None:
    if not items:
        return
    console.print(f"\n[bold]{title}[/bold]")
    for it in items:
        console.print(f"  - {it}")


def _print_score(score) -> None:
    tone = "green" if score.score >= 4 else "yellow" if score.score >= 3 else "red"
    console.print(f"\n[bold {tone}]Score: {score.score}/{score.max_score}[/bold {tone}]")
    for r in score.reasons:
        console.print(f"  [green]+[/green] {r}")
    for w in score.warnings:
        console.print(f"  [red]-[/red] {w}")


def _print_finding(finding) -> None:
    location = finding.file
    if finding.line:
        location = f"{location}:{finding.line}"
    console.print(f"  - [bold]{finding.rule}[/bold] {location}")
    console.print(f"    {finding.message}")
