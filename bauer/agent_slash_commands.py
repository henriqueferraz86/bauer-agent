"""Handlers dos slash-commands da sessão do agente (/kanban, /spec, /agent,
/task, /dispatch, /ops, /memory, /project).

Extraídos de agent.py (god object): processam comandos digitados na sessão e
não participam do loop de tokens/tools. O despacho permanece em agent.py.
"""

from __future__ import annotations

from typing import Any


def _handle_kanban_cmd(console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Exibe o Kanban board (TASKS.md) do workspace ativo dentro da sessao."""
    import sys as _sys
    from rich.columns import Columns
    from rich.panel import Panel as _Panel
    from rich.text import Text as _Text

    try:
        from .workspace_manager_factory import get_workspace_manager
    except ImportError:
        console.print("[dim]WorkspaceManager nao disponivel.[/dim]")
        return

    wm = get_workspace_manager(workspace)
    tasks = wm.list_tasks()

    if not tasks:
        console.print("[dim]Nenhuma tarefa. Adicione com: [bold]bauer task add 'titulo'[/bold][/dim]")
        return

    _utf8 = _sys.platform != "win32" or (
        hasattr(_sys.stdout, "encoding") and
        (_sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
    )
    _ICONS = {
        "TODO":        "📋" if _utf8 else "[ ]",
        "READY":       "▶" if _utf8 else "[>]",
        "IN_PROGRESS": "🔄" if _utf8 else "[~]",
        "DONE":        "✅" if _utf8 else "[x]",
        "BLOCKED":     "🚫" if _utf8 else "[!]",
        "FAILED":      "✖" if _utf8 else "[x!]",
    }
    _BAR_FULL  = "█" if _utf8 else "#"
    _BAR_EMPTY = "░" if _utf8 else "."

    COLUMNS = [
        ("TODO",        "TODO",        "bright_white"),
        ("READY",       "READY",       "cyan"),
        ("IN_PROGRESS", "IN PROGRESS", "yellow"),
        ("BLOCKED",     "BLOCKED",     "red"),
        ("FAILED",      "FAILED",      "magenta"),
        ("DONE",        "DONE",        "green"),
    ]
    by_status: dict[str, list] = {s: [] for s, *_ in COLUMNS}
    for t in tasks:
        if t.status in by_status:
            by_status[t.status].append(t)

    panels = []
    for status, label, color in COLUMNS:
        col = by_status[status]
        lines = _Text()
        if not col:
            lines.append("  (vazio)\n", style="dim")
        else:
            for t in col:
                lines.append(f" [{t.id}] ", style="dim")
                lines.append(t.title + "\n", style=color)
        from rich.markup import escape as _esc
        panels.append(_Panel(
            lines,
            title=f"[bold {color}]{_esc(_ICONS[status])} {label} ({len(col)})[/bold {color}]",
            border_style=color,
            expand=True,
            padding=(0, 1),
        ))

    total = len(tasks)
    done = len(by_status["DONE"])
    pct = int(done / total * 100) if total else 0
    bar = _BAR_FULL * (pct // 5) + _BAR_EMPTY * (20 - pct // 5)

    console.print()
    console.print(Columns(panels, equal=True, expand=True))
    console.print(f"[dim]  Progresso: {bar} {pct}%  ({done}/{total} concluidas)[/dim]\n")

# ─── slash-command handler ────────────────────────────────────────────────

def _handle_spec_cmd(user_input: str, console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Processa comandos /spec digitados dentro da sessão do agente.

    Subcomandos:
      /spec          → lista specs existentes
      /spec list     → lista specs existentes
      /spec new      → wizard interativo para criar novo spec
      /spec new <id> → wizard com ID pré-preenchido
      /spec <id>     → exibe spec completo
    """
    from pathlib import Path as _Path
    from rich.panel import Panel
    from rich.table import Table

    try:
        from .spec_manager import SpecManager
        from .spec_wizard import wizard_create_spec
    except ImportError:
        console.print("[red]SpecManager nao disponivel.[/red]")
        return

    # Specs vivem sob o WORKSPACE (é lá que write_file('specs/x.yaml') do agente
    # cai, via sandbox), não em 'specs/' relativo ao cwd — rodando `bauer agent`
    # da home, o default cwd apontava para uma pasta vazia/inexistente.
    _specs_dir = _Path(str(workspace)) / "specs"
    mgr = SpecManager(_specs_dir)
    parts = user_input.strip().split()
    # parts[0] = "/spec", parts[1] = subcomando (opcional), parts[2] = id (opcional)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub in ("list", "ls"):
        specs = mgr.list_specs()
        if not specs:
            console.print(
                f"[dim]Nenhum spec em [cyan]{_specs_dir}[/cyan]. "
                "Use [bold]/spec new[/bold] para criar.[/dim]"
            )
            return
        table = Table(show_lines=False, box=None, title=f"Specs ({len(specs)})")
        table.add_column("id", style="cyan", no_wrap=True)
        table.add_column("status")
        table.add_column("ACs", style="dim", width=4)
        table.add_column("purpose", style="dim")
        _colors = {"draft":"dim","review":"yellow","approved":"blue","implemented":"green","deprecated":"red"}
        for s in specs:
            c = _colors.get(s.status, "white")
            purpose = s.purpose.split("\n")[0][:55] + ("…" if len(s.purpose) > 55 else "")
            table.add_row(s.id, f"[{c}]{s.status}[/{c}]", str(len(s.acceptance_criteria)), purpose)
        console.print(table)
        console.print("[dim]Use [bold]/spec <id>[/bold] para ver detalhes, [bold]/spec new[/bold] para criar.[/dim]")
        return

    if sub == "new":
        spec_id_hint = parts[2] if len(parts) > 2 else ""
        if spec_id_hint:
            console.print(f"[dim]Criando spec '[cyan]{spec_id_hint}[/cyan]'...[/dim]")
        wizard_create_spec(mgr)
        return

    # /spec <id> — exibe o spec
    spec = mgr.get(sub)
    if not spec:
        console.print(f"[yellow]Spec '[cyan]{sub}[/cyan]' nao encontrado.[/yellow]")
        console.print(f"[dim]Crie com: [bold]/spec new {sub}[/bold][/dim]")
        if mgr.list_specs():
            console.print(f"[dim]Specs existentes: {', '.join(s.id for s in mgr.list_specs())}[/dim]")
        return

    console.print(Panel(
        spec.to_context(compact=False),
        title=f"[bold cyan]{spec.id}[/bold cyan] — {spec.title}",
        border_style="cyan",
    ))

# ─── slash-command handler ────────────────────────────────────────────────

def _handle_agent_cmd(user_input: str, console) -> None:  # type: ignore[type-arg]
    """Processa comandos /agent digitados dentro da sessao do agente.

    Subcomandos:
      /agents              → lista agents (alias)
      /agent list          → lista agents criados
      /agent create        → wizard interativo para criar agent
      /agent delete <nome> → remove agent do registry
    """
    from rich.table import Table
    from rich.prompt import Confirm

    try:
        from .agent_registry import (
            AgentRegistry,
            list_builtin_specialists,
            merged_specialist_pool,
            resolve_user_agents_path,
        )
        from .agent_wizard import wizard_create_agent
    except ImportError:
        console.print("[red]AgentRegistry nao disponivel.[/red]")
        return

    parts = user_input.strip().split(maxsplit=2)
    # "/agents" (sem espaço) → list
    cmd0 = parts[0].lower()
    sub = parts[1].lower() if len(parts) > 1 else "list"
    if cmd0 == "/agents":
        sub = "list"

    # Registry do USUÁRIO resolvido pela mesma prioridade do resto do Bauer
    # (~/.bauer/agents.yaml, env, cwd) — NÃO "agents.yaml" relativo ao cwd, que
    # rodando `bauer agent` da home nunca encontrava nada.
    _user_agents_path = resolve_user_agents_path()
    registry = AgentRegistry(_user_agents_path)

    if sub in ("list", "ls"):
        # Pool mesclado: 10 especialistas embutidos do pacote + agents do
        # usuário (user sobrescreve builtin por nome). Antes só lia o cwd e
        # aparecia "nenhum agent" mesmo com os 10 especialistas disponíveis.
        agents = merged_specialist_pool(_user_agents_path)
        _builtin_names = {a.name for a in list_builtin_specialists()}
        if not agents:
            console.print(
                "[yellow]Nenhum agent disponivel.[/yellow]\n"
                "Crie um com: [bold]/agent create[/bold]"
            )
            return
        agents = sorted(agents, key=lambda a: (a.name not in _builtin_names, a.name))
        table = Table(title=f"Agents ({len(agents)})", show_lines=True)
        table.add_column("nome",     style="cyan", no_wrap=True)
        table.add_column("tipo",     style="dim", no_wrap=True)
        table.add_column("descricao")
        table.add_column("modelo",   style="dim")
        table.add_column("tools",    style="dim")
        for ag in agents:
            model_str = f"{ag.provider}/{ag.model}" if ag.model else "[dim]config.yaml[/dim]"
            tools_str = ", ".join(ag.tools) if ag.tools else "—"
            tipo = "[blue]especialista[/blue]" if ag.name in _builtin_names else (
                "[magenta]remoto[/magenta]" if ag.url else "[green]seu[/green]"
            )
            table.add_row(ag.name, tipo, ag.description, model_str, tools_str)
        console.print(table)
        console.print(
            f"[dim]{len(_builtin_names)} especialistas embutidos + seus agents em "
            f"[cyan]{_user_agents_path}[/cyan]. Rodar: [bold]bauer agent run <nome>[/bold] | "
            "Criar: [bold]/agent create[/bold][/dim]"
        )
        return

    if sub == "create":
        wizard_create_agent(registry)
        return

    if sub == "delete":
        nome = parts[2].strip() if len(parts) > 2 else ""
        if not nome:
            console.print("[yellow]Uso: [bold]/agent delete <nome>[/bold][/yellow]")
            return
        ag = registry.get(nome)
        if ag is None:
            console.print(f"[red]Agent '[cyan]{nome}[/cyan]' nao encontrado.[/red]")
            agents = registry.list_agents()
            if agents:
                console.print(f"[dim]Agents existentes: {', '.join(a.name for a in agents)}[/dim]")
            return
        try:
            if not Confirm.ask(f"[yellow]Remover agent '[cyan]{nome}[/cyan]'?[/yellow]", default=False):
                console.print("[dim]Cancelado.[/dim]")
                return
        except Exception:
            # fallback se Confirm nao estiver disponivel (ex: pipe)
            pass
        registry.delete(nome)
        console.print(f"[green]✓[/green] Agent [cyan]{nome}[/cyan] removido.")
        return

    console.print(f"[yellow]Subcomando desconhecido: [bold]/agent {sub}[/bold][/yellow]")
    console.print("[dim]Disponiveis: list | create | delete <nome>[/dim]")

# ─── slash-command handler ────────────────────────────────────────────────

def _handle_task_cmd(user_input: str, console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Processa comandos /task digitados dentro da sessao do agente.

    Subcomandos:
      /task               → exibe Kanban board (delega a _handle_kanban_cmd)
      /task list          → lista tarefas com status
      /task add <titulo>  → adiciona nova tarefa
      /task ready <id>    → muda status para READY e habilita dispatcher
      /task start <id>    → muda status para IN_PROGRESS
      /task done <id>     → muda status para DONE
      /task block <id>    → muda status para BLOCKED
      /task fail <id>     → muda status para FAILED
    """
    from rich.table import Table

    try:
        from .workspace_manager import WorkspaceError
        from .workspace_manager_factory import get_workspace_manager
    except ImportError:
        console.print("[red]WorkspaceManager nao disponivel.[/red]")
        return

    parts = user_input.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else ""

    # bare /task → board
    if not sub:
        _handle_kanban_cmd(console, workspace)
        return

    wm = get_workspace_manager(workspace)

    if sub in ("list", "ls"):
        tasks = wm.list_tasks()
        if not tasks:
            console.print("[dim]Nenhuma tarefa. Use [bold]/task add <titulo>[/bold] para criar.[/dim]")
            return
        _STATUS_COLORS = {
            "TODO": "bright_white", "READY": "cyan",
            "IN_PROGRESS": "yellow", "DONE": "green",
            "BLOCKED": "red", "FAILED": "magenta",
        }
        table = Table(show_lines=False, box=None)
        table.add_column("ID",     style="dim",    width=4, no_wrap=True)
        table.add_column("Status", width=12)
        table.add_column("Titulo")
        for t in tasks:
            c = _STATUS_COLORS.get(t.status, "white")
            table.add_row(t.id, f"[{c}]{t.status}[/{c}]", t.title)
        console.print(table)
        return

    if sub == "add":
        titulo = parts[2].strip() if len(parts) > 2 else ""
        if not titulo:
            console.print("[yellow]Uso: [bold]/task add <titulo>[/bold][/yellow]")
            return
        task = wm.add_task(titulo)
        console.print(f"[green]Tarefa adicionada:[/green] [[dim]{task.id}[/dim]] {task.title}")
        return

    if sub == "ready":
        task_id = parts[2].strip() if len(parts) > 2 else ""
        if not task_id:
            console.print("[yellow]Uso: [bold]/task ready <id>[/bold][/yellow]")
            return
        try:
            from .task_dispatcher import TaskDispatcher
            task = TaskDispatcher(workspace).mark_ready(task_id)
            console.print(
                f"[green]Tarefa pronta para dispatcher:[/green] "
                f"[[dim]{task.id}[/dim]] {task.title} → [READY]"
            )
        except Exception as exc:
            console.print(f"[red]Erro:[/red] {exc}")
        return

    # start / done / block / fail → precisam de <id>
    _STATUS_MAP = {"start": "IN_PROGRESS", "done": "DONE", "block": "BLOCKED", "fail": "FAILED"}
    if sub in _STATUS_MAP:
        task_id = parts[2].strip() if len(parts) > 2 else ""
        if not task_id:
            console.print(f"[yellow]Uso: [bold]/task {sub} <id>[/bold][/yellow]")
            return
        new_status = _STATUS_MAP[sub]
        try:
            task = wm.update_task_status(task_id, new_status)
            _VERBS = {"IN_PROGRESS": "iniciada", "DONE": "concluida", "BLOCKED": "bloqueada", "FAILED": "falhou"}
            console.print(
                f"[green]Tarefa {_VERBS[new_status]}:[/green] "
                f"[[dim]{task.id}[/dim]] {task.title} → [{new_status}]"
            )
        except Exception as exc:
            console.print(f"[red]Erro:[/red] {exc}")
        return

    console.print(f"[yellow]Subcomando desconhecido: [bold]/task {sub}[/bold][/yellow]")
    console.print("[dim]Disponiveis: add | list | ready | start | done | block | fail[/dim]")

# ─── slash-command handler ────────────────────────────────────────────────

def _handle_dispatch_cmd(user_input: str, console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Processa comandos /dispatch dentro da sessao do agente."""
    import shlex
    from collections import Counter

    from rich.table import Table

    try:
        parts = shlex.split(user_input)
    except ValueError as exc:
        console.print(f"[red]Erro ao ler comando:[/red] {exc}")
        return

    sub = parts[1].lower() if len(parts) > 1 else "once"
    args = parts[2:]

    def _int_option(names: tuple[str, ...], default: int) -> int:
        for idx, token in enumerate(args):
            for name in names:
                if token == name and idx + 1 < len(args):
                    try:
                        return max(1, int(args[idx + 1]))
                    except ValueError:
                        return default
                prefix = name + "="
                if token.startswith(prefix):
                    try:
                        return max(1, int(token[len(prefix):]))
                    except ValueError:
                        return default
        return default

    if sub in ("help", "-h", "--help"):
        console.print(
            "[bold]Uso:[/bold]\n"
            "  /dispatch                 # um tick em background\n"
            "  /dispatch once --dry-run  # mostra o que seria claimed\n"
            "  /dispatch once --foreground\n"
            "  /dispatch status\n"
            "  /dispatch reclaim\n"
            "  /dispatch cancel <id>\n"
            "  /dispatch retry <id>\n"
            "\n[dim]READY e fila. IN_PROGRESS e worker claimed. DONE/FAILED sao finais.[/dim]"
        )
        return

    try:
        from .task_dispatcher import TaskDispatcher
        from .workspace_manager_factory import get_workspace_manager
    except ImportError as exc:
        console.print(f"[red]Dispatcher nao disponivel:[/red] {exc}")
        return

    if sub in ("status", "queue", "fila"):
        from .kanban_store import KanbanStore

        wm = get_workspace_manager(workspace)
        store = KanbanStore(workspace)
        tasks = wm.list_tasks()
        counts = Counter(t.status for t in tasks)
        table = Table(title=f"Dispatcher - {workspace}", show_lines=False)
        table.add_column("Status", style="cyan")
        table.add_column("Qtd", justify="right")
        for status in ("READY", "IN_PROGRESS", "FAILED", "DONE", "BLOCKED", "TODO"):
            table.add_row(status, str(counts.get(status, 0)))
        console.print(table)

        running = [t for t in tasks if t.status == "IN_PROGRESS" and t.metadata.get("dispatch") == "true"]
        if running:
            run_table = Table(title="Claims ativos", show_lines=False)
            run_table.add_column("ID", style="dim", no_wrap=True)
            run_table.add_column("Tentativas", justify="right")
            run_table.add_column("Worker")
            run_table.add_column("Heartbeat", style="dim")
            run_table.add_column("Titulo")
            for task in running:
                run_table.add_row(
                    task.id,
                    task.metadata.get("attempts", "0"),
                    task.metadata.get("worker_pid") or task.metadata.get("claimed_by", ""),
                    task.metadata.get("heartbeat_at", ""),
                    task.title,
                )
            console.print(run_table)
        else:
            console.print("[dim]Nenhum claim ativo do dispatcher.[/dim]")

        runs = store.list_runs(statuses=["claimed", "running", "retrying"], limit=10)
        if runs:
            runs_table = Table(title="Runs ativos/recentes", show_lines=False)
            runs_table.add_column("Run", style="dim")
            runs_table.add_column("Task")
            runs_table.add_column("Status")
            runs_table.add_column("Tent.", justify="right")
            runs_table.add_column("Heartbeat", style="dim")
            for run in runs:
                runs_table.add_row(run.run_id, run.task_id, run.status, str(run.attempt), run.heartbeat_at)
            console.print(runs_table)

        events = store.list_events(limit=5)
        if events:
            events_table = Table(title="Ultimos eventos", show_lines=False)
            events_table.add_column("Task")
            events_table.add_column("Evento")
            events_table.add_column("Ator")
            events_table.add_column("Mensagem")
            for event in events:
                events_table.add_row(event.task_id, event.event_type, event.actor, event.message[:80])
            console.print(events_table)
        return

    if sub in ("daemon", "loop"):
        console.print(
            "[yellow]/dispatch daemon nao roda dentro do chat para nao bloquear a sessao.[/yellow]\n"
            "[dim]Use no terminal: [bold]bauer dispatch daemon --workspace <workspace>[/bold][/dim]"
        )
        return

    if sub in ("reclaim", "recover"):
        dispatcher = TaskDispatcher(workspace)
        crashed = dispatcher.detect_crashed_workers()
        reclaimed = dispatcher.reclaim_stale()
        console.print(
            "[bold cyan]dispatch reclaim[/bold cyan] "
            f"crashed={len(crashed)} reclaimed={len(reclaimed)}"
        )
        if crashed:
            console.print(f"[dim]crashed:[/dim] {', '.join(crashed)}")
        if reclaimed:
            console.print(f"[dim]reclaimed:[/dim] {', '.join(reclaimed)}")
        return

    if sub in ("cancel", "retry"):
        if not args:
            console.print(f"[yellow]Uso: /dispatch {sub} <task_id>[/yellow]")
            return
        dispatcher = TaskDispatcher(workspace)
        try:
            if sub == "cancel":
                task = dispatcher.cancel_task(args[0], reason="cancelado via chat")
                console.print(f"[yellow]{task.id}[/yellow] -> [BLOCKED] {task.title}")
            else:
                task = dispatcher.retry_failed(args[0], reason="retry via chat")
                console.print(f"[cyan]{task.id}[/cyan] -> [READY] {task.title}")
        except Exception as exc:
            console.print(f"[red]Erro no dispatcher:[/red] {exc}")
        return

    if sub not in ("once", "run", "tick"):
        console.print(f"[yellow]Subcomando desconhecido: [bold]/dispatch {sub}[/bold][/yellow]")
        console.print("[dim]Disponiveis: once | status | reclaim | cancel | retry | help[/dim]")
        return

    dry_run = any(a in ("--dry-run", "--dry") for a in args)
    foreground = any(a in ("--foreground", "-f") for a in args)
    max_spawn = _int_option(("--max-spawn", "--max"), 1)
    max_in_progress = _int_option(("--max-in-progress", "--limit"), 1)

    dispatcher = TaskDispatcher(workspace)
    try:
        result = dispatcher.dispatch_once(
            dry_run=dry_run,
            max_spawn=max_spawn,
            max_in_progress=max_in_progress,
            spawn_background=not foreground,
        )
    except Exception as exc:
        console.print(f"[red]Erro no dispatcher:[/red] {exc}")
        return

    console.print(
        "[bold cyan]dispatch once[/bold cyan] "
        f"crashed={len(result.crashed)} reclaimed={len(result.reclaimed)} claimed={len(result.claimed)} "
        f"spawned={len(result.spawned)} completed={len(result.completed)} "
        f"failed={len(result.failed)} dry={len(result.dry_run)}"
    )
    any_activity = False
    for label, items in (
        ("crashed", result.crashed),
        ("reclaimed", result.reclaimed),
        ("claimed", result.claimed),
        ("spawned", result.spawned),
        ("completed", result.completed),
        ("failed", result.failed),
        ("dry", result.dry_run),
        ("skipped", result.skipped),
    ):
        if items:
            any_activity = True
            console.print(f"[dim]{label}:[/dim] {', '.join(items)}")

    if result.spawned and not foreground:
        console.print("[dim]Workers em background. Acompanhe com [bold]/task[/bold] ou [bold]/dispatch status[/bold].[/dim]")
    if not any_activity:
        console.print("[dim]Nenhuma task READY elegivel. Use [bold]/task ready <id>[/bold] para entrar na fila.[/dim]")

# ─── slash-command handler ────────────────────────────────────────────────

def _handle_ops_cmd(user_input: str, console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Processa /ops dentro da sessao do agente."""
    from rich.table import Table

    from .ops_status import build_ops_status

    parts = user_input.strip().split()
    sub = parts[1].lower() if len(parts) > 1 else "status"
    if sub not in ("status", "queue", "fila", "lanes"):
        console.print("[yellow]Uso: /ops status[/yellow]")
        return

    status = build_ops_status(workspace, limit=8)
    counts = status["status_counts"]
    summary = Table(title=f"Ops - {workspace}", show_lines=False)
    summary.add_column("Status", style="cyan")
    summary.add_column("Qtd", justify="right")
    for name in ("READY", "IN_PROGRESS", "FAILED", "BLOCKED", "TODO", "DONE"):
        summary.add_row(name, str(counts.get(name, 0)))
    console.print(summary)

    lanes = status.get("lanes", [])
    if lanes:
        lane_table = Table(title="Lanes", show_lines=False)
        lane_table.add_column("Lane", style="cyan")
        lane_table.add_column("Agent")
        lane_table.add_column("Cap.", justify="right")
        lane_table.add_column("Ready", justify="right")
        lane_table.add_column("Run", justify="right")
        lane_table.add_column("Fail", justify="right")
        for lane in lanes:
            lane_table.add_row(
                str(lane.get("lane", "")),
                str(lane.get("agent", "")),
                str(lane.get("max_concurrent", "")),
                str(lane.get("ready", 0)),
                str(lane.get("running", 0)),
                str(lane.get("failed", 0)),
            )
        console.print(lane_table)

    claims = status.get("active_claims", [])
    if claims:
        claim_table = Table(title="Claims ativos", show_lines=False)
        claim_table.add_column("Task", style="cyan")
        claim_table.add_column("Lane")
        claim_table.add_column("PID")
        claim_table.add_column("Alive")
        claim_table.add_column("Lease")
        for claim in claims:
            lease = claim.get("claim_seconds_left")
            claim_table.add_row(
                str(claim.get("public_id", "")),
                str(claim.get("lane", "")),
                str(claim.get("worker_pid") or ""),
                str(claim.get("worker_alive")),
                "" if lease is None else f"{lease}s",
            )
        console.print(claim_table)
    else:
        console.print("[dim]Nenhum claim ativo.[/dim]")

    events = status.get("recent_events", [])[:5]
    if events:
        events_table = Table(title="Eventos recentes", show_lines=False)
        events_table.add_column("Task")
        events_table.add_column("Evento")
        events_table.add_column("Mensagem")
        for event in events:
            events_table.add_row(
                str(event.get("task_id", "")),
                str(event.get("event_type", "")),
                str(event.get("message", ""))[:80],
            )
        console.print(events_table)

# ─── slash-command handler ────────────────────────────────────────────────

def _handle_memory_cmd(user_input: str, console) -> None:  # type: ignore[type-arg]
    """Processa comandos /memory digitados dentro da sessao do agente.

    Subcomandos:
      /memory                  → lista arquivos de memoria
      /memory list             → lista arquivos de memoria
      /memory search <query>   → busca TF-IDF nos arquivos Markdown
      /memory note <texto>     → adiciona nota rapida
    """
    from pathlib import Path as _Path
    from rich.table import Table

    parts = user_input.strip().split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    try:
        from .memory_manager import MemoryManager
    except ImportError:
        console.print("[red]MemoryManager nao disponivel.[/red]")
        return

    mm = MemoryManager()

    if sub in ("list", "ls", ""):
        mem_dir = _Path("memory")
        if not mem_dir.exists():
            console.print("[dim]Diretorio memory/ nao encontrado.[/dim]")
            return
        files = sorted(mem_dir.glob("*.md"))
        if not files:
            console.print("[dim]Nenhum arquivo de memoria encontrado.[/dim]")
            return
        table = Table(show_lines=False, box=None, title=f"Memoria ({len(files)} arquivos)")
        table.add_column("Arquivo",  style="cyan")
        table.add_column("Tamanho",  style="dim", justify="right")
        for f in files:
            size = f.stat().st_size
            table.add_row(f.name, f"{size:,} B")
        console.print(table)
        console.print("[dim]Use [bold]/memory search <query>[/bold] para buscar.[/dim]")
        return

    if sub == "search":
        query = parts[2].strip() if len(parts) > 2 else ""
        if not query:
            console.print("[yellow]Uso: [bold]/memory search <query>[/bold][/yellow]")
            return
        try:
            results = mm.search(query, top_k=5)
        except Exception as exc:
            console.print(f"[red]Erro na busca:[/red] {exc}")
            return
        if not results:
            console.print(f"[dim]Nenhum resultado para '[cyan]{query}[/cyan]'.[/dim]")
            return
        table = Table(show_lines=True, box=None, title=f"Resultados: '{query}'")
        table.add_column("Score", style="dim", width=7, justify="right")
        table.add_column("Arquivo", style="cyan", no_wrap=True)
        table.add_column("Secao")
        table.add_column("Trecho", style="dim")
        for r in results:
            score_str = f"{r['score']:.3f}"
            snippet = (r.get("snippet", "") or "")[:70].replace("\n", " ")
            table.add_row(score_str, r["file"], r.get("title", ""), snippet)
        console.print(table)
        return

    if sub == "note":
        note_text = parts[2].strip() if len(parts) > 2 else ""
        if not note_text:
            console.print("[yellow]Uso: [bold]/memory note <texto>[/bold][/yellow]")
            return
        try:
            # Usa as primeiras 60 chars como titulo e o texto completo como corpo
            title = note_text[:60] + ("..." if len(note_text) > 60 else "")
            mm.add_note(title, note_text)
            console.print(f"[green]Nota adicionada:[/green] {title}")
        except Exception as exc:
            console.print(f"[red]Erro ao salvar nota:[/red] {exc}")
        return

    console.print(f"[yellow]Subcomando desconhecido: [bold]/memory {sub}[/bold][/yellow]")
    console.print("[dim]Disponiveis: list | search <query> | note <texto>[/dim]")

# ─── slash-command handler ────────────────────────────────────────────────

def _handle_project_cmd(console, workspace: Any = "workspace") -> None:  # type: ignore[type-arg]
    """Exibe PROJECT.md e um resumo das tarefas do workspace."""
    from pathlib import Path as _Path
    from rich.panel import Panel as _Panel

    project_file = _Path(workspace) / "PROJECT.md"
    tasks_summary_parts: list[str] = []

    # Tenta carregar resumo de tarefas
    try:
        from .workspace_manager_factory import get_workspace_manager
        wm = get_workspace_manager(workspace)
        tasks = wm.list_tasks()
        if tasks:
            from collections import Counter
            counts = Counter(t.status for t in tasks)
            total = len(tasks)
            done = counts.get("DONE", 0)
            pct = int(done / total * 100) if total else 0
            tasks_summary_parts.append(
                f"[dim]Tarefas: {total} total | "
                f"[green]{counts.get('DONE', 0)} DONE[/green] | "
                f"[yellow]{counts.get('IN_PROGRESS', 0)} IN_PROGRESS[/yellow] | "
                f"[cyan]{counts.get('READY', 0)} READY[/cyan] | "
                f"[white]{counts.get('TODO', 0)} TODO[/white] | "
                f"[red]{counts.get('BLOCKED', 0)} BLOCKED[/red] | "
                f"[magenta]{counts.get('FAILED', 0)} FAILED[/magenta] | "
                f"{pct}% concluido[/dim]"
            )
    except Exception:
        pass

    console.print()
    if project_file.exists():
        content = project_file.read_text(encoding="utf-8")
        # Exibe primeiros 50 linhas para nao inundar o terminal
        lines = content.splitlines()
        preview = "\n".join(lines[:50])
        if len(lines) > 50:
            preview += f"\n\n[dim]... (+{len(lines) - 50} linhas — abra workspace/PROJECT.md para ver tudo)[/dim]"
        console.print(_Panel(preview, title="[bold cyan]PROJECT.md[/bold cyan]", border_style="cyan"))
    else:
        console.print("[dim]PROJECT.md nao encontrado no workspace.[/dim]")

    for line in tasks_summary_parts:
        console.print(line)

    # Projetos governados pela App Factory (1 ideia = 1 pasta): é aqui que
    # moram os apps do usuário (ex.: bauerinvest/, nexusalpha/), não num
    # PROJECT.md solto na raiz. Lista-os + marca o projeto ativo.
    try:
        from . import app_factory as _af
        from rich.table import Table as _Table

        _ws = _Path(str(workspace))
        _active = _af.get_active_project(_ws)
        _active_name = _active.name if _active is not None else None
        _governed = []
        if _ws.is_dir():
            for _sub in sorted(p for p in _ws.iterdir() if p.is_dir()):
                if _af.is_governed(_sub):
                    _governed.append(_sub)
        if _governed:
            _t = _Table(title=f"Projetos App Factory ({len(_governed)})", show_lines=False, box=None)
            _t.add_column("projeto", style="cyan", no_wrap=True)
            _t.add_column("gate", no_wrap=True)
            _t.add_column("score", style="dim", no_wrap=True)
            _t.add_column("", style="green", no_wrap=True)
            for _p in _governed:
                _gate = _af.current_gate(_p)
                _sc = _af.delivery_score(_p)
                _mark = "● ativo" if _p.name == _active_name else ""
                _t.add_row(
                    _p.name,
                    _gate.slug if _gate is not None else "—",
                    f"{_sc.get('score', 0)}/10",
                    _mark,
                )
            console.print()
            console.print(_t)
            console.print(
                "[dim]Detalhe de um projeto: [bold]/spec list[/bold] · "
                "[bold]app_factory_status[/bold] (tool). Novo: descreva a ideia "
                "e o Bauer chama [bold]app_factory_init[/bold].[/dim]"
            )
        elif not project_file.exists():
            console.print(
                "[dim]Nenhum projeto governado ainda. Descreva uma ideia de app "
                "e o Bauer inicia a App Factory automaticamente.[/dim]"
            )
    except Exception:
        pass

    console.print()
