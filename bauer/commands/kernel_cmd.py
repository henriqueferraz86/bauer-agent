"""Comandos do Bauer Kernel — executa e opera runs pela fachada única.

`bauer kernel run` é o trilho unificado: created → planning → policy_check →
queued → running → completed, com estados persistidos e eventos auditáveis
(`bauer runs list` / `bauer audit run <id>` enxergam tudo).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ._common import console

kernel_app = typer.Typer(help="Bauer Kernel: executa e opera runs pela fachada unica.")


def _build(config: Path, state_dir: Path, *, with_policy: bool):
    from ..config_loader import load_config
    from ..core.kernel import build_kernel

    cfg = None
    try:
        cfg = load_config(config)
    except Exception:  # noqa: BLE001 — kernel funciona sem config (defaults)
        cfg = None
    workspace = "workspace"
    try:
        if cfg is not None and getattr(cfg.agent, "workspace", ""):
            workspace = str(cfg.agent.workspace)
    except Exception:  # noqa: BLE001
        pass
    return build_kernel(cfg, root=str(state_dir), workspace=workspace,
                        with_policy=with_policy), cfg


@kernel_app.command("run")
def kernel_run_cmd(
    task: str = typer.Argument(..., help="Tarefa a executar"),
    agent_id: str = typer.Option("default", "--agent"),
    adapter: str = typer.Option("", "--adapter", help="Runtime adapter (vazio = default do config)"),
    model: str = typer.Option("", "--model", help="Modelo (vazio = model.name do config)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
    no_policy: bool = typer.Option(False, "--no-policy", help="Pula o policy_check (debug)"),
):
    """Executa uma tarefa de ponta a ponta pelo Kernel (estados + policy + eventos)."""
    from ..core.kernel import KernelRequest

    kernel, cfg = _build(config, state_dir, with_policy=not no_policy)

    # bauer_native exige client/model no payload — monta a partir do config.
    request_input: dict = {}
    resolved_adapter = adapter or (getattr(getattr(cfg, "runtime", None), "default_adapter", "") or "")
    if (resolved_adapter or "bauer_native") == "bauer_native":
        try:
            from ._runtime import _build_client
            request_input["client"] = _build_client(cfg)
            request_input["model"] = model or cfg.model.name
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Nao consegui montar o client:[/red] {exc}")
            raise typer.Exit(code=1)

    ksec = getattr(cfg, "kernel", None)
    out = kernel.execute(KernelRequest(
        task=task, agent_id=agent_id, runtime_adapter=adapter, input=request_input,
        max_retries=int(getattr(ksec, "max_retries", 0) or 0),
        retry_backoff_s=float(getattr(ksec, "retry_backoff_s", 0.0) or 0.0),
        fallback_adapters=list(getattr(ksec, "fallback_adapters", None) or []),
    ))

    color = {"completed": "green", "waiting_approval": "yellow"}.get(out.status, "red")
    console.print(f"[{color}]{out.status}[/{color}] run={out.run_id}")
    console.print(f"[dim]estados: {' → '.join(out.trajectory)}[/dim]")
    if out.policy_action:
        console.print(f"[dim]policy: {out.policy_action} — {out.policy_reason}[/dim]")
    if out.error:
        console.print(f"[red]{out.error}[/red]")
    if out.output:
        console.print(str(out.output))
    if out.status not in {"completed", "waiting_approval"}:
        raise typer.Exit(code=1)


@kernel_app.command("pause")
def kernel_pause_cmd(
    run_id: str = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Pausa um run em execução (running → paused)."""
    kernel, _ = _build(config, state_dir, with_policy=False)
    try:
        result = kernel.pause(run_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]paused[/yellow] {run_id} (adapter: {result['adapter'].get('status')})")


@kernel_app.command("resume")
def kernel_resume_cmd(
    run_id: str = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Retoma um run pausado/aguardando aprovação (→ queued)."""
    kernel, _ = _build(config, state_dir, with_policy=False)
    try:
        result = kernel.resume(run_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]queued[/green] {run_id} (adapter: {result['adapter'].get('status')})")


@kernel_app.command("cancel")
def kernel_cancel_cmd(
    run_id: str = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Cancela um run (idempotente em estados terminais)."""
    kernel, _ = _build(config, state_dir, with_policy=False)
    try:
        result = kernel.cancel(run_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[red]{result['status']}[/red] {run_id}")


@kernel_app.command("recover")
def kernel_recover_cmd(
    max_age_s: int = typer.Option(900, "--max-age-s", help="Idade minima (s) p/ considerar preso"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Recupera runs presos em estados nao-terminais (pos-restart/crash)."""
    kernel, _ = _build(config, state_dir, with_policy=False)
    recovered = kernel.recover(max_age_s=max_age_s)
    if not recovered:
        console.print("[green]Nenhum run preso.[/green]")
        return
    for item in recovered:
        console.print(f"[yellow]recuperado[/yellow] {item['run_id']} → {item['status']}")


@kernel_app.command("approve")
def kernel_approve_cmd(
    approval_id: str = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Aprova um run em waiting_approval e continua a execução."""
    kernel, cfg = _build(config, state_dir, with_policy=False)
    # Re-injeta o client (não persiste no JSONL) p/ o continue_run do bauer_native
    extra: dict = {}
    try:
        from ._runtime import _build_client
        extra = {"client": _build_client(cfg), "model": cfg.model.name}
    except Exception:  # noqa: BLE001 — sem client, o run volta p/ queued
        pass
    try:
        out = kernel.approve(approval_id, continue_with=extra or None)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if isinstance(out, dict):
        console.print(f"[green]approved[/green] {approval_id} → run {out.get('run_id')} queued")
        return
    color = "green" if out.ok else "red"
    console.print(f"[{color}]{out.status}[/{color}] run={out.run_id}")
    if out.output:
        console.print(str(out.output))


@kernel_app.command("deny")
def kernel_deny_cmd(
    approval_id: str = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Nega uma aprovação pendente (run → failed)."""
    kernel, _ = _build(config, state_dir, with_policy=False)
    try:
        out = kernel.deny(approval_id)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[red]denied[/red] {approval_id} (run {out.get('run_id')})")


@kernel_app.command("health")
def kernel_health_cmd(
    adapter: str = typer.Option("", "--adapter", help="Adapter (vazio = default do config)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Healthcheck do runtime adapter."""
    kernel, _ = _build(config, state_dir, with_policy=False)
    result = kernel.healthcheck(adapter or None)
    console.print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") == "unhealthy":
        raise typer.Exit(code=1)
