"""Comando bauer agent — loop principal do agente + run-one/serve embutido."""

from __future__ import annotations

from ..orchestrator import AgentOrchestrator
from ..config_loader import ConfigError
from rich.console import Console
from ..orchestrator import MAX_STEPS
from ..model_router import ModelRouter
from ..orchestrator import OrchestratorConfig
from rich.panel import Panel
from pathlib import Path
from ..model_router import Route
from ..model_router import RouterConfig
from ..config_loader import load_config
from ..ascii_intro import play_intro
from ..agent import run_agent_session
from ..logging_config import setup_logging
import sys
import typer

from ._common import _COMPANIES_DIR, _MEMORY_DIR, _RUNTIME_STATE_DEFAULT, _WORKSPACE_DIR, console
from ._runtime import _build_client, _build_router, _get_or_run_state, _load_or_die, _pick_model, _resolve_model_with_ram_check, _start_gateway_thread_cli

agent_app = typer.Typer(
    invoke_without_command=True,
    help="Agente interativo — use sem sub-comando para chat, ou: create/list/run/delete.",
)


def _build_fallback_clients(cfg, *, console=None) -> list:
    """Constrói clientes de fallback a partir de cfg.model.fallback_models.

    - Dedup: pula entradas iguais ao modelo principal ou repetidas (evita
      re-tentar o mesmo provider que acabou de falhar).
    - Observabilidade: falhas de construção vão para DEBUG (não somem em
      silêncio) e um resumo é exibido no console.

    Retorna lista de (client, model_name) pronta para `fallback_clients`.
    """
    import logging as _logging
    _log = _logging.getLogger("bauer.fallback")
    clients: list = []
    fb_models = getattr(cfg.model, "fallback_models", []) or []
    primary = (cfg.model.provider, cfg.model.name)
    seen: set = {primary}
    skipped = 0
    for fb in fb_models:
        prov = fb.provider if hasattr(fb, "provider") else (fb or {}).get("provider", "")
        name = fb.name if hasattr(fb, "name") else (fb or {}).get("name", "")
        if not prov or not name:
            continue
        key = (prov, name)
        if key in seen:
            _log.debug("fallback: pulando %s/%s (duplicado ou igual ao principal)", prov, name)
            skipped += 1
            continue
        seen.add(key)
        try:
            _fb_raw = cfg.model_dump()
            _fb_raw["model"]["provider"] = prov
            _fb_raw["model"]["name"] = name
            # fallback não se propaga recursivamente
            _fb_raw["model"]["fallback_models"] = []
            _fb_raw["model"]["fallback_providers"] = []
            from ..config_loader import BauerConfig as _BauerCfg
            _fb_cfg = _BauerCfg(**_fb_raw)
            from ..env_loader import apply_env_to_config as _aenv
            _aenv(_fb_cfg)
            _fb_client = _build_client(_fb_cfg)
            clients.append((_fb_client, name))
            _log.debug("fallback: pronto %s/%s", prov, name)
        except Exception as exc:  # noqa: BLE001 — fallback mal configurado é tolerável
            skipped += 1
            _log.debug("fallback: falhou ao montar %s/%s: %s", prov, name, exc)
    if console is not None and (clients or skipped):
        console.print(
            f"[dim]Fallback: {len(clients)} modelo(s) pronto(s)"
            + (f", {skipped} pulado(s)" if skipped else "")
            + ".[/dim]"
        )
    return clients


def _resolve_cwd_project(*, interactive: bool, cwd: "Path | None" = None) -> "Path | None":
    """Workspace derivado da pasta atual quando `bauer agent` roda de dentro dela.

    - cwd (ou ancestral) já registrado como projeto → usa direto (sem perguntar).
    - pasta nova/não-registrada → oferece adoção com UMA confirmação; ao aceitar,
      registra no projects.json (aparece na tela Projetos do desktop) e vira o
      workspace. Uma pasta VAZIA é adotada normalmente — é justamente o fluxo de
      "criei a pasta, entrei e quero começar a arquitetar o projeto".
    - pasta sensível (home, raiz de disco, ~/.bauer) ou não-interativo → None.

    Retorna o Path do workspace, ou None para manter o padrão (~/.bauer/workspace).
    """
    from .. import projects_registry as pr

    try:
        cur = (cwd or Path.cwd()).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return None

    if cur == Path(_WORKSPACE_DIR).resolve() or pr.is_sensitive_dir(cur):
        return None

    pid = pr.find_project_for_cwd(cur)
    if pid:
        entry = pr.get_project(pid) or {}
        proj_path = Path(entry.get("path", cur))
        console.print(
            f"[dim]📂 Projeto detectado: [cyan]{entry.get('name', proj_path.name)}"
            f"[/cyan] — {proj_path}[/dim]"
        )
        return proj_path

    if not interactive:
        return None

    console.print(f"[yellow]Esta pasta ainda não é um projeto Bauer:[/yellow] {cur}")
    try:
        adopt = typer.confirm(
            f"Adotar como workspace do projeto '{cur.name}'?", default=False
        )
    except Exception:  # noqa: BLE001 — sem TTY/EOF: não adota
        return None
    if not adopt:
        return None
    try:
        entry = pr.add_project(cur)
        console.print(
            f"[green]✓ Projeto '{entry['name']}' registrado.[/green] Workspace: {cur}"
        )
        return cur
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Não consegui registrar o projeto: {exc}[/red]")
        return None


