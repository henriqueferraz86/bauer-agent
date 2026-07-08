"""Comando bauer orchestrate."""

from __future__ import annotations

from ..orchestrator import AgentOrchestrator
from ..orchestrator import MAX_STEPS
from ..model_router import ModelRouter
from ..ollama_client import OllamaClient
from ..orchestrator import OrchestratorConfig
from pathlib import Path
from ..model_router import Route
from ..model_router import RouterConfig
from rich.rule import Rule
from rich.table import Table
from ..workspace_manager import WorkspaceManager
import sys
import typer

from ._common import _RUNTIME_STATE_DEFAULT, _SPECS_DIR, _WORKSPACE_DIR, console
from ._runtime import _build_client, _build_router, _get_or_run_state, _load_or_die

orchestrate_app = typer.Typer(help="Orquestrador de agents — tarefas complexas em varios passos")


def _build_orchestrator_runtime(
    *,
    config: Path,
    models: Path,
    workspace: Path,
    state_file: Path,
    agents_file: Path,
    planner: str = "",
    synthesizer: str = "",
) -> AgentOrchestrator:
    cfg, reg = _load_or_die(config, models)
    _get_or_run_state(cfg, reg, state_file)
    client = _build_client(cfg)

    ollama_client = OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    alive, alive_msg = ollama_client.is_alive()
    if not alive:
        if cfg.model.provider == "ollama":
            console.print(
                f"[red]Ollama em {cfg.ollama.host} nao esta respondendo: {alive_msg}[/red]\n"
                f"Verifique se o servidor Ollama esta rodando."
            )
            raise typer.Exit(code=1)
        ollama_client = client

    workspace.mkdir(parents=True, exist_ok=True)
    tool_router = _build_router(cfg, workspace)
    is_ollama = cfg.model.provider == "ollama"
    main_model = cfg.model.name
    code_model = cfg.router.code_model if is_ollama else main_model
    reasoning_model = cfg.router.reasoning_model if is_ollama else main_model
    direct_model = cfg.router.direct_model if is_ollama else main_model
    router_cfg = RouterConfig(
        router_model=cfg.router.router_model,
        default_model=cfg.model.name,
        routes=[
            Route("code", "codigo", code_model),
            Route("reasoning", "raciocinio", reasoning_model),
            Route("tool", "ferramenta", code_model),
            Route("direct", "direto", direct_model),
        ],
    )
    model_router = ModelRouter(ollama_client, router_cfg)
    orch_cfg = OrchestratorConfig(
        planner_model=planner or (cfg.router.router_model if is_ollama else cfg.model.name),
        synthesizer_model=synthesizer or (cfg.router.reasoning_model if is_ollama else cfg.model.name),
        max_steps=MAX_STEPS,
        parallel_steps=cfg.runtime.profile == "high",
        agents_file=str(agents_file),
    )
    return AgentOrchestrator(
        client,
        tool_router,
        model_router,
        orch_cfg,
        planner_client=ollama_client,
        console=console,
    )


