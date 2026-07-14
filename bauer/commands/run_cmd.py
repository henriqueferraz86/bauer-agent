"""`bauer run` — entrada autônoma única: roda uma tarefa de ponta a ponta.

A porta da frente para "faça isso do início ao fim, sem eu ter que confirmar
cada passo". Fachada FINA (plano 022): a máquina de rodadas mora em
``serve_loop.run_loop_rounds`` (a MESMA da UI web), a governança no Kernel
(``kernel.admit`` quando ``kernel.enabled``), e este módulo só monta o
contexto, mostra os limites em PT e traduz o desfecho em exit code.

    cd C:\caminho\do\projeto
    bauer run "implemente o cadastro, rode os testes e corrija ate passar"

Decisões de segurança:
- workspace = CWD (recusa raiz/home/~/.bauer via ``is_sensitive_dir``);
- config = ``paths.config_path()`` (canônico) — NUNCA o ``config.yaml`` que
  por acaso exista na pasta do projeto;
- custo é ESTIMADO (o banner deixa explícito); tempo + nº de tools são os
  guardrails primários.
"""

from __future__ import annotations

from pathlib import Path

import typer

from ._common import console

# Exit codes (contrato estável — testado):
EXIT_OK = 0           # tarefa concluída (modelo confirmou)
EXIT_INCOMPLETE = 2   # parou sem concluir (budget/kill-switch/erro)
EXIT_INTERRUPTED = 130  # Ctrl+C


class _CostRecorder:
    """Sink do cost_meter para o `bauer run`: acumula o custo REAL de cada LLM
    call para alimentar o guardrail --max-cost e o display de custo. Mesmo
    contrato do sink do serve (provider, model, usage, cost_usd)."""

    def __init__(self) -> None:
        self.total_usd = 0.0

    def __call__(self, provider: str, model: str, usage: dict, cost_usd: float) -> None:
        self.total_usd += float(cost_usd or 0.0)