@agent_app.callback(invoke_without_command=True)
def agent(
    ctx: typer.Context,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(
        _RUNTIME_STATE_DEFAULT,
        "--state-file",
        help="Runtime state (gerado pelo doctor)",
    ),
    model: str = typer.Option("", "--model", help="Sobrescreve o modelo do config"),
    pick: bool = typer.Option(False, "--pick", help="Mostra lista de modelos para escolher"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Retoma a ultima sessao salva"),
    session_id_opt: str = typer.Option("", "--session-id", help="ID de sessao especifica para retomar"),
    sessions_dir: Path = typer.Option(Path("memory/sessions"), "--sessions-dir", help="Diretorio de sessoes"),
    no_intro: bool = typer.Option(False, "--no-intro", help="Pula a animacao de entrada"),
    port: int = typer.Option(
        0, "--port", "-p",
        help="Sobe servidor HTTP embutido nesta porta (0 = desabilitado). "
             "Permite conexao do Claw3D/escritorio virtual sem bauer serve separado.",
    ),
    gateway_port: int = typer.Option(
        0, "--gateway-port", "-g",
        help="Porta do gateway WebSocket Claw3D (0 = desabilitado). "
             "Requer --port. Ex: --port 7770 --gateway-port 18789",
    ),
    api_key_opt: str = typer.Option("", "--api-key", help="API key para o servidor embutido"),
):
    """Agente interativo com Tool Bridge, roteamento inteligente e sessao persistente.

    Sem sub-comando: inicia o chat com o modelo atual.
    Sub-comandos: create, list, run, delete — gerenciam agents especializados.

    Use --resume para continuar a conversa de onde parou.
    Use --resume --session-id abc123 para retomar uma sessao especifica.
    Use --port 7770 para aceitar conexoes HTTP do Claw3D (floor local-runtime).
    Use --port 7770 --gateway-port 18789 para conexao WebSocket completa (todos os floors).
    """
    # Se um sub-comando foi chamado (create/list/run/delete), nao executa o chat
    if ctx.invoked_subcommand is not None:
        return

    # Animacao de entrada — apenas em sessoes novas (nao --resume)
    # Para bauer agent run <name>, a intro eh chamada dentro do agent_run
    if not resume:
        play_intro(console, skip=no_intro)

    cfg, reg = _load_or_die(config, models)
    setup_logging(cfg.logging.level, cfg.logging.file)

    is_ollama_provider = cfg.model.provider == "ollama"

    # Cria cliente Ollama local — necessario para roteamento e planejamento.
    # O probe de rede só roda quando o provider principal é ollama: com provider
    # cloud o ModelRouter é desabilitado de qualquer forma (ver bloco do router
    # abaixo), e esperar o timeout de um Ollama offline só atrasava o startup.
    from bauer.ollama_client import OllamaClient as _OllamaClient
    _ollama = _OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    _ollama_alive = False
    if is_ollama_provider:
        _ollama_alive, _ = _ollama.is_alive()

    state = _get_or_run_state(cfg, reg, state_file)

    # Ollama e obrigatorio apenas quando o provider principal e ollama.
    # Para OpenAI/OpenRouter, o agente funciona sem Ollama local
    # (apenas o roteamento fica desabilitado se Ollama estiver offline).
    if is_ollama_provider and not state.get("ollama_alive"):
        console.print(
            "[red]Ollama offline.[/red]\n"
            "Verifique se o Ollama esta rodando e rode [bold]bauer doctor[/bold]."
        )
        raise typer.Exit(code=1)

    if not workspace.exists():
        console.print(f"[yellow]Workspace '{workspace}' nao existe — criando.[/yellow]")
        workspace.mkdir(parents=True, exist_ok=True)

    _ensure_dispatcher_running(workspace, config, models)

    client = _build_client(cfg)
    applied_context = state["context"]["applied"]
    # Propaga num_ctx ao OllamaClient — sem isso o Ollama usa o default do modelo (geralmente 2048)
    if is_ollama_provider and hasattr(client, "num_ctx"):
        client.num_ctx = applied_context
    # Propaga think ao OllamaClient — usa valor de config.yaml (None → False no cliente)
    if is_ollama_provider and hasattr(client, "think"):
        client.think = cfg.model.think

    # Resolucao do modelo: --model > --pick > auto (com RAM check so para Ollama)
    import logging as _logging
    _mlog = _logging.getLogger("bauer.model_selection")
    _mlog.debug("[model-selection] config.model.name=%s", cfg.model.name)
    _mlog.debug("[model-selection] config.model.provider=%s", cfg.model.provider)
    _mlog.debug("[model-selection] router.enabled=%s", cfg.router.enabled)
    _mlog.debug("[model-selection] requested_context=%s  applied_context=%s",
               cfg.model.requested_context, applied_context)
    if is_ollama_provider:
        _mlog.debug("[model-selection] think=%s", cfg.model.think)

    if model:
        model_name = model
        _mlog.debug("[model-selection] source=--model flag  active=%s", model_name)
    elif pick:
        model_name = _pick_model(client, state["configured_model"])
        _mlog.debug("[model-selection] source=--pick  active=%s", model_name)
    else:
        if is_ollama_provider:
            model_name = _resolve_model_with_ram_check(
                state["configured_model"], reg, client,
                state["ram_available_mb"], cfg.runtime.safety_margin_mb, _MEMORY_DIR,
            )
        else:
            # Providers cloud: usa o modelo configurado diretamente (sem RAM check local)
            model_name = cfg.model.name
        _mlog.debug("[model-selection] source=config.yaml  active=%s", model_name)

    # Verifica modelo no Ollama apenas quando provider=ollama
    if is_ollama_provider:
        resolved = client.resolve_model_name(model_name)
        if resolved is None:
            console.print(
                f"[red]Modelo '{model_name}' nao encontrado no Ollama.[/red]\n"
                f"Rode: [bold]ollama pull {model_name}[/bold]\n"
                f"Ou veja os modelos instalados: [bold]ollama list[/bold]"
            )
            raise typer.Exit(code=1)
        if resolved != model_name:
            console.print(f"[dim]Modelo resolvido: '{model_name}' → '{resolved}'[/dim]")
            model_name = resolved

    # ── Workspace: projeto da pasta atual OU empresa ativa — ANTES do router ──
    from ..company_manager import CompanyManager as _CompanyManager
    _cm_main = _CompanyManager(_COMPANIES_DIR)
    _active_company_main = _cm_main.get_active()

    # `bauer agent` de dentro de uma pasta de projeto (sem --workspace explícito
    # e sem empresa ativa): usa/adota essa pasta como workspace, em vez de cair
    # sempre no ~/.bauer/workspace. Só quando o usuário NÃO passou --workspace.
    _default_ws = Path(_WORKSPACE_DIR)
    _ws_is_default = workspace == _default_ws or workspace == _default_ws.resolve()
    if _ws_is_default and not _active_company_main:
        _detected = _resolve_cwd_project(interactive=sys.stdin.isatty())
        if _detected is not None:
            workspace = _detected

    if _active_company_main:
        _default_ws = Path(_WORKSPACE_DIR)
        if workspace == _default_ws or workspace == _default_ws.resolve():
            _cws = _cm_main.root / _active_company_main.id / "workspace"
            _cws.mkdir(parents=True, exist_ok=True)
            # Só redireciona para workspace isolado se ele tiver conteúdo real.
            # Se estiver vazio mas o workspace global tiver arquivos, usa o global
            # (setup legado: conteúdo ainda está em workspace/).
            _cws_has = any(
                f for f in _cws.rglob("*")
                if f.is_file() and f.name not in (".gitkeep", ".gitignore")
            )
            _gws_has = any(
                f for f in _default_ws.rglob("*")
                if f.is_file() and f.name not in (".gitkeep", ".gitignore")
                and not str(f).startswith(str(_cm_main.root))
            ) if _default_ws.exists() else False
            workspace = _cws if (_cws_has or not _gws_has) else _default_ws

        _default_sessions = Path("memory/sessions")
        if sessions_dir == _default_sessions or sessions_dir == _default_sessions.resolve():
            _css = _cm_main.root / _active_company_main.id / "memory" / "sessions"
            _css.mkdir(parents=True, exist_ok=True)
            sessions_dir = _css
        console.print(
            f"Empresa ativa: [bold cyan]{_active_company_main.name}[/bold cyan]"
        )

    router = _build_router(cfg, workspace)

    # ── ModelRouter: só faz sentido com Ollama (múltiplos modelos locais) ────
    # Com provider cloud (Groq, OpenAI, etc.) há apenas UM modelo configurado.
    # Rodar o classificador (qwen3:0.6b) pra rotear pro mesmo modelo é overhead
    # sem ganho — desabilitamos o router automaticamente nesses casos.
    model_router = None
    orchestrator = None
    if cfg.router.enabled:
        if not is_ollama_provider:
            pass  # cloud provider: router silently disabled
        elif not _ollama_alive:
            console.print(
                f"[yellow]Roteamento desabilitado:[/yellow] Ollama offline em {cfg.ollama.host}.\n"
                f"[dim]O ModelRouter precisa do Ollama local para o classificador "
                f"({cfg.router.router_model}).[/dim]"
            )
        else:
            # Ollama com múltiplos modelos — roteamento faz sentido
            router_cfg = RouterConfig(
                router_model=cfg.router.router_model,
                default_model=model_name,
                routes=[
                    Route("code",       "codigo",     cfg.router.code_model),
                    Route("reasoning",  "raciocinio", cfg.router.reasoning_model),
                    Route("tool",       "ferramenta", cfg.router.code_model),
                    Route("direct",     "direto",     cfg.router.direct_model),
                    Route("orchestrate","orquestrar", cfg.router.reasoning_model),
                ],
            )
            model_router = ModelRouter(_ollama, router_cfg)

            _parallel = cfg.runtime.profile == "high"
            orch_cfg = OrchestratorConfig(
                planner_model=cfg.router.router_model,
                synthesizer_model=cfg.router.reasoning_model,
                max_steps=MAX_STEPS,
                parallel_steps=_parallel,
            )
            orchestrator = AgentOrchestrator(
                client, router, model_router, orch_cfg,
                planner_client=_ollama,
            )
            console.print(
                f"[dim]Router ativo ({cfg.router.router_model}) -> "
                f"code={cfg.router.code_model} | "
                f"reasoning={cfg.router.reasoning_model} | "
                f"direct={cfg.router.direct_model}[/dim]"
            )

    # ── Sessao persistente ───────────────────────────────────────────────────
    try:
        from ..sqlite_session_store import SqliteSessionStore
        store = SqliteSessionStore(sessions_dir)
    except Exception:
        from ..session_store import SessionStore
        store = SessionStore(sessions_dir)
    sid: str | None = None

    if resume:
        if session_id_opt:
            if store.exists(session_id_opt):
                sid = session_id_opt
                console.print(f"[yellow]Retomando sessao: {sid}[/yellow]")
            else:
                console.print(f"[red]Sessao '{session_id_opt}' nao encontrada.[/red]")
                sessions = store.list_sessions()
                if sessions:
                    console.print(f"[dim]Sessoes disponiveis: {', '.join(sessions[-5:])}[/dim]")
                raise typer.Exit(code=1)
        else:
            sessions = store.list_sessions()
            if sessions:
                sid = sessions[-1]  # mais recente (ordenado por nome = timestamp UUID)
                msgs = store.load(sid)
                console.print(
                    f"[yellow]Retomando ultima sessao: {sid} "
                    f"({len(msgs)} mensagens)[/yellow]"
                )
            else:
                console.print("[yellow]Nenhuma sessao anterior encontrada — iniciando nova.[/yellow]")

    if sid is None:
        sid = store.new_id()

    # ── Servidor HTTP embutido (opcional — para Claw3D sem bauer serve separado) ──
    _embedded_server_thread = None
    if port > 0:
        _embedded_server_thread = _start_embedded_server(
            client=client,
            model_name=model_name,
            applied_context=applied_context,
            router=router,
            sessions_dir=sessions_dir,
            api_key=api_key_opt or cfg.serve.api_key,
            host="0.0.0.0",
            port=port,
            console=console,
            config_path=config,
        )
        # ── Gateway WebSocket (opcional — requer --port) ──────────────────────
        if gateway_port > 0:
            _start_gateway_thread_cli(
                bauer_url=f"http://localhost:{port}",
                host="0.0.0.0",
                port=gateway_port,
                api_key=api_key_opt or cfg.serve.api_key,
                console=console,
            )
        else:
            console.print(
                "[dim]  Claw3D Gateway: desabilitado "
                "(use --gateway-port 18789 para ativar)[/dim]"
            )
    elif gateway_port > 0:
        console.print(
            "[yellow]Aviso:[/yellow] --gateway-port requer --port. "
            "Exemplo: [bold]bauer agent --port 7770 --gateway-port 18789[/bold]"
        )

    # L1: SelfTuner — ajusta modelo e contexto com base em RAM + histórico (Ollama only)
    if is_ollama_provider:
        try:
            from ..self_tuner import SelfTuner as _SelfTuner
            import json as _pref_json
            # L8: detecta preferência explícita do usuário (impede troca de modelo pelo tuner)
            _pref_file = _MEMORY_DIR / "model_preference.json"
            _user_preferred = False
            try:
                if _pref_file.exists():
                    _pref = _pref_json.loads(_pref_file.read_text(encoding="utf-8"))
                    _user_preferred = (
                        _pref.get("set_by") == "user"
                        and _pref.get("model", "") == model_name
                    )
            except Exception:
                pass
            _installed = [m.get("name", "") for m in (client.list_models() or [])]
            _tune = _SelfTuner(_MEMORY_DIR, cfg.runtime.safety_margin_mb).tune(
                desired_model=model_name,
                desired_context=applied_context,
                minimum_context=cfg.model.minimum_context,
                installed_models=_installed,
                registry=reg,
                ram_available_mb=state["ram_available_mb"],
                machine_id=state.get("machine_id", ""),
                honor_user_preference=_user_preferred,
            )
            if _tune.adjustments:
                for _adj in _tune.adjustments:
                    console.print(f"[dim cyan]Auto-tuner:[/dim cyan] [dim]{_adj}[/dim]")
                model_name = _tune.model
                applied_context = _tune.context_tokens
                if hasattr(client, "num_ctx"):
                    client.num_ctx = applied_context
            if _tune.warnings:
                for _w in _tune.warnings:
                    console.print(f"[yellow]Auto-tuner:[/yellow] {_w}")
        except Exception:
            pass  # nunca bloqueia o startup por falha do tuner

    # Constrói clientes de fallback (dedup + observabilidade no helper)
    _fallback_clients = _build_fallback_clients(cfg, console=console)

    def _rebuild_client_chat():
        """Reconstrói client + model_name + fallbacks a partir do config.yaml atual.

        Retorna 3-tupla — o /model ao vivo precisa renovar a cadeia de fallback,
        senão ela ficaria apontando para os fallbacks do modelo anterior.
        """
        from ..env_loader import load_dotenv as _lenv
        _lenv()
        _new_cfg, _ = _load_or_die(config, models)
        _new_client = _build_client(_new_cfg)
        _new_fallbacks = _build_fallback_clients(_new_cfg, console=console)
        return _new_client, _new_cfg.model.name, _new_fallbacks

    # L6: injeta recomendações do LearningEngine no system prompt
    _learning_hints: str | None = None
    try:
        from ..learning_engine import LearningEngine as _LE6
        _le6 = _LE6(_MEMORY_DIR)
        _recs = _le6.recommend(machine_id=state.get("machine_id", ""))
        if _recs:
            _hint_lines = []
            for _r in _recs[:3]:
                _hint_lines.append(f"- {_r.action}")
                if _r.reason:
                    _hint_lines.append(f"  (evidência: {_r.reason})")
            _learning_hints = "\n".join(_hint_lines)
    except Exception:
        pass

    # Roteamento heurístico por turno (Fase 12) — mesmo comportamento do serve:
    # opt-in via model.router_enabled + model.profiles no config.yaml.
    from ._runtime import heuristic_route_kit
    _route_profiles, _route_client_fn = heuristic_route_kit(cfg)
    if _route_profiles:
        console.print(
            f"[dim]Roteamento por turno ativo — tiers: "
            f"{', '.join(sorted(_route_profiles))}[/dim]"
        )

    import time as _time
    _session_start = _time.time()
    _session_result = "ok"
    try:
        run_agent_session(
            client, model_name, applied_context, console, router,
            model_router, orchestrator,
            session_store=store, session_id=sid,
            rebuild_client_fn=_rebuild_client_chat,
            fallback_clients=_fallback_clients or None,
            tool_timeout_s=cfg.agent.tool_timeout_s,
            learning_hints=_learning_hints,
            route_profiles=_route_profiles,
            route_client_fn=_route_client_fn,
        )
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            _session_result = "interrupted"
            console.print("\n[dim]Sessao encerrada pelo usuario.[/dim]")
        else:
            _session_result = "error"
            console.print(f"\n[red]Erro inesperado:[/red] {exc}")
            console.print("[dim]Execute 'bauer doctor' para verificar o ambiente.[/dim]")
        raise typer.Exit(code=1)
    finally:
        # L5: grava outcome da sessão automaticamente no MODEL_EXPERIENCE.md
        _session_dur = _time.time() - _session_start
        if _session_dur >= 10:  # ignora sessões de teste (< 10s)
            try:
                from ..learning_engine import LearningEngine as _LE
                _le = _LE(_MEMORY_DIR)
                _le.mm.append_entry(
                    "MODEL_EXPERIENCE.md",
                    f"{model_name} via {cfg.model.provider}",
                    fields={
                        "context_tokens": str(applied_context),
                        "result": _session_result,
                        "machine_id": state.get("machine_id", ""),
                        "lesson": f"duração: {_session_dur:.0f}s",
                    },
                )
            except Exception:
                pass  # nunca interrompe o fluxo por falha de gravação


def _start_embedded_server(
    *,
    client,
    model_name: str,
    applied_context: int,
    router,
    sessions_dir: Path,
    api_key: str,
    host: str,
    port: int,
    console: Console,
    config_path: "Path | None" = None,  # noqa: F821
):
    """Sobe o servidor HTTP em uma daemon thread e retorna o thread.

    O servidor usa a mesma configuração do agent (client, router, model),
    mas mantém sessões HTTP independentes das sessões do terminal.
    """
    import threading

    try:
        from ..server import create_app
        from ..agent import _build_system_prompt
    except ImportError as exc:
        console.print(f"[yellow]Servidor embutido indisponivel: {exc}[/yellow]")
        return None

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[yellow]uvicorn nao instalado — servidor embutido desabilitado.[/yellow]\n"
            "[dim]Instale com: pip install uvicorn[/dim]"
        )
        return None

    system_prompt = _build_system_prompt(router)
    fastapi_app = create_app(
        model_name=model_name,
        applied_context=applied_context,
        router=router,
        client=client,
        system_prompt=system_prompt,
        sessions_dir=sessions_dir,
        api_key=api_key,
        rate_limit_requests=60,
        rate_limit_window_s=60.0,
        config_path=config_path,
    )

    uv_config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level="error",   # silencioso — logs vao para o terminal do agent
        access_log=False,
    )
    uv_server = uvicorn.Server(uv_config)

    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(uv_server.serve())

    t = threading.Thread(target=_run, daemon=True, name="bauer-embedded-server")
    t.start()

    console.print(
        f"[dim]Servidor embutido em [bold]http://{host}:{port}[/bold] "
        f"(POST /v1/chat/completions) — Claw3D: floor local-runtime[/dim]"
    )
    return t