@orchestrate_app.command("run")
def orchestrate_run(
    task: str = typer.Argument("", help="Descricao da tarefa — omita para modo entrevista"),
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(
        _RUNTIME_STATE_DEFAULT,
        "--state-file",
        help="Runtime state (gerado pelo doctor)",
    ),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    agent_name: str = typer.Option("", "--agent", "-a", help="Agent especializado a usar (ex: python-expert)"),
    planner: str = typer.Option("", "--planner", help="Modelo planejador (padrao: qwen3:0.6b)"),
    synthesizer: str = typer.Option("", "--synthesizer", help="Modelo sintetizador (padrao: phi4-mini)"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Modo passo-a-passo (confirma cada onda antes de executar)"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Retoma execucao anterior interrompida"),
    mode: str = typer.Option("sync", "--mode", help="sync | hybrid | durable"),
    node_runtime: str = typer.Option("auto", "--node-runtime", help="auto | inline | dispatcher"),
    background: bool = typer.Option(False, "--background", help="Submete nodes ao dispatcher e retorna sem bloquear"),
    run_id: str = typer.Option("", "--run-id", help="ID de orchestration_run para retomar/forcar"),
):
    """Executa tarefa complexa com orquestrador multi-passo (DAG + paralelo).

    Planeja, executa passos independentes em paralelo e sintetiza o resultado.
    Progresso e salvo automaticamente — use --resume para retomar se interrompido.

    Exemplos:
      bauer orchestrate run "crie um script que leia dados.csv e calcule estatisticas"
      bauer orchestrate run "pesquise sobre IA, salve o resumo em um arquivo" --interactive
      bauer orchestrate run "tarefa longa" --resume
    """
    cfg, reg = _load_or_die(config, models)

    # Modo entrevista: sem tarefa → wizard interativo
    if not task:
        from ..agent_wizard import wizard_orchestrate
        result = wizard_orchestrate()
        if result is None:
            raise typer.Exit(code=0)
        task = result["task"]
        if result["agent"] and not agent_name:
            agent_name = result["agent"]
        if result["interactive"]:
            interactive = True
        if result["resume"]:
            resume = True

    # Auto-seleção de agent por capability matching (se não especificado)
    if not agent_name:
        try:
            from ..agent_registry import AgentRegistry as _AR
            _reg = _AR(agents_file)
            _matched = _reg.auto_select(task)
            if _matched:
                console.print(
                    f"[dim]Auto-selecionado: agent [cyan]{_matched.name}[/cyan] "
                    f"— {_matched.description[:60]}[/dim]"
                )
                agent_name = _matched.name
        except Exception:
            pass

    # Aplica system prompt do agent especializado (se especificado)
    _agent_system_patch = None
    if agent_name:
        from ..agent_registry import AgentRegistry
        from ..agent import _build_system_prompt as _bsp
        reg_agents = AgentRegistry(agents_file)
        ag = reg_agents.get(agent_name)
        if ag:
            console.print(f"[dim]Agent: [cyan]{ag.name}[/cyan] — {ag.description}[/dim]")
            _orig_bsp = _bsp

            def _patched_bsp(r):
                base = _orig_bsp(r) + f"\n\n# ESPECIALIZACAO\n{ag.system}"
                _mem = workspace / "agents" / ag.name / "MEMORY.md"
                if _mem.exists():
                    _mc = _mem.read_text(encoding="utf-8").strip()
                    if _mc:
                        base += (
                            f"\n\n# MEMÓRIA DO AGENTE\n"
                            f"Conteúdo carregado de `agents/{ag.name}/MEMORY.md`:\n\n{_mc}"
                        )
                return base

            import bauer.agent as _agent_mod
            _agent_mod._build_system_prompt = _patched_bsp  # type: ignore[attr-defined]
            _agent_system_patch = (_agent_mod, _orig_bsp)
        else:
            console.print(f"[yellow]Agent '[cyan]{agent_name}[/cyan]' nao encontrado.[/yellow]")
            if typer.confirm(f"Criar o agent '{agent_name}' agora?", default=True):
                from ..agent_wizard import wizard_create_agent
                _cfg_tmp, _ = _load_or_die(config, models)
                _created_ag = wizard_create_agent(
                    reg_agents,
                    config_model=_cfg_tmp.model.name,
                    config_provider=_cfg_tmp.model.provider,
                )
                if _created_ag:
                    ag = _created_ag
                    _orig_bsp2 = _bsp

                    def _patched_bsp2(r):
                        base2 = _orig_bsp2(r) + f"\n\n# ESPECIALIZACAO\n{ag.system}"  # type: ignore[union-attr]
                        _mem2 = workspace / "agents" / ag.name / "MEMORY.md"  # type: ignore[union-attr]
                        if _mem2.exists():
                            _mc2 = _mem2.read_text(encoding="utf-8").strip()
                            if _mc2:
                                base2 += (
                                    f"\n\n# MEMÓRIA DO AGENTE\n"
                                    f"Conteúdo carregado de `agents/{ag.name}/MEMORY.md`:\n\n{_mc2}"  # type: ignore[union-attr]
                                )
                        return base2

                    import bauer.agent as _agent_mod2
                    _agent_mod2._build_system_prompt = _patched_bsp2  # type: ignore[attr-defined]
                    _agent_system_patch = (_agent_mod2, _orig_bsp2)
                    console.print(f"[green]✓[/green] Agent [cyan]{ag.name}[/cyan] criado e aplicado.")
                else:
                    console.print("[dim]Agent nao criado — usando agent padrao.[/dim]")
            else:
                console.print("[dim]Usando agent padrao.[/dim]")

    state = _get_or_run_state(cfg, reg, state_file)

    # Cliente principal (pode ser Ollama, OpenAI, etc.)
    client = _build_client(cfg)

    # Cliente Ollama separado para roteamento/planejamento (sempre local quando disponivel)
    # Se Ollama nao estiver rodando E o provider principal nao for Ollama,
    # usamos o proprio client principal como planejador (fallback gracioso).
    from bauer.ollama_client import OllamaClient as _OllamaClient
    ollama_client = _OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    alive, _alive_msg = ollama_client.is_alive()
    _ollama_available = alive

    if not _ollama_available:
        if cfg.model.provider == "ollama":
            # Provider e Ollama mas servidor down → erro fatal
            console.print(
                f"[red]Ollama em {cfg.ollama.host} nao esta respondendo: {_alive_msg}[/red]\n"
                f"Verifique se o servidor Ollama esta rodando."
            )
            raise typer.Exit(code=1)
        # Fallback: usa o client principal (cloud) tambem para planejamento/roteamento
        console.print(
            f"[dim]Ollama indisponivel em {cfg.ollama.host} — "
            f"usando [cyan]{cfg.model.provider}[/cyan] para planejar e sintetizar.[/dim]"
        )
        ollama_client = client  # sinaliza para o resto do fluxo usar o client principal

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    tool_router = _build_router(cfg, workspace)

    # ModelRouter usa o Ollama client (modelos locais de roteamento)
    # Para providers cloud, todas as rotas de execucao usam o modelo cloud
    _orch_is_ollama = cfg.model.provider == "ollama"
    _orch_main_model = cfg.model.name
    _orch_code   = cfg.router.code_model      if _orch_is_ollama else _orch_main_model
    _orch_reason = cfg.router.reasoning_model if _orch_is_ollama else _orch_main_model
    _orch_direct = cfg.router.direct_model    if _orch_is_ollama else _orch_main_model

    router_cfg = RouterConfig(
        router_model=cfg.router.router_model,
        default_model=cfg.model.name,
        routes=[
            Route("code",      "codigo",     _orch_code),
            Route("reasoning", "raciocinio", _orch_reason),
            Route("tool",      "ferramenta", _orch_code),
            Route("direct",    "direto",     _orch_direct),
        ],
    )
    model_router = ModelRouter(ollama_client, router_cfg)

    # Paralelo apenas no perfil high (GPU/alta RAM)
    _parallel = cfg.runtime.profile == "high"

    # Quando Ollama nao esta disponivel, usamos o modelo do client principal
    # para planejamento e sintese tambem — modelos Ollama (qwen3, phi4-mini) nao existem.
    if _ollama_available:
        _planner = planner or cfg.router.router_model
        _synthesizer = synthesizer or cfg.router.reasoning_model
    else:
        _planner = planner or cfg.model.name
        _synthesizer = synthesizer or cfg.model.name

    orch_cfg = OrchestratorConfig(
        planner_model=_planner,
        synthesizer_model=_synthesizer,
        max_steps=MAX_STEPS,
        parallel_steps=_parallel,
        agents_file=str(agents_file),
    )
    orch = AgentOrchestrator(
        client, tool_router, model_router, orch_cfg,
        planner_client=ollama_client,
        console=console,
    )

    # Carrega lista de agents para o planejador
    from ..agent_registry import AgentRegistry as _AgentRegistry
    _agents_list = _AgentRegistry(agents_file).list_agents()

    # Carrega specs aprovados/implementados para o planejador respeitar contratos
    from ..spec_manager import SpecManager as _SpecManager
    _specs_list = _SpecManager(_SPECS_DIR).list_specs()
    _active_specs = [s for s in _specs_list if s.status in ("approved", "implemented")]
    if _active_specs:
        console.print(f"[dim]Specs ativos: {', '.join(s.id for s in _active_specs)}[/dim]")

    # --- Execucao com tratamento de erros ---
    try:

        # --- Planejamento (ou carrega plano salvo) ---
        console.print(Rule("[bold]Orquestrador[/bold]"))
        console.print(f"[dim]Tarefa:[/dim] {task}\n")

        _mode = (mode or "sync").strip().lower()
        if _mode not in {"sync", "hybrid", "durable"}:
            console.print("[red]--mode invalido. Use sync, hybrid ou durable.[/red]")
            raise typer.Exit(code=2)
        _node_runtime = (node_runtime or "auto").strip().lower()
        if _node_runtime not in {"auto", "inline", "dispatcher"}:
            console.print("[red]--node-runtime invalido. Use auto, inline ou dispatcher.[/red]")
            raise typer.Exit(code=2)
        if background and not (_mode == "durable" or _node_runtime == "dispatcher"):
            console.print("[red]--background requer --mode durable ou --node-runtime dispatcher.[/red]")
            raise typer.Exit(code=2)

        if _mode in {"hybrid", "durable"}:
            from ..execution_engine import DurableDAGExecutionEngine

            console.print(
                f"[yellow]ExecutionEngine duravel ativo:[/yellow] "
                f"mode={_mode} node_runtime={_node_runtime}"
            )
            engine = DurableDAGExecutionEngine(
                orch,
                workspace=workspace,
                mode=_mode,
                node_runtime=_node_runtime,
            )
            if background:
                result = engine.submit(
                    task,
                    resume=resume or bool(run_id),
                    run_id=run_id,
                    agents=_agents_list or None,
                    specs=_active_specs or None,
                )
            else:
                result = engine.run(
                    task,
                    resume=resume or bool(run_id),
                    run_id=run_id,
                    agents=_agents_list or None,
                    specs=_active_specs or None,
                )
            console.print(
                f"[dim]orchestration_run:[/dim] [cyan]{result.run_id}[/cyan] "
                f"status={result.status} runtime={result.node_runtime} steps={len(result.results)}"
            )
            if background:
                console.print(
                    "[green]Run submetido ao dispatcher.[/green] "
                    "Use [bold]bauer dispatch daemon[/bold] para processar em background."
                )
                if _agent_system_patch:
                    _mod, _orig = _agent_system_patch
                    _mod._build_system_prompt = _orig  # type: ignore[attr-defined]
                return
            console.print(Rule("[bold]Resultado Final[/bold]"))
            sys.stdout.write("\033[32morchestrate>\033[0m ")
            sys.stdout.write(result.final)
            sys.stdout.write("\n\n")
            sys.stdout.flush()
            if _agent_system_patch:
                _mod, _orig = _agent_system_patch
                _mod._build_system_prompt = _orig  # type: ignore[attr-defined]
            return

        if resume and orch.has_saved_progress(task):
            saved_steps = orch.load_plan(task)
            if saved_steps:
                steps = saved_steps
                console.print("[yellow]Retomando execucao anterior...[/yellow]")
            else:
                steps = None
        else:
            steps = None

        if not steps:
            if _agents_list:
                console.print(f"[dim]Agents disponiveis para o planejador: {', '.join(a.name for a in _agents_list)}[/dim]")
            console.print("[yellow]Planejando passos...[/yellow]")
            steps = orch.plan(task, agents=_agents_list or None, specs=_active_specs or None)
            orch.save_plan(task, steps)

        if not steps:
            console.print("[red]Nao foi possivel decompor a tarefa em passos.[/red]")
            raise typer.Exit(code=1)

        # Exibe plano com indicacao de dependencias e agent designado
        batches = orch._topological_batches(steps)
        _mode_label = "paralelo" if orch_cfg.parallel_steps else "sequencial"
        console.print(f"\n[bold]Plano ({len(steps)} passos, {len(batches)} onda(s)) [{_mode_label}]:[/bold]")
        for wave_idx, batch in enumerate(batches):
            can_parallel = len(batch) > 1 and orch_cfg.parallel_steps
            wave_label = f"  Onda {wave_idx + 1}" + (" [paralelo]" if can_parallel else "")
            console.print(f"[dim]{wave_label}:[/dim]")
            for s in batch:
                tools_tag = "[cyan][tools][/cyan]" if s.get("tools") else ""
                deps = s.get("depends_on", [])
                deps_tag = f"[dim](dep: {deps})[/dim]" if deps else ""
                agent_tag = f"[magenta][{s['agent']}][/magenta]" if s.get("agent") else ""
                console.print(f"    {s['id']}. {s['goal']} {tools_tag}{agent_tag} {deps_tag}")

        # Confirmacao do plano (sempre — nao apenas em modo interativo)
        if not typer.confirm("\nExecutar plano?", default=True):
            console.print("[dim]Cancelado.[/dim]")
            orch.clear_progress(task)
            if _agent_system_patch:
                _mod, _orig = _agent_system_patch
                _mod._build_system_prompt = _orig  # type: ignore[attr-defined]
            return

        # Registra passos como tarefas no TASKS.md (se workspace inicializado)
        _task_ids: dict[int, str] = {}
        try:
            _wm_orch = WorkspaceManager(workspace)
            if _wm_orch.tasks_file.exists():
                _spec_mgr_orch = _SpecManager(_SPECS_DIR) if _active_specs else None
                for s in steps:
                    _step_spec_id = ""
                    if _spec_mgr_orch:
                        _relevant = _spec_mgr_orch.find_relevant(s["goal"], max_results=1)
                        if _relevant:
                            _step_spec_id = _relevant[0].id
                    _t = _wm_orch.add_task(
                        f"[Orch] {s['goal']}",
                        description=f"Passo {s['id']} do plano: {task}",
                        spec_id=_step_spec_id,
                    )
                    _task_ids[s["id"]] = _t.id
                    _wm_orch.update_task_status(_t.id, "IN_PROGRESS")
                if _task_ids:
                    console.print(f"[dim]{len(_task_ids)} tarefa(s) registradas em TASKS.md[/dim]")
        except Exception:
            pass  # workspace nao inicializado — silenciosamente ignora

        # --- Execucao em ondas ---
        done: dict = {r.id: r for r in (orch.load_progress(task) if resume else [])}
        all_results: list = list(done.values())

        for wave_idx, batch in enumerate(batches):
            pending = [s for s in batch if s["id"] not in done]

            # Passos ja concluidos nesta onda (retomados do cache)
            cached = [s for s in batch if s["id"] in done]
            if cached:
                for s in cached:
                    console.print(f"  [dim]Passo {s['id']} (retomado do cache)[/dim]")

            if not pending:
                continue

            # Cabecalho da onda
            if len(pending) > 1 and orch_cfg.parallel_steps:
                ids = ", ".join(str(s["id"]) for s in pending)
                console.print(f"\n[bold]Onda {wave_idx + 1} — Passos {ids} (paralelo):[/bold]")
                for s in pending:
                    tools_tag = "[cyan][tools][/cyan]" if s.get("tools") else ""
                    agent_tag = f" [magenta][{s['agent']}][/magenta]" if s.get("agent") else ""
                    console.print(f"  {s['id']}. {s['goal']} {tools_tag}{agent_tag}")
            else:
                s = pending[0]
                tools_tag = "[cyan][tools][/cyan]" if s.get("tools") else ""
                agent_tag = f" [magenta][{s['agent']}][/magenta]" if s.get("agent") else ""
                console.print(f"\n[bold]Passo {s['id']}:[/bold] {s['goal']} {tools_tag}{agent_tag}")

            if interactive:
                if not typer.confirm(f"Executar onda {wave_idx + 1}?", default=True):
                    console.print("[dim]Interrompido pelo usuario. Use --resume para continuar.[/dim]")
                    if _task_ids:
                        try:
                            for s in pending:
                                if s["id"] in _task_ids:
                                    _wm_orch.update_task_status(_task_ids[s["id"]], "BLOCKED")
                        except Exception:
                            pass
                    break

            batch_results = orch.execute_parallel_steps(pending, all_results)
            all_results.extend(batch_results)
            orch.save_progress(task, batch_results)
            for r in batch_results:
                done[r.id] = r

            # Atualiza status de cada passo concluido no TASKS.md
            if _task_ids:
                try:
                    for r in batch_results:
                        if r.id in _task_ids:
                            new_status = "BLOCKED" if r.model_used == "(erro)" else "DONE"
                            _wm_orch.update_task_status(_task_ids[r.id], new_status)
                except Exception:
                    pass

            # Exibe resultado de cada passo da onda
            for r in batch_results:
                step_used_tools = any(s["id"] == r.id and s.get("tools") for s in pending)
                if len(pending) > 1:
                    console.print(f"  [bold]Passo {r.id}[/bold] [dim](modelo: {r.model_used})[/dim]")
                else:
                    console.print(f"  [dim]Modelo: {r.model_used}[/dim]")
                if r.tool_log:
                    for tl in r.tool_log:
                        console.print(f"  [dim]  -> {tl['tool']}[/dim]")
                if step_used_tools or not r.tool_log and r.model_used != "(erro)":
                    if step_used_tools:
                        preview = r.response[:400].replace("\n", " ")
                        suffix = "..." if len(r.response) > 400 else ""
                        console.print(f"  [green]{preview}{suffix}[/green]")
                if r.model_used == "(erro)":
                    console.print(f"  [red]{r.response}[/red]")

        # --- Sintese ---
        console.print("\n[yellow]Sintetizando resultados...[/yellow]")
        goal_text = steps[0].get("goal", task)
        final = orch.synthesize(goal_text, all_results)
        orch.clear_progress(task)

        # Marca tarefas restantes como DONE no TASKS.md (ex: se interrompido antes)
        if _task_ids:
            try:
                for step_id, tid in _task_ids.items():
                    if step_id in done:
                        r = done[step_id]
                        if r.model_used != "(erro)":
                            _wm_orch.update_task_status(tid, "DONE")
            except Exception:
                pass

        console.print(Rule("[bold]Resultado Final[/bold]"))
        sys.stdout.write("\033[32morchestrate>\033[0m ")
        sys.stdout.write(final)
        sys.stdout.write("\n\n")
        sys.stdout.flush()

    except Exception as exc:
        from ..openai_client import OpenAIClientError as _OCE
        from ..ollama_client import OllamaError as _OE
        _err_type = "Ollama" if isinstance(exc, _OE) else "Provider" if isinstance(exc, _OCE) else "Erro"
        console.print(f"\n[red]{_err_type} no orquestrador:[/red] {exc}")
        console.print("[dim]Use --resume para retomar de onde parou.[/dim]")

    # Restaura system prompt original se foi patchado pelo agent
    if _agent_system_patch:
        _mod, _orig = _agent_system_patch
        _mod._build_system_prompt = _orig  # type: ignore[attr-defined]