def run(
    task: str = typer.Argument("", help="A tarefa a executar de ponta a ponta"),
    workspace: Path = typer.Option(None, "--workspace", help="Pasta de trabalho (padrão: pasta atual)"),
    config: Path = typer.Option(None, "--config", help="config.yaml (padrão: ~/.bauer/config.yaml canônico)"),
    models: Path = typer.Option(None, "--models", help="models.yaml"),
    model: str = typer.Option("", "--model", help="Sobrescreve o modelo do config"),
    max_minutes: int = typer.Option(None, "--max-minutes", help="Teto de tempo (min)"),
    max_tool_calls: int = typer.Option(None, "--max-tool-calls", help="Teto de chamadas de ferramenta"),
    max_cost: float = typer.Option(None, "--max-cost", help="Teto de custo ESTIMADO (US$)"),
    approval: str = typer.Option(None, "--approval", help="threshold | deny_all | yolo"),
):
    """Executa uma tarefa autônoma até concluir, sem confirmar cada passo.

    Usa a PASTA ATUAL como workspace e o config canônico do Bauer
    (~/.bauer/config.yaml), ignorando qualquer config.yaml do projeto. Mostra os
    limites efetivos antes de começar; o custo exibido é ESTIMADO.

    Exemplos:
      bauer run "implemente a feature X, rode os testes e corrija ate passar"
      bauer run "refatore o modulo Y" --max-minutes 15 --approval yolo
    """
    if not task.strip():
        console.print("[red]Erro:[/red] informe a tarefa. Ex: [bold]bauer run \"faca X\"[/bold]")
        raise typer.Exit(code=1)

    from ..paths import config_path as _canonical_config, get_bauer_home
    from ..projects_registry import is_sensitive_dir

    ws = (workspace or Path.cwd()).resolve()
    if is_sensitive_dir(ws):
        console.print(
            f"[red]Recusando rodar em pasta sensível:[/red] {ws}\n"
            "[dim]Entre na pasta de um projeto (não a raiz do disco, sua home ou ~/.bauer).[/dim]"
        )
        raise typer.Exit(code=1)

    cfg_path = config or _canonical_config()
    models_path = models or (get_bauer_home() / "models.yaml")

    from ._runtime import _build_client, _build_router, _load_or_die
    try:
        cfg, _reg = _load_or_die(cfg_path, models_path)
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Falha ao carregar config:[/red] {exc}")
        raise typer.Exit(code=1)

    # Limites: CLI é dona da máquina → override SUBSTITUI o config (clamp=False).
    from ..serve_loop import resolve_loop_limits
    overrides = {"max_minutes": max_minutes, "max_tool_calls": max_tool_calls,
                 "max_cost_usd": max_cost, "approval_mode": approval}
    try:
        limits = resolve_loop_limits(cfg.loop, overrides, clamp_to_config=False)
    except ValueError as exc:
        console.print(f"[red]Limite inválido:[/red] {exc}")
        raise typer.Exit(code=1)

    model_name = (model or cfg.model.name).strip()
    client = _build_client(cfg)
    router = _build_router(cfg, ws, llm_client=client)

    from .agent_cmd import _build_fallback_clients
    fallback_clients = _build_fallback_clients(cfg, console=console) or None

    # Contexto do turno (mesmo padrão do serve): system prompt do router.
    from ..agent import _build_system_prompt, run_one_turn_with_fallback
    from ..context_manager import ContextManager
    applied_context = int(getattr(cfg.model, "requested_context", 0) or 8192)
    ctx = ContextManager(applied_context=applied_context,
                         system_prompt=_build_system_prompt(router))

    # Kernel: governa quando ligado no config (mesma admissão da web).
    kernel = None
    try:
        from ..core.kernel import build_kernel, kernel_enabled
        if kernel_enabled(cfg):
            kernel = build_kernel(cfg, workspace=str(ws))
    except Exception as exc:  # noqa: BLE001 — kernel é opt-in; nunca bloqueia o run
        from ..logging_config import log_suppressed
        log_suppressed("run_cmd.kernel_wiring", exc)

    # Aprovação headless (o /loop nunca para pra perguntar; o modo controla o
    # que é auto-aprovado vs. auto-negado).
    from ..headless_approval import HeadlessApprovalConfig, HeadlessApprovalEngine
    engine = HeadlessApprovalEngine(HeadlessApprovalConfig(
        mode=limits.approval_mode,
        risk_threshold=float(getattr(cfg.loop, "approval_risk_threshold", 0.4)),
    ))

    _banner(ws, model_name, cfg.model.provider, limits, kernel is not None)

    from ..autonomous_budget import AutonomousBudget
    from ..serve_loop import run_loop_rounds
    budget = AutonomousBudget(
        max_cost_usd=limits.max_cost_usd,
        max_wall_seconds=limits.max_minutes * 60,
        max_tool_calls=limits.max_tool_calls,
    )

    kill_control = None
    admitted_run = None
    if kernel is not None:
        from ..core.kernel import KernelRequest
        from ..core.runtime.resilience import RuntimeControl
        kill_control = RuntimeControl(store=kernel.runs.store)
        admitted_run, early = kernel.admit(KernelRequest(
            task=task, agent_id="cli.run", input={"endpoint": "bauer run", "workspace": str(ws)},
        ))
        if early is not None:
            reason = early.error or early.policy_reason or early.status
            console.print(f"[red]⛔ Bloqueado antes de iniciar:[/red] {reason}")
            raise typer.Exit(code=EXIT_INCOMPLETE)
        kernel.runs.start_run(admitted_run.id)

    def _turn_fn():
        return run_one_turn_with_fallback(ctx, router, client, model_name, fallback_clients)

    def _should_stop():
        if kill_control is not None and kill_control.kill_switch_enabled():
            return "kill_switch"
        return None

    # Custo REAL por rodada → budget. Sem este sink, budget.consume_cost() nunca
    # era chamado: o guardrail --max-cost não disparava e o display mostrava
    # sempre ~US$ 0.000 (o banner promete "OU ~US$ X ESTIMADO"). Mesmo padrão do
    # /loop da web (server._loop_worker): cost_sink acumula, on_round consome o
    # delta no budget.
    from ..cost_meter import cost_sink
    _cost = _CostRecorder()
    _cost_token = cost_sink.set(_cost)
    _last_cost = 0.0

    def _on_round(n: int, text: str, tl: list) -> None:
        nonlocal _last_cost
        delta = _cost.total_usd - _last_cost
        _last_cost = _cost.total_usd
        if delta > 0:
            try:
                budget.consume_cost(delta)
            except Exception as exc:  # esgotou: run_loop_rounds encerra no topo
                from ..logging_config import log_suppressed
                log_suppressed("run_cmd.consume_cost", exc)
        _print_round(n, budget, tl)

    router._approval_callback = engine.make_approval_callback()
    stop_reason = "completed"
    rounds = 0
    last_text = ""
    tool_log: list = []
    try:
        stop_reason, rounds, last_text, tool_log = run_loop_rounds(
            goal=task, ctx=ctx, turn_fn=_turn_fn, budget=budget,
            should_stop=_should_stop,
            on_round=_on_round,
        )
    except KeyboardInterrupt:
        stop_reason = "interrupted"
    finally:
        cost_sink.reset(_cost_token)
        router._approval_callback = None
        if admitted_run is not None:
            _finalize_run(kernel, admitted_run.id, stop_reason, last_text, tool_log, budget)

    _summary(stop_reason, rounds, budget)

    if stop_reason == "interrupted":
        raise typer.Exit(code=EXIT_INTERRUPTED)
    if stop_reason != "completed":
        raise typer.Exit(code=EXIT_INCOMPLETE)
    raise typer.Exit(code=EXIT_OK)


