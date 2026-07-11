"""CLI for Phase 11 scenario-driven runtime benchmarks."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer
from rich.table import Table

from ._common import console

benchmark_app = typer.Typer(help="Benchmarks end-to-end do runtime Bauer.")


@benchmark_app.command("run")
def benchmark_run_cmd(
    scenario: str = typer.Option("", "--scenario", help="Executa apenas um scenario pelo id."),
    run_all: bool = typer.Option(False, "--all", help="Executa todos os scenarios aplicaveis."),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    scenarios_dir: Path = typer.Option(Path("benchmarks"), "--scenarios-dir"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
    workspace_root: Path = typer.Option(Path(".bauer/benchmark-workspaces"), "--workspace-root"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
) -> None:
    """Executa scenarios YAML como runs reais e aplica o RunScore."""
    from ..core.audit import load_benchmark_scenarios, run_benchmark_suite

    scenarios = load_benchmark_scenarios(scenarios_dir)
    if scenario:
        scenarios = [item for item in scenarios if item.id == scenario]
        if not scenarios:
            console.print(f"[red]Scenario nao encontrado:[/red] {scenario}")
            raise typer.Exit(code=1)
    elif not run_all:
        run_all = True
    if not scenarios:
        console.print(f"[red]Nenhum scenario em[/red] {scenarios_dir}")
        raise typer.Exit(code=1)

    execute = _build_executor(state_dir, config, models)
    report = run_benchmark_suite(
        state_dir,
        scenarios,
        execute,
        workspace_root=workspace_root,
    )
    _print_report(report, fmt)
    if report.failed:
        raise typer.Exit(code=1)


@benchmark_app.command("report")
def benchmark_report_cmd(
    limit: int = typer.Option(20, "--limit"),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    """Mostra o historico dos benchmarks da Fase 11."""
    from ..core.audit import list_benchmark_reports

    reports = list_benchmark_reports(state_dir, limit=limit)
    if fmt == "json":
        console.file.write(json.dumps(reports, ensure_ascii=False, indent=2) + "\n")
        console.file.flush()
        return
    table = Table(title="Bauer Benchmark History")
    table.add_column("Started")
    table.add_column("Suite")
    table.add_column("Passed", justify="right")
    table.add_column("Failed", justify="right")
    for report in reports:
        table.add_row(
            str(report.get("started_at", "")),
            str(report.get("id", "")),
            str(report.get("passed", 0)),
            str(report.get("failed", 0)),
        )
    console.print(table)


def _build_executor(state_dir: Path, config: Path, models: Path):
    from ._runtime import _build_client, _load_or_die
    from ..agent import run_one_turn
    from ..context_manager import ContextManager
    from ..core.events import EventBus
    from ..core.runtime import RunManager, SessionManager
    from ..tool_router import ToolRouter, reset_runtime_ids, set_runtime_ids

    cfg, _ = _load_or_die(config, models)
    client = _build_client(cfg)
    bus = EventBus(root=state_dir)
    runs = RunManager(root=state_dir, event_bus=bus)
    sessions = SessionManager(root=state_dir)

    def execute(item, workspace: Path) -> str:
        session = sessions.create_session(user_id="benchmark", agent_id="benchmark")
        run = runs.create_run(
            session_id=session.id,
            agent_id="benchmark",
            runtime_adapter=str(getattr(getattr(cfg, "runtime", None), "default_adapter", "bauer_native")),
            input={"message": item.prompt, "benchmark_scenario": item.id},
            status="running",
        )
        router = ToolRouter(workspace=workspace)
        router._event_bus = bus
        router._policy_root = state_dir
        context = ContextManager(
            applied_context=int(getattr(cfg.model, "requested_context", 32768)),
            provider=str(cfg.model.provider),
        )
        context.add_user(item.prompt)
        token = set_runtime_ids(session.id, run.id)
        try:
            response, tool_log = run_one_turn(context, router, client, cfg.model.name)
            runs.complete_run(
                run.id,
                output={"response": response},
                tool_calls_count=len(tool_log),
            )
        except Exception as exc:
            runs.fail_run(run.id, str(exc))
        finally:
            reset_runtime_ids(token)
        return run.id

    return execute


def _print_report(report, fmt: str) -> None:
    if fmt == "json":
        console.file.write(json.dumps(asdict(report), ensure_ascii=False, indent=2) + "\n")
        console.file.flush()
        return
    table = Table(title=f"Bauer Benchmark - {report.passed}/{report.total} aprovados")
    table.add_column("Scenario")
    table.add_column("Run")
    table.add_column("Score", justify="right")
    table.add_column("Status")
    table.add_column("Falhas")
    for item in report.results:
        table.add_row(
            item.scenario_id,
            item.run_id,
            f"{item.score}/{item.min_score}",
            "aprovado" if item.passed else "reprovado",
            "; ".join(item.failures),
        )
    console.print(table)