@orchestrate_app.command("node-worker")
def orchestrate_node_worker(
    run_id: str = typer.Argument(..., help="ID do orchestration_run"),
    step_id: int = typer.Argument(..., help="ID do step/node dentro do plano"),
    task_id: str = typer.Option("", "--task-id", help="Task Kanban claimed pelo dispatcher"),
    claim_id: str = typer.Option("", "--claim-id", help="Claim id esperado"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    planner: str = typer.Option("", "--planner"),
    synthesizer: str = typer.Option("", "--synthesizer"),
):
    """Worker interno: executa um node persistido de uma orchestration_run."""
    from ..execution_engine import run_orchestration_node

    orch = _build_orchestrator_runtime(
        config=config,
        models=models,
        workspace=workspace,
        state_file=state_file,
        agents_file=agents_file,
        planner=planner,
        synthesizer=synthesizer,
    )
    try:
        result = run_orchestration_node(
            orch,
            workspace=workspace,
            run_id=run_id,
            step_id=step_id,
            task_id=task_id,
            claim_id=claim_id,
        )
    except Exception as exc:
        console.print(f"[red]Erro no node-worker:[/red] {exc}")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]orchestration_run:[/dim] [cyan]{result.run_id}[/cyan] "
        f"step={result.step_id} status={result.status} "
        f"orchestration={result.orchestration_status}"
    )
    if result.final:
        console.print(Rule("[bold]Resultado Final[/bold]"))
        sys.stdout.write("\033[32morchestrate>\033[0m ")
        sys.stdout.write(result.final)
        sys.stdout.write("\n\n")
    else:
        sys.stdout.write(result.step_result.response[:2000])
        sys.stdout.write("\n")
    sys.stdout.flush()
    if result.status == "failed":
        raise typer.Exit(code=1)


