"""Comando bauer memory."""

from __future__ import annotations

from ..memory_manager import MemoryManager
from pathlib import Path
from rich.table import Table
from ..runtime_state import read_state
import typer

from ._common import _FILE_ALIASES, _MEMORY_DIR, _RUNTIME_STATE_DEFAULT, console

memory_app = typer.Typer(help="Operacoes com memoria Markdown")


@memory_app.command("init")
def memory_init(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Cria o diretorio memory/ e inicializa os arquivos Markdown."""
    mm = MemoryManager(memory_dir)
    created = mm.init_files()
    if created:
        for p in created:
            console.print(f"[green]criado:[/green] {p}")
    else:
        console.print(f"[dim]Todos os arquivos ja existem em {memory_dir}/[/dim]")


@memory_app.command("list")
def memory_list(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Lista os arquivos de memoria com contagem de entradas."""
    mm = MemoryManager(memory_dir)
    table = Table(title=f"Memoria — {memory_dir}/")
    table.add_column("arquivo", style="cyan")
    table.add_column("linhas", justify="right")
    table.add_column("entradas", justify="right")
    for name, lines, entries in mm.list_files():
        table.add_row(name, str(lines), str(entries))
    console.print(table)


@memory_app.command("show")
def memory_show(
    file: str = typer.Argument(
        "memory",
        help="Arquivo: memory | decisions | failures | experience | prefs | lessons",
    ),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Mostra o conteudo de um arquivo de memoria."""
    mm = MemoryManager(memory_dir)
    filename = _FILE_ALIASES.get(file.lower(), file)
    content = mm.read_file(filename)
    console.print(content)


@memory_app.command("add-decision")
def memory_add_decision(
    title: str = typer.Argument(..., help="Titulo curto da decisao"),
    body: str = typer.Argument(..., help="Descricao da decisao"),
    context: str = typer.Option("", "--context", help="Contexto ou motivo"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra uma decisao tecnica em DECISIONS.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_decision(title, body, context)
    console.print(f"[green]Decisao registrada em {p}[/green]")


@memory_app.command("add-failure")
def memory_add_failure(
    title: str = typer.Argument(..., help="Titulo curto do problema"),
    error: str = typer.Argument(..., help="Descricao do erro"),
    fix: str = typer.Option("", "--fix", help="O que corrigiu o problema"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra uma tentativa falha em FAILED_ATTEMPTS.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_failure(title, error, fix)
    console.print(f"[green]Falha registrada em {p}[/green]")


@memory_app.command("add-model-exp")
def memory_add_model_exp(
    result: str = typer.Argument(..., help="Resultado: ok | slow | oom | error"),
    lesson: str = typer.Option("", "--lesson", help="Licao aprendida"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra experiencia do modelo atual em MODEL_EXPERIENCE.md.

    Le o modelo, contexto e RAM diretamente do .runtime_state.json.
    """
    state = read_state(state_file)
    if state is None:
        console.print(
            "[red]Runtime state nao encontrado.[/red]\n"
            "Rode [bold]bauer doctor[/bold] primeiro."
        )
        raise typer.Exit(code=1)

    mm = MemoryManager(memory_dir)
    p = mm.add_model_experience(
        model=state["configured_model"],
        context_tokens=state["context"]["applied"],
        result=result,
        ram_used_mb=state["ram_available_mb"],
        machine_id=state["machine_id"],
        lesson=lesson,
    )
    console.print(f"[green]Experiencia registrada em {p}[/green]")


@memory_app.command("summarize")
def memory_summarize(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Mostra resumo estruturado de todos os arquivos de memoria."""
    import re
    from ..memory_manager import MEMORY_FILES

    mm = MemoryManager(memory_dir)
    _SECTION_RE = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)

    table = Table(title="Resumo da Memoria — memory/")
    table.add_column("arquivo", style="cyan")
    table.add_column("entradas", justify="right")
    table.add_column("ultima entrada", style="dim")

    for key, filename in MEMORY_FILES.items():
        p = memory_dir / filename
        if not p.exists():
            table.add_row(filename, "0", "—")
            continue
        content = p.read_text(encoding="utf-8", errors="replace")
        matches = list(_SECTION_RE.finditer(content))
        count = len(matches)
        last_ts = matches[-1].group(1) if matches else "—"
        table.add_row(filename, str(count), last_ts)

    console.print(table)
    console.print(
        "\n[dim]Use 'bauer memory show <arquivo>' para ver o conteudo completo.[/dim]"
    )


@memory_app.command("add-note")
def memory_add_note(
    title: str = typer.Argument(..., help="Titulo da nota"),
    body: str = typer.Argument(..., help="Conteudo da nota"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Adiciona uma nota geral em MEMORY.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_note(title, body)
    console.print(f"[green]Nota registrada em {p}[/green]")


@memory_app.command("add-lesson")
def memory_add_lesson(
    decision: str = typer.Argument(..., help="Decisao automatica tomada"),
    reason: str = typer.Argument(..., help="Motivo da decisao"),
    undo: str = typer.Option("", "--undo", help="Como desfazer"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra uma decisao automatica do runtime em RUNTIME_LESSONS.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_runtime_lesson(decision, reason, undo)
    console.print(f"[green]Licao registrada em {p}[/green]")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Texto a buscar na memoria"),
    top_k: int = typer.Option(5, "--top", "-n", help="Numero de resultados"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    fts: bool = typer.Option(False, "--fts", help="Usa indice SQLite FTS persistente"),
):
    """Busca semantica (TF-IDF) nos arquivos de memoria."""
    from rich.table import Table as RichTable

    if fts:
        from ..memory_index import MemoryIndex

        index = MemoryIndex(memory_dir)
        if not index.db_path.exists():
            index.rebuild()
        hits = index.search(query, limit=top_k)
        results = [
            {"file": hit.file, "title": hit.title, "score": hit.score, "snippet": hit.snippet}
            for hit in hits
        ]
    else:
        mm = MemoryManager(memory_dir)
        results = mm.search(query, top_k=top_k)

    if not results:
        console.print(f"[yellow]Nenhum resultado para '{query}' em {memory_dir}/[/yellow]")
        raise typer.Exit()

    table = RichTable(title=f"Busca: '{query}' — {len(results)} resultado(s)", show_lines=True)
    table.add_column("Arquivo", style="cyan", no_wrap=True)
    table.add_column("Titulo", style="bold")
    table.add_column("Score", style="dim", width=7)
    table.add_column("Trecho", style="dim")

    for r in results:
        table.add_row(
            r["file"],
            r["title"][:60],
            str(r["score"]),
            r["snippet"][:120] + ("…" if len(r["snippet"]) > 120 else ""),
        )

    console.print(table)


@memory_app.command("index")
def memory_index_cmd(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Reconstrói o indice SQLite FTS dos arquivos Markdown de memoria."""
    from ..memory_index import MemoryIndex

    count = MemoryIndex(memory_dir).rebuild()
    console.print(f"[green]Indice de memoria atualizado:[/green] {count} bloco(s)")


@memory_app.command("skills-pending")
def memory_skills_pending_cmd(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Lista sugestões de skills pendentes de aprovação manual."""
    from ..skill_registry import SkillRegistry

    suggestions = SkillRegistry(memory_dir).pending_suggestions()
    if not suggestions:
        console.print("[dim]Nenhuma sugestao de skill pendente.[/dim]")
        return
    table = Table(title="Skills pendentes", show_lines=False)
    table.add_column("Skill", style="cyan")
    table.add_column("Ocorrencias", justify="right")
    table.add_column("Status")
    for suggestion in suggestions:
        table.add_row(
            suggestion.get("name", ""),
            suggestion.get("ocorrencias", ""),
            suggestion.get("status", ""),
        )
    console.print(table)


@memory_app.command("skill-approve")
def memory_skill_approve_cmd(
    name: str = typer.Argument(..., help="Nome da skill sugerida"),
    workspace: Path = typer.Option(Path("workspace"), "--workspace"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    description: str = typer.Option("", "--description"),
    content: str = typer.Option("", "--content"),
):
    """Promove uma sugestão pendente para workspace/.bauer_skills.json."""
    from ..skill_registry import SkillRegistry

    try:
        path = SkillRegistry(memory_dir).approve_suggestion(
            name,
            workspace=workspace,
            description=description,
            content=content,
        )
    except Exception as exc:
        console.print(f"[red]Erro aprovando skill:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]Skill aprovada em[/green] {path}")


@memory_app.command("cleanup")
def memory_cleanup(
    days: int = typer.Option(90, "--days", "-d", help="Remover entradas mais antigas que N dias"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simula sem modificar arquivos"),
):
    """Remove entradas de memória mais antigas que N dias (padrão: 90).

    Exemplos:
      bauer memory cleanup              # remove entradas >90 dias
      bauer memory cleanup --days 30    # remove entradas >30 dias
      bauer memory cleanup --dry-run    # conta sem apagar
    """
    from rich.table import Table as RichTable

    mm = MemoryManager(memory_dir)
    removed = mm.cleanup_old_entries(max_age_days=days, dry_run=dry_run)

    total = sum(removed.values())
    if total == 0:
        console.print(f"[green]Nenhuma entrada com mais de {days} dias encontrada.[/green]")
        return

    table = RichTable(
        title=f"{'[dim]Simulação[/dim] — ' if dry_run else ''}Entradas removidas (>{days} dias)",
        show_lines=False,
        box=None,
    )
    table.add_column("Arquivo", style="cyan")
    table.add_column("Removidas", style="yellow", justify="right")
    for fname, n in removed.items():
        if n > 0:
            table.add_row(fname, str(n))

    console.print(table)
    action = "seriam removidas" if dry_run else "removidas"
    console.print(f"[bold]{total}[/bold] entradas {action} no total.")
    if dry_run:
        console.print("[dim]Rode sem --dry-run para aplicar.[/dim]")