@agent_app.command("create")
def agent_create(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Cria um agent especializado em modo entrevista (wizard interativo)."""
    from ..agent_registry import AgentRegistry
    from ..agent_wizard import wizard_create_agent

    try:
        cfg = load_config(config)
        config_model = cfg.model.name
        config_provider = cfg.model.provider
    except ConfigError:
        config_model = ""
        config_provider = "ollama"

    registry = AgentRegistry(agents_file)
    wizard_create_agent(registry, config_model=config_model, config_provider=config_provider)


@agent_app.command("list")
def agent_list(
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Lista todos os agents criados."""
    from ..agent_registry import AgentRegistry
    from rich.table import Table

    registry = AgentRegistry(agents_file)
    agents = registry.list_agents()

    if not agents:
        console.print(
            "[yellow]Nenhum agent criado ainda.[/yellow]\n"
            "Crie um com: [bold]bauer agent create[/bold]"
        )
        return

    table = Table(title="Agents", show_lines=True)
    table.add_column("nome", style="cyan", no_wrap=True)
    table.add_column("descrição")
    table.add_column("modelo", style="dim")
    table.add_column("tools", style="dim")
    table.add_column("criado em", style="dim")

    for ag in agents:
        model_str = f"{ag.provider}/{ag.model}" if ag.model else "[dim]config.yaml[/dim]"
        tools_str = ", ".join(ag.tools) if ag.tools else "—"
        created = ag.created_at[:10] if ag.created_at else "—"
        table.add_row(ag.name, ag.description, model_str, tools_str, created)

    console.print(table)
    console.print("\n[dim]Para rodar: [bold]bauer agent run <nome>[/bold][/dim]")


@agent_app.command("run")
def agent_run(
    name: str = typer.Argument(..., help="Nome do agent (ex: python-expert)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Retoma a ultima sessao"),
    sessions_dir: Path = typer.Option(Path("memory/sessions"), "--sessions-dir"),
    no_intro: bool = typer.Option(False, "--no-intro", help="Pula a animacao de entrada"),
):
    """Inicia um agent especializado pelo nome."""
    from ..agent_registry import AgentRegistry

    registry = AgentRegistry(agents_file)
    ag = registry.get(name)

    # Fallback: busca em workspace/agents.yaml e companies/<slug>/agents.yaml
    if ag is None:
        _extra_paths: list[Path] = [
            _WORKSPACE_DIR / "agents.yaml",           # workspace/agents.yaml
        ]
        # Empresa ativa → também checa o agents.yaml dela
        _cm_pre = None
        try:
            from ..company_manager import CompanyManager as _CMpre
            _cm_pre = _CMpre(_COMPANIES_DIR)
            _active_id = _cm_pre.get_active_id()
            if _active_id:
                _extra_paths.insert(0, _COMPANIES_DIR / _active_id / "agents.yaml")
        except Exception:
            pass

        for _xp in _extra_paths:
            if _xp.exists() and _xp != agents_file:
                _xreg = AgentRegistry(_xp)
                _xag = _xreg.get(name)
                if _xag:
                    ag = _xag
                    agents_file = _xp  # atualiza para salvar no lugar certo
                    break

    if ag is None:
        console.print(f"[yellow]Agent '[cyan]{name}[/cyan]' nao encontrado.[/yellow]")
        if typer.confirm(f"Criar o agent '{name}' agora?", default=True):
            from ..agent_wizard import wizard_create_agent
            cfg_tmp, _ = _load_or_die(config, models)
            ag = wizard_create_agent(
                registry,
                config_model=cfg_tmp.model.name,
                config_provider=cfg_tmp.model.provider,
            )
            if ag is None:
                raise typer.Exit(code=0)
        else:
            console.print(
                "Liste os agents: [bold]bauer agent list[/bold]\n"
                "Crie um novo:   [bold]bauer agent create[/bold]"
            )
            raise typer.Exit(code=1)

    # Carrega config base, sobrescreve modelo/provider se o agent define
    cfg, reg = _load_or_die(config, models)
    if ag.model:
        cfg.model.name = ag.model
    if ag.provider:
        cfg.model.provider = ag.provider  # type: ignore[assignment]

    # ── Detecção de empresa ativa (ANTES de construir o router) ─────────────
    # Feito aqui para garantir que workspace, sessions_dir, model/provider e
    # tools_allowed da empresa sejam todos aplicados antes de qualquer setup.
    from ..company_manager import CompanyManager
    _cm = CompanyManager(_COMPANIES_DIR)
    _active_company = _cm.get_active()

    if _active_company:
        # 1) Workspace: sempre usa companies/<slug>/workspace/ quando empresa está ativa.
        #    O workspace isolado é o padrão — não há mais fallback para o global.
        _default_ws = Path(_WORKSPACE_DIR)  # "workspace"
        if workspace == _default_ws or workspace == _default_ws.resolve():
            _company_ws = _cm.root / _active_company.id / "workspace"
            _company_ws.mkdir(parents=True, exist_ok=True)
            workspace = _company_ws

        # 2) Sessions: redireciona para companies/<slug>/memory/sessions/
        _default_sessions = Path("memory/sessions")
        if sessions_dir == _default_sessions or sessions_dir == _default_sessions.resolve():
            _company_sessions = _cm.root / _active_company.id / "memory" / "sessions"
            _company_sessions.mkdir(parents=True, exist_ok=True)
            sessions_dir = _company_sessions

        # 3) Model/provider da empresa (prioridade menor que o do agent)
        if _active_company.model and not ag.model:
            cfg.model.name = _active_company.model
        if _active_company.provider and not ag.provider:
            cfg.model.provider = _active_company.provider  # type: ignore[assignment]

    setup_logging(cfg.logging.level, cfg.logging.file)

    is_ollama_provider = cfg.model.provider == "ollama"

    from bauer.ollama_client import OllamaClient as _OllamaClient
    _ollama = _OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    _ollama_alive, _ = _ollama.is_alive()

    state = _get_or_run_state(cfg, reg, state_file)

    if is_ollama_provider and not state.get("ollama_alive"):
        console.print("[red]Ollama offline.[/red] Verifique se o Ollama esta rodando.")
        raise typer.Exit(code=1)

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    # Intro antes de qualquer mensagem de cliente/token
    if not resume:
        play_intro(console, skip=no_intro)

    client = _build_client(cfg)
    applied_context = state["context"]["applied"]
    model_name = ag.model or cfg.model.name

    # Constrói ToolRouter respeitando as tools do agent e da empresa ativa
    from ..agent_registry import ALL_TOOLS as _ALL_TOOLS
    allowed = set(ag.tools) if ag.tools else set(_ALL_TOOLS)
    # Se a empresa define tools_allowed, intersecta (empresa restringe o agent)
    if _active_company and _active_company.tools_allowed:
        allowed = allowed & set(_active_company.tools_allowed)
    # Constrói router com workspace CORRETO (empresa ou global) e llm_client para vision/delegate
    router = _build_router(cfg, workspace, llm_client=client)
    # Filtra tools fora do escopo do agent/empresa
    router._tools = {k: v for k, v in router._tools.items() if k in allowed}  # type: ignore[attr-defined]

    # ModelRouter/Orchestrator — só ativo com Ollama (múltiplos modelos locais).
    # Com provider cloud há apenas um modelo: routing é overhead sem ganho.
    model_router = None
    orchestrator = None
    if cfg.router.enabled and is_ollama_provider and _ollama_alive:
        router_cfg = RouterConfig(
            router_model=cfg.router.router_model,
            default_model=model_name,
            routes=[
                Route("code",       "codigo",     cfg.router.code_model),
                Route("reasoning",  "raciocinio", cfg.router.reasoning_model),
                Route("tool",       "ferramenta", cfg.router.code_model),
                Route("direct",     "direto",     cfg.router.direct_model),
                Route("orchestrate","orquestrar", cfg.router.reasoning_model),
            ],
        )
        model_router = ModelRouter(_ollama, router_cfg)

    # Sessao persistente — nomeada pelo agent para auto-resume automático.
    # Cada agent tem seu próprio histórico: "agent-<nome>.jsonl"
    # /clear dentro da sessão apaga o histórico e começa do zero.
    try:
        from ..sqlite_session_store import SqliteSessionStore
        store = SqliteSessionStore(sessions_dir)
    except Exception:
        from ..session_store import SessionStore
        store = SessionStore(sessions_dir)
    sid = f"agent-{ag.name}"
    _prev_msgs = store.load(sid)
    if _prev_msgs:
        console.print(
            f"[dim]Continuando sessao anterior — {len(_prev_msgs)} mensagens. "
            f"Use [bold]/clear[/bold] para reiniciar.[/dim]"
        )

    # Painel de informações do agent
    _ws_display = str(workspace)
    _company_badge = (
        f" | Empresa: [cyan]{_active_company.name}[/cyan]" if _active_company else ""
    )
    console.print(Panel(
        f"[cyan]{ag.name}[/cyan] — {ag.description}\n"
        f"[dim]Modelo: {cfg.model.provider}/{model_name} | "
        f"Tools: {', '.join(list(allowed)[:4])}{'…' if len(allowed) > 4 else ''}"
        f"{_company_badge}[/dim]\n"
        f"[dim]Workspace: {_ws_display}[/dim]",
        title="[bold]Agent Especializado[/bold]",
        border_style="cyan",
    ))

    # ── Resolve o workspace de identidade do agent ─────────────────────────
    # Regra: o agent usa os arquivos de identidade (SOUL/SKILLS/MEMORY/CONTEXT)
    # do workspace onde ele foi ENCONTRADO — não necessariamente o workspace ativo.
    #
    # Exemplos:
    #   alice (agents.yaml da bauer-corp)    → workspace/companies/bauer-corp/workspace/
    #   henrique-ferraz (workspace/agents.yaml global) → workspace/  ← workspace global
    #
    # Isso evita o bug de agents globais não encontrarem seus arquivos quando
    # uma empresa está ativa (que redireciona o workspace de trabalho).
    _agents_file_abs = agents_file.resolve()
    _company_ws_root = _cm.root  # workspace/companies/

    # Verifica se o agents_file está dentro do diretório de alguma empresa
    _identity_ws: Path
    try:
        _rel = _agents_file_abs.relative_to(_company_ws_root.resolve())
        # agents_file está dentro de workspace/companies/<slug>/...
        # → identity workspace = workspace/companies/<slug>/workspace/
        _company_slug = _rel.parts[0]
        _identity_ws = _company_ws_root / _company_slug / "workspace"
    except ValueError:
        # agents_file NÃO está dentro de companies/ → é um agent global
        # → identity workspace = workspace/ (global)
        _identity_ws = Path(_WORKSPACE_DIR)

    _agent_dir = _identity_ws / "agents" / ag.name

    # Verifica arquivos de identidade (silencioso — erros visíveis no prompt)
    if not _agent_dir.exists():
        pass  # agent sem diretório de identidade — sistema prompt padrão é usado

    # Injeta system prompt do agent + contexto da empresa
    from ..agent import run_agent_session as _run_session
    from ..agent import _build_system_prompt

    _original_build = _build_system_prompt

    def _agent_system_prompt(r):
        base = _original_build(r)
        specialization = f"\n\n# ESPECIALIZACAO\n{ag.system}"
        result = base + specialization

        # ── Injeção de arquivos de identidade do agent ───────────────────────
        # Ordem: SOUL → SKILLS → MEMORY → CONTEXT → Empresa
        # Usa _agent_dir resolvido acima (workspace correto por origem do agent)
        _inject_files = [
            ("SOUL.md",    "ALMA DO AGENTE",
             "Sua identidade, valores e princípios carregados de"),
            ("SKILLS.md",  "HABILIDADES DO AGENTE",
             "Suas habilidades e expertise carregadas de"),
            ("MEMORY.md",  "MEMÓRIA DO AGENTE",
             "Contexto de sessões anteriores carregado de. "
             "Ao encerrar, atualize com novos aprendizados via write_file."),
            ("CONTEXT.md", "CONTEXTO ATIVO",
             "Estado atual do projeto/objetivos carregado de. "
             "Atualize ao encerrar a sessão."),
        ]
        for _fname, _section, _desc in _inject_files:
            _fpath = _agent_dir / _fname
            if _fpath.exists():
                _content = _fpath.read_text(encoding="utf-8").strip()
                if _content:
                    result = result + (
                        f"\n\n# {_section}\n"
                        f"[{_desc} `agents/{ag.name}/{_fname}`]\n\n"
                        f"{_content}"
                    )

        # Prefixa contexto da empresa se houver empresa ativa
        if _active_company:
            result = _cm.inject_context(result, _active_company)

        return result

    import bauer.agent as _agent_mod
    _agent_mod._build_system_prompt = _agent_system_prompt  # type: ignore[attr-defined]

    def _rebuild_client_agent():
        """Reconstrói client + model_name + fallbacks a partir do config.yaml (live switch)."""
        from ..env_loader import load_dotenv as _lenv
        _lenv()
        _new_cfg, _ = _load_or_die(config, models)
        _new_client = _build_client(_new_cfg)
        _new_fallbacks = _build_fallback_clients(_new_cfg, console=console)
        return _new_client, _new_cfg.model.name, _new_fallbacks

    try:
        _run_session(
            client, model_name, applied_context, console, router,
            model_router, orchestrator,
            session_store=store, session_id=sid,
            rebuild_client_fn=_rebuild_client_agent,
        )
    finally:
        _agent_mod._build_system_prompt = _original_build  # type: ignore[attr-defined]


@agent_app.command("run-one")
def agent_run_one(
    task: str = typer.Argument(..., help="Tarefa para o sub-agente executar"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    agent: str = typer.Option(
        "", "--agent", help="Nome do agent especialista (agents.yaml) — aplica o system prompt dele"
    ),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Executa uma única tarefa e imprime o resultado (usado por delegate_task).

    Com --agent, carrega o system prompt do especialista do registry — sem
    isso, é um LLM genérico sem nenhuma especialização.
    """
    cfg, _ = _load_or_die(config, models)
    client = _build_client(cfg)
    from ..core.runtime.adapters import get_runtime_adapter

    system = ""
    model_name = cfg.model.name
    agent_spec = {}
    if agent:
        try:
            from ..agent_registry import AgentRegistry
            _ag = AgentRegistry(agents_file).get(agent)
            if _ag:
                system = _ag.system
                if _ag.model:
                    model_name = _ag.model
                from ..core.runtime.agent_spec import agent_spec_from_mapping
                agent_spec = agent_spec_from_mapping(_ag.to_dict()).to_dict()
        except Exception:
            pass  # registry indisponível — segue genérico

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task})
    try:
        adapter = get_runtime_adapter(config=cfg)
        result = adapter.run_agent({
            "client": client,
            "model": model_name,
            "messages": messages,
            "agent_id": agent or "",
            "agent_spec": agent_spec,
            "source": "cli.agent.run_one",
        })
        if result.get("status") == "failed":
            raise RuntimeError(str(result.get("error", "runtime adapter failed")))
        console.print(str(result.get("output", "")))
    except Exception as exc:
        console.print(f"[red]run-one: {exc}[/red]", err=True)
        raise typer.Exit(code=1)


@agent_app.command("delete")
def agent_delete(
    name: str = typer.Argument(..., help="Nome do agent a remover"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Pula confirmacao"),
):
    """Remove um agent do registry."""
    from ..agent_registry import AgentRegistry
    from rich.prompt import Confirm

    registry = AgentRegistry(agents_file)
    ag = registry.get(name)
    if ag is None:
        console.print(f"[red]Agent '{name}' nao encontrado.[/red]")
        raise typer.Exit(code=1)

    if not yes:
        if not Confirm.ask(f"[yellow]Remover agent '{name}'?[/yellow]", default=False):
            console.print("[dim]Cancelado.[/dim]")
            return

    registry.delete(name)
    console.print(f"[green]✓[/green] Agent [cyan]{name}[/cyan] removido.")


def _ensure_dispatcher_running(workspace: Path, config: Path, models: Path) -> None:
    """Inicia o dispatcher em background se ainda não estiver rodando."""
    import os as _os
    import subprocess as _sp

    pid_file = workspace / ".bauer_dispatch" / "daemon.pid"
    if pid_file.exists():
        try:
            import psutil
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if psutil.pid_exists(pid):
                return  # já rodando
        except Exception:
            pass  # PID stale — sobe um novo

    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_path = pid_file.parent / "daemon.log"
    log_handle = log_path.open("ab")

    cmd = [
        sys.executable, "-m", "bauer.cli", "dispatch", "daemon",
        "--workspace", str(workspace.resolve()),
        "--config", str(Path(config).resolve()),
        "--models", str(Path(models).resolve()),
    ]
    popen_kwargs: dict = {
        "stdout": log_handle,
        "stderr": _sp.STDOUT,
        "stdin": _sp.DEVNULL,
        "close_fds": True,
        "cwd": str(Path(__file__).resolve().parent.parent.parent),
    }
    if _os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(_sp, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        proc = _sp.Popen(cmd, **popen_kwargs)
        pid_file.write_text(str(proc.pid), encoding="utf-8")
        console.print(f"[dim]Dispatcher ativo (pid={proc.pid})[/dim]")
    except Exception:
        pass  # silencioso — dispatcher é opcional
    finally:
        log_handle.close()
