"""Comando bauer company."""

from __future__ import annotations

from rich.panel import Panel
from pathlib import Path
import typer

from ._common import _COMPANIES_DIR, console

company_app = typer.Typer(help="Gestao multi-empresa — namespaces isolados por empresa")


@company_app.command("create")
def company_create(
    slug: str = typer.Argument(..., help="ID da empresa (ex: acme-corp)"),
    name: str = typer.Option(..., "--name", "-n", help="Nome da empresa (ex: 'Acme Corp')"),
    industry: str = typer.Option("tecnologia", "--industry", "-i", help="Setor da empresa"),
    language: str = typer.Option("pt", "--language", "-l", help="Idioma padrao (pt|en|es)"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
    activate: bool = typer.Option(True, "--activate/--no-activate", help="Ativar esta empresa apos criar"),
):
    """Cria uma nova empresa com namespace isolado em companies/<slug>/."""
    from ..company_manager import CompanyManager, CompanyManagerError

    cm = CompanyManager(companies_dir)
    try:
        company = cm.create(slug, name, industry=industry, language=language)
    except CompanyManagerError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    console.print(Panel(
        f"[bold green]Empresa criada com sucesso![/bold green]\n\n"
        f"  ID:       [cyan]{company.id}[/cyan]\n"
        f"  Nome:     {company.name}\n"
        f"  Setor:    {industry}\n"
        f"  Idioma:   {company.language}\n\n"
        f"  [dim]Diretorio: {companies_dir / slug}[/dim]\n"
        f"  [dim]Edite o contexto: {companies_dir / slug / 'company.yaml'}[/dim]",
        title="[bold]Nova Empresa[/bold]",
        border_style="green",
    ))

    if activate:
        cm.set_active(slug)
        console.print(f"[green]Empresa [cyan]{slug}[/cyan] ativada.[/green]")

    console.print(
        f"\n[dim]Adicione agents especificos: "
        f"[bold]bauer agent create --agents {companies_dir / slug / 'agents.yaml'}[/bold][/dim]"
    )


@company_app.command("list")
def company_list(
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
):
    """Lista todas as empresas cadastradas."""
    from ..company_manager import CompanyManager

    cm = CompanyManager(companies_dir)
    companies = cm.list_companies()
    active_id = cm.get_active_id()

    if not companies:
        console.print("[dim]Nenhuma empresa cadastrada.[/dim]")
        console.print(f"[dim]Crie uma: [bold]bauer company create <slug> --name 'Nome'[/bold][/dim]")
        return

    from rich.table import Table
    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("", width=2)
    table.add_column("ID", style="cyan")
    table.add_column("Nome")
    table.add_column("Idioma", justify="center", width=8)
    table.add_column("Departments", justify="right")
    table.add_column("Criada em", style="dim")

    for c in companies:
        is_active = c.id == active_id
        marker = "[bold green]▶[/bold green]" if is_active else " "
        name_style = f"[bold]{c.name}[/bold]" if is_active else c.name
        table.add_row(
            marker,
            c.id,
            name_style,
            c.language,
            str(len(c.departments)),
            c.created_at[:10] if c.created_at else "—",
        )

    console.print(table)
    if active_id:
        console.print(f"\n[dim]Empresa ativa: [cyan]{active_id}[/cyan][/dim]")
    else:
        console.print(
            f"\n[dim]Nenhuma empresa ativa. Selecione: "
            f"[bold]bauer company select <id>[/bold][/dim]"
        )


@company_app.command("select")
def company_select(
    slug: str = typer.Argument(..., help="ID da empresa a ativar"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
):
    """Define a empresa ativa para esta sessao."""
    from ..company_manager import CompanyManager, CompanyManagerError

    cm = CompanyManager(companies_dir)
    try:
        cm.set_active(slug)
    except CompanyManagerError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    company = cm.get(slug)
    console.print(
        f"[green]Empresa ativa: [bold cyan]{slug}[/bold cyan]"
        + (f" — {company.name}" if company else "")
        + "[/green]"
    )
    console.print(
        f"[dim]Todos os agents usarao o contexto de [cyan]{slug}[/cyan] "
        f"automaticamente.[/dim]"
    )


@company_app.command("info")
def company_info(
    slug: str = typer.Argument("", help="ID da empresa (padrao: empresa ativa)"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
):
    """Exibe detalhes de uma empresa."""
    from ..company_manager import CompanyManager

    cm = CompanyManager(companies_dir)

    if not slug:
        slug = cm.get_active_id() or ""
        if not slug:
            console.print("[yellow]Nenhuma empresa ativa.[/yellow]")
            console.print("[dim]Use: [bold]bauer company select <id>[/bold][/dim]")
            raise typer.Exit(code=1)

    company = cm.get(slug)
    if company is None:
        console.print(f"[red]Empresa '{slug}' nao encontrada.[/red]")
        raise typer.Exit(code=1)

    active_id = cm.get_active_id()
    is_active = company.id == active_id

    lines = [
        f"  [bold]ID:[/bold]        [cyan]{company.id}[/cyan]"
        + (" [bold green](ativa)[/bold green]" if is_active else ""),
        f"  [bold]Nome:[/bold]      {company.name}",
        f"  [bold]Idioma:[/bold]    {company.language}",
    ]
    if company.model:
        lines.append(f"  [bold]Modelo:[/bold]    {company.provider}/{company.model}")
    if company.agent_prefix:
        lines.append(f"  [bold]Prefixo:[/bold]   {company.agent_prefix}")
    if company.departments:
        lines.append(f"  [bold]Depts:[/bold]     {', '.join(company.departments)}")
    if company.tools_allowed:
        lines.append(f"  [bold]Tools:[/bold]     {', '.join(company.tools_allowed)}")
    lines.append(f"  [bold]Criada:[/bold]    {company.created_at[:10] if company.created_at else '—'}")

    if company.context.strip():
        lines.append(f"\n  [bold]Contexto injetado:[/bold]")
        for ln in company.context.strip().splitlines():
            lines.append(f"  [dim]{ln}[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold]Empresa: {company.name}[/bold]",
        border_style="cyan",
    ))

    # Mostra agents especificos desta empresa
    agents_file = companies_dir / slug / "agents.yaml"
    if agents_file.exists():
        from ..agent_registry import AgentRegistry
        reg = AgentRegistry(agents_file)
        agents = reg.list_agents()
        if agents:
            console.print(f"\n[dim]Agents especificos ({len(agents)}):[/dim]")
            for ag in agents:
                console.print(f"  [cyan]{ag.name}[/cyan] — {ag.description}")
        else:
            console.print(f"\n[dim]Sem agents especificos. "
                          f"Crie: [bold]bauer agent create --agents {agents_file}[/bold][/dim]")


@company_app.command("clear")
def company_clear():
    """Remove a selecao de empresa ativa (volta ao modo global)."""
    from ..company_manager import CompanyManager

    cm = CompanyManager(_COMPANIES_DIR)
    active = cm.get_active_id()
    if not active:
        console.print("[dim]Nenhuma empresa ativa no momento.[/dim]")
        return

    cm.clear_active()
    console.print(f"[yellow]Empresa '[cyan]{active}[/cyan]' desativada. Modo global restaurado.[/yellow]")


@company_app.command("delete")
def company_delete(
    slug: str = typer.Argument(..., help="ID da empresa a remover"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Pula confirmacao"),
):
    """Remove uma empresa e todos os seus dados. IRREVERSIVEL."""
    from ..company_manager import CompanyManager
    from rich.prompt import Confirm

    cm = CompanyManager(companies_dir)
    company = cm.get(slug)
    if company is None:
        console.print(f"[red]Empresa '{slug}' nao encontrada.[/red]")
        raise typer.Exit(code=1)

    if not yes:
        if not Confirm.ask(
            f"[bold red]Remover empresa '{slug}' ({company.name}) e TODOS os seus dados?[/bold red]",
            default=False,
        ):
            console.print("[dim]Cancelado.[/dim]")
            return

    # Desativa se for a empresa ativa
    if cm.get_active_id() == slug:
        cm.clear_active()

    cm.delete(slug)
    console.print(f"[red]Empresa '[cyan]{slug}[/cyan]' removida.[/red]")


@company_app.command("personas")
def company_personas(
    department: str = typer.Argument("", help="Filtrar por departamento (ex: tech, finance, hr)"),
):
    """Lista todas as personas disponíveis por departamento."""
    from ..agent_registry import PERSONAS
    from rich.table import Table

    # Mapeamento de grupos
    groups: dict[str, list[str]] = {
        "Tecnologia": ["python", "backend", "frontend", "devops", "sre", "security",
                       "data-engineer", "ml-engineer", "sql", "architect", "scrum-master", "docs"],
        "C-Suite": ["ceo", "cto", "cfo", "coo", "cmo", "chro"],
        "Financeiro": ["financial-analyst", "controller", "internal-auditor", "treasury"],
        "Marketing": ["brand-manager", "copywriter", "seo", "growth", "social-media"],
        "Vendas": ["sdr", "account-executive", "sales-engineer", "customer-success"],
        "RH / Pessoas": ["recruiter", "learning-dev", "people-analytics", "comp-benefits"],
        "Juridico": ["legal-contracts", "compliance", "ip-specialist"],
        "Operacoes": ["supply-chain", "project-manager", "business-analyst", "process-engineer"],
        "Suporte": ["support-agent", "qa-analyst", "knowledge-manager"],
        "Dados & Analytics": ["data-scientist", "bi-analyst", "data-architect"],
        "Produto": ["product-manager", "product-owner", "ux-researcher", "ux-designer"],
    }

    dept_filter = department.lower()

    for group_name, keys in groups.items():
        # Filtra por departamento se especificado
        if dept_filter and dept_filter not in group_name.lower():
            # Tenta match parcial nos nomes das personas
            keys_filtered = [k for k in keys if dept_filter in k]
            if not keys_filtered:
                continue
            keys = keys_filtered

        table = Table(
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            title=f"[bold]{group_name}[/bold]",
            title_justify="left",
        )
        table.add_column("Persona", style="cyan", width=22)
        table.add_column("Descricao")

        for key in keys:
            p = PERSONAS.get(key)
            if p:
                table.add_row(key, p["description"])

        console.print(table)
        console.print()

    total = len(PERSONAS)
    console.print(
        f"[dim]{total} personas disponíveis. "
        f"Use: [bold]bauer agent run <persona>[/bold] para iniciar.[/dim]"
    )
