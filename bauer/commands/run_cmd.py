r"""`bauer run` — entrada autônoma única: roda uma tarefa de ponta a ponta.

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

    from ._runtime import (
        _apply_ollama_runtime, _build_client, _build_router, _load_or_die,
        heuristic_route_kit,
    )
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

    # Isolamento (S12 nível 1): quando o contrato pede `isolation: worktree`, o
    # agente passa a trabalhar num branch dedicado e o master não é tocado. A
    # regra vem do CONTRATO, não é geral — isolar todo run quebraria o fluxo de
    # fix pequeno direto no master. Sem contrato, nada muda.
    from ..core.workspace import preparar as _preparar_ambiente
    from ..core.workspace.isolation import contrato_do_workspace
    from uuid import uuid4 as _uuid4

    # Estado de GOVERNANÇA fica no repo, não no worktree. O isolamento troca
    # onde o TRABALHO acontece; runs, kill-switch, aprovações e orçamento não
    # podem ir junto. Iam: `ws` era reatribuído para o worktree logo abaixo e o
    # `root` do Kernel saía dali — o que significava (a) histórico de run
    # destruído junto com o worktree descartável e, pior, (b) o kill-switch que
    # o usuário liga no repo INVISÍVEL para o run isolado, que é exatamente o
    # run que mais precisa poder ser parado.
    _root_gov = str(ws / "memory" / "runtime")
    from ..core.events.bus import EventBus
    from ..core.runtime.state_store import JsonlStateStore
    _bus = EventBus(store=JsonlStateStore(_root_gov))

    _run_slug = f"run-{_uuid4().hex[:10]}"
    _contrato = contrato_do_workspace(ws)
    _iso = _preparar_ambiente(ws, _contrato, run_id=_run_slug, bus=_bus)
    if _iso.aviso:
        console.print(f"[yellow]⚠ {_iso.aviso}[/yellow]")
    if _iso.isolado:
        console.print(f"[dim]worktree:[/dim] {_iso.worktree.branch} "
                      f"[dim]({_iso.workspace})[/dim]")
        ws = _iso.workspace   # tudo abaixo — router, contexto, gates — usa o worktree

    model_name = (model or cfg.model.name).strip()
    provider_efetivo = cfg.model.provider
    client = _build_client(cfg)

    # Roteamento por tier — `bauer run` ignorava `model.profiles` e usava sempre
    # `model.name`, então quem configurou coding/heavy via os tiers valerem no
    # `bauer agent` e no serve, mas NÃO no caminho mais autônomo.
    #
    # Classifica UMA vez, pela tarefa, e fixa o modelo do tier para o run
    # inteiro: aqui o objetivo não muda entre rodadas, e trocar de modelo no
    # meio de um laço sem supervisão só tornaria o resultado irreprodutível.
    # `--model` explícito continua vencendo — flag do usuário acima de heurística.
    if not model:
        _perfis, _cliente_do_provider = heuristic_route_kit(cfg)
        if _perfis:
            from ..model_router import classify_task
            _rota = classify_task(task)
            _perfil = _perfis.get(_rota.profile)
            _alvo = getattr(_perfil, "model", "") if _perfil else ""
            if _alvo:
                _prov = getattr(_perfil, "provider", "") or cfg.model.provider
                _c = client if _prov == cfg.model.provider else _cliente_do_provider(_prov)
                if _c is not None:
                    client, model_name, provider_efetivo = _c, _alvo, _prov
                    console.print(
                        f"[dim]Tier [bold]{_rota.profile}[/bold] → {_alvo} ({_prov})"
                        f" — {_rota.reason}[/dim]"
                    )
                else:
                    console.print(
                        f"[yellow]Tier '{_rota.profile}' pede provider "
                        f"'{_prov}', que não subiu — seguindo com {model_name}.[/yellow]"
                    )

    router = _build_router(cfg, ws, llm_client=client)

    from .agent_cmd import _build_fallback_clients
    fallback_clients = _build_fallback_clients(cfg, console=console) or None

    # Contexto do turno (mesmo padrão do serve): system prompt do router.
    from ..agent import _build_system_prompt, run_one_turn_with_fallback
    applied_context = int(getattr(cfg.model, "requested_context", 0) or 8192)
    # O contexto aplicado precisa CHEGAR ao Ollama: sem `options.num_ctx` na
    # requisição ele usa o próprio default (bem menor) e TRUNCA o prompt em
    # silêncio — o `bauer run` calculava 32768, exibia 32768 e mandava nada.
    # Sintoma: prompt grande volta com resposta vazia. Só `bauer chat` e
    # `bauer agent` faziam essa atribuição; serve e run ficavam de fora.
    _apply_ollama_runtime(client, cfg, applied_context)
    from ..core.context import ContextBuilder

    ctx, _ = (ContextBuilder(applied_context=applied_context, bus=_bus,
                             run_id=_run_slug)
              .instrucao("seguranca", _build_system_prompt(router, client=client))
              .montar())

    # Kernel: governa quando ligado no config (mesma admissão da web). Flag
    # desligada = None; ligada e com wiring quebrado = KernelWiringError — o
    # loop autônomo é o ÚLTIMO lugar onde rodar ingovernado em silêncio serve.
    from ..core.kernel import build_kernel, require_kernel
    # root EXPLÍCITO, atado ao workspace. O default de build_kernel é
    # "memory/runtime" RELATIVO ao cwd do processo — que por acaso coincide com
    # o workspace no uso normal, mas é acidente, não contrato: basta o processo
    # ter cwd diferente da pasta alvo para os runs irem parar noutro lugar. Foi
    # o que quebrou 4 testes ao ligar o Kernel por default: todos compartilhavam
    # o store do repo, acumulavam runs não-terminais e batiam em
    # "max parallel runs reached: 3/3".
    # `root` é o do REPO (`_root_gov`) e `workspace` é onde o trabalho acontece
    # — iguais sem isolamento, diferentes com worktree. É a separação que faz o
    # kill-switch continuar valendo e os gates rodarem no lugar certo.
    kernel = require_kernel(
        cfg,
        lambda: build_kernel(cfg, root=_root_gov, workspace=str(ws), bus=_bus),
        label="bauer run",
    )

    # Aprovação headless (o /loop nunca para pra perguntar; o modo controla o
    # que é auto-aprovado vs. auto-negado).
    from ..headless_approval import HeadlessApprovalConfig, HeadlessApprovalEngine
    engine = HeadlessApprovalEngine(HeadlessApprovalConfig(
        mode=limits.approval_mode,
        risk_threshold=float(getattr(cfg.loop, "approval_risk_threshold", 0.4)),
    ))

    _banner(ws, model_name, provider_efetivo, limits, kernel is not None)

    from ..autonomous_budget import AutonomousBudget
    from ..serve_loop import run_loop_rounds
    budget = AutonomousBudget(
        max_cost_usd=limits.max_cost_usd,
        max_wall_seconds=limits.max_minutes * 60,
        max_tool_calls=limits.max_tool_calls,
    )

    kill_control = None
    if kernel is not None:
        from ..core.runtime.resilience import RuntimeControl
        kill_control = RuntimeControl(store=kernel.runs.store)

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

    # Sinais de estagnação ENTRE rodadas (§13): reversão de alterações e janela
    # crescendo sobre um disco que não muda. Os guardrails de chamada não pegam
    # nenhum dos dois — cada chamada é legítima; o que está errado é a sequência.
    from ..progress_signals import SinaisDeProgresso
    _sinais = SinaisDeProgresso(ws, ctx=ctx, bus=_bus)

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
        for _aviso in _sinais.rodada(n, text, tl):
            # Avisa, não interrompe: quem para o laço é o orçamento. Um detector
            # heurístico com poder de matar a tarefa erraria contra refatoração
            # grande, que passa por estados intermediários iguais a estes.
            console.print(f"[yellow]⚠ {_aviso.message}[/yellow]")
            ctx.add_user(_aviso.message)
        _print_round(n, budget, tl)

    router._approval_callback = engine.make_approval_callback()
    from ..core.kernel.entry import CANCELLED, run_governed
    # None = o executor ainda não rodou. Distingue "a governança barrou antes de
    # começar" de "o laço rodou e terminou assim" — com execute(), um deny de
    # policy devolve run terminal SEM chamar o executor.
    stop_reason: str | None = None
    rounds = 0

    def _rodar_loop(payload: dict | None = None) -> dict:
        """O laço de rodadas como EXECUTOR do Kernel.

        Com custódia (``kernel.execute``), quem decide ``completed`` é o Kernel,
        depois dos gates — e não este código. Antes o ``bauer run`` admitia via
        ``kernel.admit()`` e fechava o run com ``runs.complete_run()``: o caller
        declarando sucesso, exatamente o que o harness precisa impedir. Medido:
        o Evaluator NUNCA rodava no caminho mais autônomo do Bauer.

        ``replan_feedback`` no payload é o motivo do gate reprovado — vira nudge
        para o laço corrigir o rumo na próxima passada, dentro do orçamento que
        sobrou (o ``budget`` é o mesmo objeto, então replan não ganha crédito
        novo: se acabou, a passada seguinte encerra no topo).
        """
        nonlocal stop_reason, rounds
        feedback = (payload or {}).get("replan_feedback")
        if feedback:
            ctx.add_user(f"A validação reprovou o resultado anterior: {feedback}\n"
                         f"Corrija isso e conclua.")
        stop_reason, rounds, last_text, _tool_log = run_loop_rounds(
            goal=task, ctx=ctx, turn_fn=_turn_fn, budget=budget,
            should_stop=_should_stop, on_round=_on_round,
        )
        snap = budget.snapshot()
        out: dict = {"output": last_text, "tool_calls_count": snap.tool_calls,
                     "cost_estimate": round(snap.cost_usd, 6)}
        if stop_reason == "kill_switch":
            # cancelamento, não falha — o Kernel trata terminal sem retry/replan
            return {**out, "status": CANCELLED, "error": "runtime kill switch ativo"}
        if stop_reason != "completed":
            return {**out, "status": "failed", "error": f"bauer run parou: {stop_reason}"}
        return out

    try:
        gov = run_governed(kernel, _rodar_loop, agent_id="cli.run", task=task,
                           input={"endpoint": "bauer run", "workspace": str(ws)},
                           # Laço sem supervisão: ninguém entre os turnos.
                           # Ver KernelRequest.autonomous.
                           autonomous=True)
        if gov.blocked_before_start:
            motivo = gov.error or gov.policy_reason or gov.status
            console.print(f"[red]⛔ Bloqueado antes de iniciar:[/red] {motivo}")
            stop_reason = "bloqueado"
        elif gov.status == CANCELLED and stop_reason == "completed":
            stop_reason = "interrupted"
        elif not gov.ok and gov.governed and stop_reason == "completed":
            # o veredito do Kernel VENCE o do laço: gate reprovado derruba um
            # "completed" que o laço achava que tinha conquistado
            stop_reason = "validacao_reprovou"
            console.print(f"[yellow]⚠ Validação reprovou:[/yellow] {gov.error or ''}")
    finally:
        cost_sink.reset(_cost_token)
        router._approval_callback = None
        if _iso.isolado:
            from ..core.workspace import finalizar as _finalizar_ambiente
            _artefato = _finalizar_ambiente(
                _iso, objetivo=task[:120], sucesso=(stop_reason == "completed"),
                bus=_bus, run_id=_run_slug)
            if _artefato:
                console.print(f"[dim]artefato:[/dim] {_artefato}")

    _summary(stop_reason or "completed", rounds, budget)

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
        # o laço achou que terminou; o gate do Kernel discordou
        "validacao_reprovou": "[red]✗ Validação reprovou o resultado[/red]",
        # governança barrou no preflight — o executor nunca rodou
        "bloqueado": "[red]⛔ Bloqueado pela governança[/red]",
    }
    snap = budget.snapshot()
    console.print(
        f"\n{labels.get(stop_reason, stop_reason)} "
        f"[dim]· {rounds} rodadas · {snap.tool_calls} tools · "
        f"~US$ {snap.cost_usd:.3f} estimado[/dim]"
    )


# _finalize_run REMOVIDO: era o caller decidindo o desfecho do run
# (complete_run/fail_run/cancelled à mão) porque o `bauer run` entrava por
# kernel.admit(), sem custódia. Agora entra por kernel.execute() com o laço de
# rodadas como executor — quem fecha o run é o Kernel, depois dos gates.
# O executor só REPORTA o desfecho (status cancelled/failed no dict de retorno).