def _banner(ws: Path, model: str, provider: str, limits, governed: bool) -> None:
    from rich.panel import Panel
    body = (
        f"[bold]Pasta:[/bold] {ws}\n"
        f"[bold]Modelo:[/bold] {model} [dim]({provider})[/dim]\n"
        f"[bold]Aprovação:[/bold] {limits.approval_mode}"
        f"{'  ·  governado pelo Kernel' if governed else ''}\n"
        f"[bold]Limites:[/bold] {limits.banner_pt()}.\n"
        f"[dim]Ctrl+C interrompe. Custo é ESTIMADO (depende de uso + tabela de preços).[/dim]"
    )
    console.print(Panel(body, title="▶ bauer run", border_style="cyan"))


def _print_round(n: int, budget, tool_log: list) -> None:
    snap = budget.snapshot()
    mins, secs = divmod(int(snap.elapsed_seconds), 60)
    console.print(
        f"[dim][rodada {n}] {snap.tool_calls}/{snap.max_tool_calls} tools · "
        f"{mins}m{secs:02d}s/{budget.max_wall_seconds // 60}m · "
        f"~US$ {snap.cost_usd:.3f}/{snap.max_cost_usd:.2f}[/dim]"
    )


def _summary(stop_reason: str, rounds: int, budget) -> None:
    labels = {
        "completed": "[green]✓ Tarefa concluída[/green]",
        "budget_exhausted": "[yellow]⏱ Orçamento esgotado[/yellow]",
        "kill_switch": "[red]■ Interrompido pelo kill-switch[/red]",
        "cancelled": "[red]■ Cancelado[/red]",
        "provider_error": "[red]✗ Erro de provider[/red]",
        "empty_response": "[yellow]Resposta vazia — parei[/yellow]",
        "max_rounds": "[yellow]Teto de rodadas atingido[/yellow]",
        "interrupted": "[red]■ Interrompido (Ctrl+C)[/red]",
    }
    snap = budget.snapshot()
    console.print(
        f"\n{labels.get(stop_reason, stop_reason)} "
        f"[dim]· {rounds} rodadas · {snap.tool_calls} tools · "
        f"~US$ {snap.cost_usd:.3f} estimado[/dim]"
    )


def _finalize_run(kernel, run_id: str, stop_reason: str, last_text: str,
                  tool_log: list, budget) -> None:
    """Fecha o Run no Kernel conforme o desfecho (o admit deixou em running)."""
    try:
        snap = budget.snapshot()
        cost = round(snap.cost_usd, 6)
        if stop_reason == "completed":
            kernel.runs.complete_run(run_id, output={"response": last_text},
                                     tool_calls_count=snap.tool_calls, cost_estimate=cost)
        elif stop_reason == "kill_switch":
            kernel.runs.update_run(run_id, status="cancelled", error="runtime kill switch ativo")
        else:
            kernel.runs.fail_run(run_id, f"bauer run parou: {stop_reason}")
    except Exception as exc:  # noqa: BLE001 — finalização best-effort
        from ..logging_config import log_suppressed
        log_suppressed("run_cmd.finalize", exc)
