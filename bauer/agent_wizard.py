"""Wizards interativos (modo entrevista) para criação de agents, tasks e orchestrate.

Cada wizard faz perguntas em sequência com Rich Prompt, valida as respostas
e retorna o objeto pronto para salvar/usar.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table

from .agent_registry import (
    ALL_TOOLS,
    DEFAULT_TOOLS,
    PERSONAS,
    AgentDef,
    AgentRegistry,
)

console = Console(highlight=False)

# ─── helpers ────────────────────────────────────────────────────────────────


def _header(title: str, subtitle: str = "") -> None:
    console.print()
    console.print(Rule(f"[bold cyan]{title}[/bold cyan]"))
    if subtitle:
        console.print(f"[dim]{subtitle}[/dim]")
    console.print()


def _ask(prompt: str, default: str = "", password: bool = False) -> str:
    return Prompt.ask(f"[bold]{prompt}[/bold]", default=default, password=password).strip()


def _pick_numbered(
    items: list[tuple[str, str]],
    title: str,
    allow_empty: bool = False,
) -> str | None:
    """Exibe tabela numerada, retorna o id escolhido ou None se cancelado."""
    table = Table(title=title, show_lines=False, box=None)
    table.add_column("#", style="dim", width=3)
    table.add_column("opção", style="cyan")
    table.add_column("descrição")
    for i, (id_, desc) in enumerate(items, 1):
        table.add_row(str(i), id_, desc)
    console.print(table)

    default_hint = " (Enter para pular)" if allow_empty else ""
    raw = Prompt.ask(
        f"[bold]Escolha pelo número[/bold]{default_hint}",
        default="",
    ).strip()
    if not raw:
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(items):
            return items[idx][0]
    except ValueError:
        for id_, _ in items:
            if raw == id_:
                return raw
    return raw


def _pick_multi(items: list[str], selected: list[str], title: str) -> list[str]:
    """Seleção múltipla numerada. Enter confirma sem mudanças."""
    while True:
        table = Table(title=title, show_lines=False, box=None)
        table.add_column("#", style="dim", width=3)
        table.add_column("tool", style="cyan")
        table.add_column("status")
        for i, item in enumerate(items, 1):
            mark = "[green]✓[/green]" if item in selected else "[dim]○[/dim]"
            table.add_row(str(i), item, mark)
        console.print(table)

        raw = Prompt.ask(
            "[bold]Número para toggle (Enter para confirmar)[/bold]",
            default="",
        ).strip()
        if not raw:
            break
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                tool = items[idx]
                if tool in selected:
                    selected.remove(tool)
                else:
                    selected.append(tool)
        except ValueError:
            console.print("[dim]Digite um número válido.[/dim]")
    return selected


# ─── Wizard: criar agent ────────────────────────────────────────────────────


def wizard_create_agent(
    registry: AgentRegistry,
    config_model: str = "",
    config_provider: str = "ollama",
) -> AgentDef | None:
    """Entrevista interativa para criar um agent. Retorna AgentDef ou None se cancelado."""

    _header(
        "Criar Agent",
        "Responda as perguntas para definir seu agent especializado.",
    )

    # ── 1. Nome ───────────────────────────────────────────────────────────
    console.print("[bold]1/6 — Nome do agent[/bold]")
    console.print("[dim]Use letras minúsculas, números e hífens. Ex: python-expert, devops-pro[/dim]")
    while True:
        name = _ask("Nome", default="meu-agent")
        if not AgentDef.valid_name(name):
            console.print("[red]Nome inválido.[/red] Use apenas letras minúsculas, números e hífens (2-31 chars).")
            continue
        if registry.get(name):
            overwrite = Confirm.ask(
                f"[yellow]Agent '{name}' já existe. Sobrescrever?[/yellow]", default=False
            )
            if not overwrite:
                continue
        break
    console.print()

    # ── 2. Persona base (opcional) ────────────────────────────────────────
    console.print("[bold]2/6 — Persona base[/bold] [dim](opcional — pule para escrever do zero)[/dim]")
    persona_items = [(k, v["description"]) for k, v in PERSONAS.items()]
    persona_items.append(("custom", "Escrever system prompt do zero"))
    persona_id = _pick_numbered(persona_items, "Personas predefinidas", allow_empty=True)
    console.print()

    # ── 3. Descrição curta ────────────────────────────────────────────────
    console.print("[bold]3/6 — Descrição curta[/bold] [dim](aparece no bauer agent list)[/dim]")
    default_desc = PERSONAS[persona_id]["description"] if persona_id in PERSONAS else ""
    description = _ask("Descrição", default=default_desc or "Meu agent especializado")
    console.print()

    # ── 4. System prompt ─────────────────────────────────────────────────
    console.print("[bold]4/6 — System prompt[/bold]")
    if persona_id in PERSONAS:
        default_system = PERSONAS[persona_id]["system"]
        console.print("[dim]Persona carregada. Pressione Enter para aceitar ou edite:[/dim]")
        console.print(Panel(default_system, border_style="dim"))
        use_default = Confirm.ask("Usar este system prompt?", default=True)
        if use_default:
            system = default_system
        else:
            console.print("[dim]Digite o system prompt (Enter duas vezes para terminar):[/dim]")
            system = _collect_multiline()
    else:
        console.print("[dim]Descreva a personalidade, especialidade e comportamento do agent.[/dim]")
        console.print("[dim]Dica: comece com 'Você é um especialista em...'[/dim]")
        system = _collect_multiline()
    console.print()

    # ── 5. Modelo ─────────────────────────────────────────────────────────
    console.print("[bold]5/6 — Modelo[/bold]")
    console.print(f"[dim]Atual (config.yaml): {config_provider}/{config_model or 'padrão'}[/dim]")
    use_default_model = Confirm.ask("Usar o modelo atual do config.yaml?", default=True)
    if use_default_model:
        model = ""
        provider = ""
    else:
        console.print("[dim]Digite o nome do modelo (ex: deepseek-v4-flash-free, phi4-mini):[/dim]")
        model = _ask("Modelo", default=config_model)
        provider = _ask("Provider (deixe vazio para usar o do config.yaml)", default="")
    console.print()

    # ── 6. Tools ─────────────────────────────────────────────────────────
    console.print("[bold]6/6 — Tools habilitadas[/bold]")
    console.print("[dim]Use o número para ligar/desligar cada tool. Enter para confirmar.[/dim]")
    selected_tools = list(DEFAULT_TOOLS)
    selected_tools = _pick_multi(ALL_TOOLS, selected_tools, "Tools disponíveis")
    console.print()

    # ── Confirmação ───────────────────────────────────────────────────────
    _show_agent_summary(name, description, system, model or config_model, provider or config_provider, selected_tools)
    if not Confirm.ask("[bold]Criar este agent?[/bold]", default=True):
        console.print("[dim]Cancelado.[/dim]")
        return None

    agent = AgentDef(
        name=name,
        description=description,
        system=system,
        tools=selected_tools,
        model=model,
        provider=provider,
    )
    registry.save(agent)

    console.print(Panel(
        f"[green]✓[/green] Agent [cyan]{name}[/cyan] criado!\n\n"
        f"[dim]Para usar: [bold]bauer agent run {name}[/bold][/dim]",
        title="[bold green]Salvo[/bold green]",
        border_style="green",
    ))
    return agent


def _collect_multiline() -> str:
    """Coleta input multiline até linha em branco dupla."""
    lines: list[str] = []
    console.print("[dim](linha em branco para terminar)[/dim]")
    blank_count = 0
    while True:
        line = input()
        if line == "":
            blank_count += 1
            if blank_count >= 1 and lines:
                break
        else:
            blank_count = 0
            lines.append(line)
    return "\n".join(lines).strip()


def _show_agent_summary(
    name: str,
    description: str,
    system: str,
    model: str,
    provider: str,
    tools: list[str],
) -> None:
    preview = system[:120] + ("…" if len(system) > 120 else "")
    console.print(Panel(
        f"[dim]Nome:[/dim]        [cyan]{name}[/cyan]\n"
        f"[dim]Descrição:[/dim]   {description}\n"
        f"[dim]Modelo:[/dim]      {provider}/{model}\n"
        f"[dim]Tools:[/dim]       {', '.join(tools)}\n"
        f"[dim]System:[/dim]      {preview}",
        title="[bold]Resumo do Agent[/bold]",
        border_style="cyan",
    ))


# ─── Wizard: criar task ─────────────────────────────────────────────────────


def wizard_create_task() -> dict | None:
    """Entrevista para criar uma task. Retorna dict com campos ou None se cancelado."""

    _header("Nova Task", "Defina a tarefa passo a passo.")

    # 1. Título
    console.print("[bold]1/5 — Título da task[/bold]")
    title = _ask("Título")
    if not title:
        console.print("[dim]Cancelado.[/dim]")
        return None
    console.print()

    # 2. Descrição
    console.print("[bold]2/5 — Descrição detalhada[/bold] [dim](opcional — Enter para pular)[/dim]")
    description = _ask("Descrição", default="")
    console.print()

    # 3. Prioridade
    console.print("[bold]3/5 — Prioridade[/bold]")
    prio_items = [
        ("alta",   "Urgente — bloqueia outras tarefas"),
        ("media",  "Normal — próximo sprint"),
        ("baixa",  "Backlog — quando houver tempo"),
    ]
    priority = _pick_numbered(prio_items, "Prioridade") or "media"
    console.print()

    # 4. Spec vinculado (SDD)
    console.print("[bold]4/5 — Spec vinculado[/bold] [dim](contrato SDD — define o comportamento esperado)[/dim]")
    spec_id = ""
    try:
        from .spec_manager import SpecManager
        from .spec_wizard import wizard_auto_spec, wizard_create_spec
        mgr = SpecManager()
        specs = mgr.list_specs()

        def _create_spec_for_task(auto: bool = True) -> str:
            """Cria spec via IA (auto) ou wizard manual. Retorna spec_id ou ''."""
            if auto:
                new_spec = wizard_auto_spec(title, description, mgr)
            else:
                new_spec = wizard_create_spec(mgr)
            return new_spec.id if new_spec else ""

        if specs:
            spec_items = [(s.id, f"[{s.status}] {s.purpose.split(chr(10))[0][:60]}") for s in specs]
            spec_items.append(("auto",   "Gerar novo spec com IA (recomendado)"))
            spec_items.append(("manual", "Criar spec manualmente (wizard)"))
            spec_items.append(("nenhum", "Sem spec (não recomendado para SDD)"))
            chosen_spec = _pick_numbered(spec_items, "Specs disponíveis", allow_empty=True)

            if chosen_spec == "auto":
                spec_id = _create_spec_for_task(auto=True)
            elif chosen_spec == "manual":
                spec_id = _create_spec_for_task(auto=False)
            elif chosen_spec in (None, "nenhum"):
                console.print("[yellow]⚠ Tarefa sem spec viola o princípio SDD.[/yellow]")
                if Confirm.ask("Gerar spec com IA agora?", default=True):
                    spec_id = _create_spec_for_task(auto=True)
            else:
                spec_id = chosen_spec or ""
        else:
            # Nenhum spec existe — gera automaticamente com IA
            console.print("[yellow]Nenhum spec encontrado. Gerando automaticamente com IA...[/yellow]")
            spec_id = _create_spec_for_task(auto=True)

    except Exception:
        pass
    console.print()

    # 5. Agent responsável
    console.print("[bold]5/5 — Agent responsável[/bold] [dim](opcional — Enter para nenhum)[/dim]")
    try:
        reg = AgentRegistry()
        agents = reg.list_agents()
        if agents:
            agent_items = [(a.name, a.description) for a in agents]
            agent_items.append(("nenhum", "Sem agent específico"))
            assigned = _pick_numbered(agent_items, "Agents disponíveis", allow_empty=True)
            assigned = "" if assigned in (None, "nenhum") else assigned
        else:
            console.print("[dim]Nenhum agent criado ainda.[/dim]")
            assigned = ""
    except Exception:
        assigned = ""
    console.print()

    # Confirmação
    prio_color = {"alta": "red", "media": "yellow", "baixa": "dim"}.get(priority, "white")
    console.print(Panel(
        f"[dim]Título:[/dim]      {title}\n"
        f"[dim]Descrição:[/dim]   {description or '—'}\n"
        f"[dim]Prioridade:[/dim]  [{prio_color}]{priority}[/{prio_color}]\n"
        f"[dim]Spec:[/dim]        [magenta]{spec_id}[/magenta]" + (" ✓" if spec_id else " —") + "\n"
        f"[dim]Agent:[/dim]       {assigned or '—'}",
        title="[bold]Resumo da Task[/bold]",
        border_style="cyan",
    ))

    if not Confirm.ask("[bold]Criar esta task?[/bold]", default=True):
        console.print("[dim]Cancelado.[/dim]")
        return None

    return {
        "title": title,
        "description": description,
        "priority": priority,
        "spec_id": spec_id,
        "assigned_agent": assigned,
    }


# ─── Wizard: orchestrate ────────────────────────────────────────────────────


def wizard_orchestrate() -> dict | None:
    """Entrevista para configurar uma execução de orquestrador."""

    _header("Orquestrador", "Configure a tarefa complexa passo a passo.")

    # 1. Tarefa principal
    console.print("[bold]1/4 — Descreva a tarefa[/bold]")
    console.print("[dim]Seja específico. Ex: 'Crie uma API REST em Python com FastAPI e testes unitários'[/dim]")
    task = _collect_single_line("Tarefa")
    if not task:
        console.print("[dim]Cancelado.[/dim]")
        return None
    console.print()

    # 2. Agent especializado
    console.print("[bold]2/4 — Agent especializado[/bold] [dim](opcional)[/dim]")
    try:
        reg = AgentRegistry()
        agents = reg.list_agents()
        if agents:
            agent_items = [(a.name, a.description) for a in agents]
            agent_items.append(("padrao", "Agent padrão (sem especialização)"))
            chosen_agent = _pick_numbered(agent_items, "Agents disponíveis", allow_empty=True)
            chosen_agent = "" if chosen_agent in (None, "padrao") else chosen_agent
        else:
            console.print("[dim]Nenhum agent criado. Use: bauer agent create[/dim]")
            chosen_agent = ""
    except Exception:
        chosen_agent = ""
    console.print()

    # 3. Modo interativo
    console.print("[bold]3/4 — Modo de execução[/bold]")
    mode_items = [
        ("automatico",   "Executa todos os passos sem pausar"),
        ("interativo",   "Confirma cada onda de passos antes de executar"),
    ]
    mode = _pick_numbered(mode_items, "Modo") or "automatico"
    interactive = mode == "interativo"
    console.print()

    # 4. Resume
    console.print("[bold]4/4 — Retomar execução anterior?[/bold]")
    resume = Confirm.ask("Verificar e retomar progresso salvo?", default=False)
    console.print()

    # Confirmação
    console.print(Panel(
        f"[dim]Tarefa:[/dim]      {task}\n"
        f"[dim]Agent:[/dim]       {chosen_agent or 'padrão'}\n"
        f"[dim]Modo:[/dim]        {'interativo' if interactive else 'automático'}\n"
        f"[dim]Resume:[/dim]      {'sim' if resume else 'não'}",
        title="[bold]Resumo do Orchestrate[/bold]",
        border_style="cyan",
    ))

    if not Confirm.ask("[bold]Executar?[/bold]", default=True):
        console.print("[dim]Cancelado.[/dim]")
        return None

    return {
        "task": task,
        "agent": chosen_agent,
        "interactive": interactive,
        "resume": resume,
    }


def _collect_single_line(prompt_text: str) -> str:
    return Prompt.ask(f"[bold]{prompt_text}[/bold]").strip()