@orchestrate_app.command("advance")
def orchestrate_advance(
    run_id: str = typer.Argument(..., help="ID do orchestration_run"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Avanca uma orchestration_run duravel: enfileira proximos nodes ou sintetiza final."""
    from ..execution_engine import DurableDAGExecutionEngine
    from ..orchestration_store import OrchestrationStore

    store = OrchestrationStore(workspace)
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]orchestration_run nao encontrado:[/red] {run_id}")
        raise typer.Exit(code=1)
    orch = _build_orchestrator_runtime(
        config=config,
        models=models,
        workspace=workspace,
        state_file=state_file,
        agents_file=agents_file,
    )
    engine = DurableDAGExecutionEngine(
        orch,
        workspace=workspace,
        mode=run.mode or "durable",
        node_runtime=run.metadata.get("node_runtime") or "dispatcher",
    )
    result = engine.advance(run_id)
    console.print(
        f"[dim]orchestration_run:[/dim] [cyan]{result.run_id}[/cyan] "
        f"status={result.status} runtime={result.node_runtime} steps_done={len(result.results)}"
    )
    if result.final:
        console.print(Rule("[bold]Resultado Final[/bold]"))
        sys.stdout.write("\033[32morchestrate>\033[0m ")
        sys.stdout.write(result.final)
        sys.stdout.write("\n\n")
        sys.stdout.flush()


@orchestrate_app.command("resume")
def orchestrate_resume(
    run_id: str = typer.Argument(..., help="ID do orchestration_run"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Retoma uma execucao duravel pelo run_id."""
    from ..orchestration_store import OrchestrationStore

    store = OrchestrationStore(workspace)
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]orchestration_run nao encontrado:[/red] {run_id}")
        raise typer.Exit(code=1)
    orchestrate_run(
        task=run.objective,
        config=config,
        models=models,
        workspace=workspace,
        state_file=state_file,
        agents_file=agents_file,
        agent_name="",
        planner="",
        synthesizer="",
        interactive=False,
        resume=True,
        mode=run.mode or "hybrid",
        node_runtime="auto",
        background=False,
        run_id=run_id,
    )


@orchestrate_app.command("list")
def orchestrate_list(
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    durable: bool = typer.Option(True, "--durable/--legacy-only", help="Inclui runs duraveis"),
):
    """Lista tarefas do orquestrador com progresso salvo (prontas para --resume)."""
    from ..orchestrator import AgentOrchestrator, OrchestratorConfig
    from unittest.mock import MagicMock as _MM

    # Cria instância mínima só para usar list_saved_progress
    orch = AgentOrchestrator(_MM(), _MM(), _MM(), OrchestratorConfig())

    entries = orch.list_saved_progress()
    durable_runs = []
    if durable:
        try:
            from ..orchestration_store import OrchestrationStore

            durable_runs = OrchestrationStore(workspace).list_runs(limit=20)
        except Exception:
            durable_runs = []

    if not entries and not durable_runs:
        console.print("[dim]Nenhuma tarefa com progresso salvo em .orchestrate_progress/[/dim]")
        console.print("[dim]Nenhuma orchestration_run duravel encontrada.[/dim]")
        console.print("[dim]Tarefas aparecem aqui quando interrompidas antes de concluir.[/dim]")
        return

    if durable_runs:
        durable_table = Table(title=f"Runs duraveis ({len(durable_runs)})", show_lines=True)
        durable_table.add_column("Run", style="cyan")
        durable_table.add_column("Status")
        durable_table.add_column("Mode")
        durable_table.add_column("Steps", justify="right")
        durable_table.add_column("Objetivo", style="bold")
        for run in durable_runs:
            durable_table.add_row(
                run.run_id,
                run.status,
                run.mode,
                str(len(run.plan)),
                run.objective[:70],
            )
        console.print(durable_table)

    if not entries:
        console.print("\n[dim]Para retomar run duravel: [bold]bauer orchestrate resume <run_id>[/bold][/dim]")
        return

    table = Table(title=f"Progresso legado ({len(entries)})", show_lines=True)
    table.add_column("Tarefa", style="bold")
    table.add_column("Progresso", style="cyan")
    table.add_column("Criado", style="dim")
    table.add_column("Hash", style="dim", width=12)

    for e in entries:
        progress = f"{e['steps_done']}/{e['steps_total']} passos"
        table.add_row(
            e["task"][:70],
            progress,
            e["created"],
            e["hash"],
        )

    console.print(table)
    console.print("\n[dim]Para retomar: [bold]bauer orchestrate run \"<tarefa>\" --resume[/bold][/dim]")


@orchestrate_app.command("cancel")
def orchestrate_cancel(
    task: str = typer.Argument("", help="Texto da tarefa a cancelar (ou 'all' para todas)"),
    all_tasks: bool = typer.Option(False, "--all", "-a", help="Cancela todas as tarefas salvas"),
    force: bool = typer.Option(False, "--force", "-f", help="Sem confirmacao interativa"),
):
    """Cancela tarefa(s) do orquestrador removendo progresso salvo."""
    import shutil
    from ..orchestrator import AgentOrchestrator, OrchestratorConfig
    from unittest.mock import MagicMock as _MM

    orch = AgentOrchestrator(_MM(), _MM(), _MM(), OrchestratorConfig())

    if all_tasks:
        entries = orch.list_saved_progress()
        if not entries:
            console.print("[dim]Nenhuma tarefa salva para cancelar.[/dim]")
            return
        if not force:
            console.print(f"[yellow]Remover {len(entries)} tarefa(s) salva(s)?[/yellow]")
            if not typer.confirm("Confirmar?", default=False):
                console.print("[dim]Cancelamento abortado.[/dim]")
                return
        base = Path(".orchestrate_progress")
        if base.exists():
            shutil.rmtree(base)
        console.print(f"[green]{len(entries)} tarefa(s) cancelada(s).[/green]")
        return

    if not task:
        console.print("[red]Especifique a tarefa ou use --all.[/red]")
        raise typer.Exit(1)

    if orch.has_saved_progress(task):
        if not force:
            console.print(f"[yellow]Remover progresso de: '{task[:60]}'?[/yellow]")
            if not typer.confirm("Confirmar?", default=False):
                console.print("[dim]Cancelamento abortado.[/dim]")
                return
        orch.clear_progress(task)
        console.print(f"[green]Progresso de '{task[:60]}' removido.[/green]")
    else:
        console.print(f"[yellow]Nenhum progresso salvo para: '{task[:60]}'[/yellow]")


@orchestrate_app.command("dag")
def orchestrate_dag(
    session_id: str = typer.Argument("", help="Session ID do orquestrador (ou vazio para última)"),
    live: bool = typer.Option(False, "--live", "-l", help="Atualiza em tempo real (Rich Live)"),
    output_json: bool = typer.Option(False, "--json", help="Emite snapshot JSON"),
    interval: float = typer.Option(1.0, "--interval", "-i", help="Intervalo de refresh em segundos (--live)"),
):
    """Visualiza o DAG de tarefas de uma sessão do orquestrador."""
    from ..dag_renderer import DAGGraph, NodeStatus

    # Tenta carregar grafo do arquivo de progresso
    progress_dir = Path(".orchestrate_progress")
    if not progress_dir.exists():
        console.print("[yellow]Nenhuma sessão ativa encontrada.[/yellow]")
        raise typer.Exit(0)

    # Encontra o arquivo mais recente ou pelo session_id
    if session_id:
        cands = list(progress_dir.glob(f"*{session_id}*.json"))
    else:
        cands = sorted(progress_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not cands:
        console.print("[yellow]Nenhum progresso encontrado.[/yellow]")
        raise typer.Exit(0)

    import json as _json
    with open(cands[0]) as f:
        data = _json.load(f)

    # Reconstrói o grafo a partir do progresso salvo
    graph = DAGGraph(session_id=data.get("task", "")[:30])
    for step in data.get("steps", []):
        step_id = step.get("id", 0)
        graph.add_node(
            node_id=step_id,
            goal=step.get("goal", ""),
            depends_on=step.get("depends_on", []),
            priority=step.get("priority", 5),
        )
        if step.get("completed"):
            from ..dag_renderer import NodeStatus
            graph.update_status(step_id, NodeStatus.DONE, result_preview=str(step.get("response", ""))[:100])

    if output_json:
        console.print(graph.to_json())
        return

    if live:
        from rich.live import Live
        import time as _time
        with Live(graph.to_rich_tree(), refresh_per_second=4, console=console) as live_ctx:
            for _ in range(int(60 / interval)):
                _time.sleep(interval)
                # Re-lê arquivo para atualizar
                try:
                    with open(cands[0]) as f:
                        data = _json.load(f)
                    g2 = DAGGraph(session_id=graph.session_id)
                    for step in data.get("steps", []):
                        g2.add_node(step["id"], step.get("goal", ""), step.get("depends_on", []))
                        if step.get("completed"):
                            g2.update_status(step["id"], NodeStatus.DONE)
                    live_ctx.update(g2.to_rich_tree())
                except Exception:
                    pass
    else:
        from rich import print as rprint
        rprint(graph.to_rich_tree())


@orchestrate_app.command("priority")
def orchestrate_priority(
    task_id: int = typer.Argument(..., help="ID do passo/tarefa"),
    set_priority: int = typer.Option(-1, "--set", "-s", help="Nova prioridade (0-10)"),
    session_id: str = typer.Option("", "--session", help="Session ID específica"),
):
    """Ajusta a prioridade de um passo do orquestrador (0 = baixa, 10 = urgente)."""
    import json as _json

    if not (0 <= set_priority <= 10):
        console.print("[red]Prioridade deve estar entre 0 e 10.[/red]")
        raise typer.Exit(1)

    progress_dir = Path(".orchestrate_progress")
    if session_id:
        cands = list(progress_dir.glob(f"*{session_id}*.json"))
    else:
        cands = sorted(progress_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if progress_dir.exists() else []

    if not cands:
        console.print("[yellow]Nenhuma sessão encontrada.[/yellow]")
        raise typer.Exit(1)

    path = cands[0]
    with open(path) as f:
        data = _json.load(f)

    found = False
    for step in data.get("steps", []):
        if step.get("id") == task_id:
            old = step.get("priority", 5)
            step["priority"] = set_priority
            found = True
            console.print(f"[green]Passo #{task_id}: prioridade {old} → {set_priority}[/green]")
            break

    if not found:
        console.print(f"[red]Passo #{task_id} não encontrado.[/red]")
        raise typer.Exit(1)

    with open(path, "w") as f:
        _json.dump(data, f, indent=2)


@orchestrate_app.command("events")
def orchestrate_events(
    topic: str = typer.Option("", "--topic", "-t", help="Filtrar por tópico"),
    limit: int = typer.Option(20, "--limit", "-n", help="Número de eventos"),
):
    """Lista eventos recentes do EventBus."""
    from ..event_bus import EventBus
    from pathlib import Path

    db = Path.home() / ".bauer" / "event_bus.db"
    bus = EventBus(db_path=db, persist=db.exists())
    history = bus.history(topic=topic or None, limit=limit)

    if not history:
        console.print("[dim]Nenhum evento registrado.[/dim]")
        return

    from rich.table import Table
    import datetime

    tbl = Table(title=f"Eventos{f' [{topic}]' if topic else ''}", show_header=True)
    tbl.add_column("ID", style="dim", width=10)
    tbl.add_column("Tópico", style="cyan")
    tbl.add_column("Fonte", style="magenta")
    tbl.add_column("Hora", style="dim")
    tbl.add_column("Payload", style="white", max_width=40)

    for evt in history:
        ts = datetime.datetime.fromtimestamp(evt["ts"]).strftime("%H:%M:%S")
        payload_str = str(evt["payload"])[:40]
        tbl.add_row(evt["id"], evt["topic"], evt["source"], ts, payload_str)

    console.print(tbl)
