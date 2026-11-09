"""Comando bauer memory.

Os subcomandos legados continuam separados para compatibilidade. O grupo
``unified`` usa a fachada única e sincroniza novas gravações entre runtime e
Markdown:
- `bauer memory md ...`      — Markdown (memory/*.md), a memoria automatica
  que o agente le/escreve via MemoryProvider a cada turno.
- `bauer memory runtime ...` — JSONL com escopo (user/company/project/agent/
  skill) e validade, uso manual/auditavel; sem consumidor automatico no loop
  do agente hoje.
"""

from __future__ import annotations

from ..core.runtime.memory import RuntimeMemoryManager
from ..memory_facade import UnifiedMemory
from ..memory_manager import MemoryManager
from pathlib import Path
from rich.table import Table
from ..runtime_state import read_state
import typer

from ._common import _FILE_ALIASES, _MEMORY_DIR, _RUNTIME_STATE_DEFAULT, console

memory_app = typer.Typer(
    help="Memoria do Bauer — 'md', 'runtime' e 'unified' (fachada sincronizada)."
)
md_app = typer.Typer(
    help="Memoria Markdown (memory/*.md) — a memoria automatica do agente "
         "(FAILED_ATTEMPTS.md, DECISIONS.md, MODEL_EXPERIENCE.md, etc.), "
         "lida/escrita via MemoryProvider a cada turno."
)
runtime_app = typer.Typer(
    help="Memoria auditavel do runtime (JSONL, escopo user/company/project/"
         "agent/skill, com validade e revisao) — uso manual, sem consumidor "
         "automatico no loop do agente hoje."
)
unified_app = typer.Typer(
    help="Memoria unificada — grava no runtime e projeta no Markdown."
)
memory_app.add_typer(md_app, name="md")
memory_app.add_typer(runtime_app, name="runtime")
memory_app.add_typer(unified_app, name="unified")

_RUNTIME_MEMORY_DIR = _MEMORY_DIR / "runtime"


@unified_app.command("add")
def unified_memory_add(
    scope: str = typer.Argument(..., help="Escopo: user | company | project | agent | skill"),
    content: str = typer.Argument(..., help="Conteudo da memoria"),
    source: str = typer.Option(..., "--source", "-s", help="Origem da memoria"),
    title: str = typer.Option("", "--title", help="Titulo da projecao Markdown"),
    confidence: float = typer.Option(1.0, "--confidence", "-c"),
    valid_until: str | None = typer.Option(None, "--valid-until"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra memoria nos dois backends pelo contrato unificado."""
    try:
        record = UnifiedMemory(memory_dir=memory_dir).remember(
            scope=scope,
            content=content,
            source=source,
            title=title or None,
            confidence=confidence,
            valid_until=valid_until,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]memoria unificada registrada:[/green] {record.id}")


@unified_app.command("search")
def unified_memory_search(
    query: str = typer.Argument(..., help="Texto a buscar"),
    top_k: int = typer.Option(10, "--top", "-n"),
    scope: str | None = typer.Option(None, "--scope"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Busca simultaneamente na memoria runtime e no Markdown."""
    results = UnifiedMemory(memory_dir=memory_dir).search(
        query, top_k=top_k, scope=scope
    )
    if not results:
        console.print(f"[yellow]Nenhum resultado para '{query}'.[/yellow]")
        raise typer.Exit()
    table = Table(title=f"Memoria unificada: {query}", show_lines=True)
    table.add_column("Origem", style="cyan")
    table.add_column("Titulo")
    table.add_column("Score", justify="right")
    table.add_column("Trecho")
    for result in results:
        table.add_row(
            str(result.get("source", "")),
            str(result.get("title", ""))[:60],
            f"{float(result.get('score', 0.0)):.4f}",
            str(result.get("snippet", ""))[:160],
        )
    console.print(table)


@md_app.command("init")
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


@md_app.command("list")
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


@md_app.command("show")
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


@md_app.command("add-decision")
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


@md_app.command("add-failure")
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


@md_app.command("add-model-exp")
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


@md_app.command("summarize")
def memory_summarize(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Mostra resumo estruturado de todos os arquivos de memoria."""
    import re
    from ..memory_manager import MEMORY_FILES

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
        "\n[dim]Use 'bauer memory md show <arquivo>' para ver o conteudo completo.[/dim]"
    )


@md_app.command("add-note")
def memory_add_note(
    title: str = typer.Argument(..., help="Titulo da nota"),
    body: str = typer.Argument(..., help="Conteudo da nota"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Adiciona uma nota geral em MEMORY.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_note(title, body)
    console.print(f"[green]Nota registrada em {p}[/green]")


@md_app.command("add-lesson")
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


@md_app.command("search")
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


@runtime_app.command("add")
def runtime_memory_add(
    scope: str = typer.Argument(..., help="Escopo: user | company | project | agent | skill"),
    content: str = typer.Argument(..., help="Conteudo da memoria"),
    source: str = typer.Option(..., "--source", "-s", help="Origem da memoria"),
    confidence: float = typer.Option(1.0, "--confidence", "-c", help="Confianca entre 0 e 1"),
    valid_until: str | None = typer.Option(None, "--valid-until", help="ISO datetime de validade"),
    runtime_root: Path = typer.Option(_RUNTIME_MEMORY_DIR, "--runtime-root"),
):
    """Registra memoria auditavel do runtime."""
    manager = RuntimeMemoryManager(root=runtime_root)
    try:
        record = manager.write(
            scope=scope,
            content=content,
            source=source,
            confidence=confidence,
            valid_until=valid_until,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]memoria registrada:[/green] {record.id}")


@runtime_app.command("list")
def runtime_memory_list(
    scope: str | None = typer.Option(None, "--scope", help="Filtra por escopo"),
    include_expired: bool = typer.Option(False, "--include-expired", help="Inclui memorias expiradas"),
    runtime_root: Path = typer.Option(_RUNTIME_MEMORY_DIR, "--runtime-root"),
):
    """Lista memorias auditaveis do runtime."""
    manager = RuntimeMemoryManager(root=runtime_root)
    try:
        records = manager.list(scope=scope, include_expired=include_expired)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    _print_runtime_memory_table(records, title="Memoria runtime")


@runtime_app.command("search")
def runtime_memory_search(
    query: str = typer.Argument(..., help="Texto a buscar"),
    scope: str | None = typer.Option(None, "--scope", help="Filtra por escopo"),
    include_expired: bool = typer.Option(False, "--include-expired", help="Inclui memorias expiradas"),
    runtime_root: Path = typer.Option(_RUNTIME_MEMORY_DIR, "--runtime-root"),
):
    """Busca memoria auditavel do runtime."""
    manager = RuntimeMemoryManager(root=runtime_root)
    try:
        records = manager.search(query, scope=scope, include_expired=include_expired)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    _print_runtime_memory_table(records, title=f"Busca runtime: {query}")


@runtime_app.command("revise")
def runtime_memory_revise(
    memory_id: str = typer.Argument(..., help="ID da memoria"),
    content: str | None = typer.Option(None, "--content", help="Novo conteudo"),
    source: str | None = typer.Option(None, "--source", "-s", help="Nova origem"),
    confidence: float | None = typer.Option(None, "--confidence", "-c", help="Nova confianca"),
    valid_until: str | None = typer.Option(None, "--valid-until", help="Nova validade ISO"),
    runtime_root: Path = typer.Option(_RUNTIME_MEMORY_DIR, "--runtime-root"),
):
    """Revisa uma memoria auditavel."""
    manager = RuntimeMemoryManager(root=runtime_root)
    try:
        record = manager.revise(
            memory_id,
            content=content,
            source=source,
            confidence=confidence,
            valid_until=valid_until,
        )
    except (KeyError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]memoria revisada:[/green] {record.id}")


@runtime_app.command("expire")
def runtime_memory_expire(
    memory_id: str = typer.Argument(..., help="ID da memoria"),
    reason: str = typer.Option("manual", "--reason", help="Motivo da expiracao"),
    runtime_root: Path = typer.Option(_RUNTIME_MEMORY_DIR, "--runtime-root"),
):
    """Expira uma memoria auditavel."""
    manager = RuntimeMemoryManager(root=runtime_root)
    try:
        record = manager.expire(memory_id, reason=reason)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]memoria expirada:[/green] {record.id}")


def _print_runtime_memory_table(records, *, title: str) -> None:
    table = Table(title=title, show_lines=True)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("scope")
    table.add_column("confidence", justify="right")
    table.add_column("valid_until", style="dim")
    table.add_column("source")
    table.add_column("content")
    for record in records:
        table.add_row(
            record.id,
            record.scope,
            f"{record.confidence:.2f}",
            record.valid_until or "",
            record.source,
            record.content[:100],
        )
    console.print(table)


@md_app.command("index")
def memory_index_cmd(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Reconstrói o indice SQLite FTS dos arquivos Markdown de memoria."""
    from ..memory_index import MemoryIndex

    count = MemoryIndex(memory_dir).rebuild()
    console.print(f"[green]Indice de memoria atualizado:[/green] {count} bloco(s)")


@md_app.command("skills-pending")
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


@md_app.command("skill-approve")
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


@md_app.command("cleanup")
def memory_cleanup(
    days: int = typer.Option(90, "--days", "-d", help="Remover entradas mais antigas que N dias"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simula sem modificar arquivos"),
):
    """Remove entradas de memória mais antigas que N dias (padrão: 90).

    Exemplos:
      bauer memory md cleanup              # remove entradas >90 dias
      bauer memory md cleanup --days 30    # remove entradas >30 dias
      bauer memory md cleanup --dry-run    # conta sem apagar
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


# ── Feedback estruturado (plano 065, sprint 65) ──────────────────────────────

feedback_app = typer.Typer(
    help="Feedback estruturado (/thumbsup, /thumbsdown) do workspace — "
         "redigido e com escopo de workspace."
)
memory_app.add_typer(feedback_app, name="feedback")


def _workspace_or_cwd(workspace: Path | None) -> Path:
    return workspace if workspace is not None else Path.cwd()


@feedback_app.command("list")
def feedback_list_cmd(
    workspace: Path | None = typer.Option(None, "--workspace", help="Workspace (padrão: pasta atual)"),
    rating: str | None = typer.Option(None, "--rating", help="positive | negative"),
    limit: int = typer.Option(50, "--limit"),
):
    """Lista o feedback gravado neste workspace (trechos já redigidos)."""
    from ..memory_feedback import FeedbackStore

    store = FeedbackStore.for_workspace(_workspace_or_cwd(workspace))
    if store is None:
        console.print("[red]Workspace inválido.[/red]")
        raise typer.Exit(code=1)
    try:
        records = store.list(rating=rating, limit=limit)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    if not records:
        console.print("[dim]Nenhum feedback registrado neste workspace.[/dim]")
        return
    table = Table(title=f"Feedback — {store.scope.workspace_id}")
    table.add_column("id", style="cyan")
    table.add_column("rating")
    table.add_column("sessão")
    table.add_column("turno", justify="right")
    table.add_column("tools")
    table.add_column("motivo/pergunta")
    for rec in records:
        table.add_row(
            rec.feedback_id, rec.rating, rec.session_id or "-", str(rec.turn_index),
            ", ".join(rec.tool_names)[:40], (rec.reason or rec.user_excerpt)[:80],
        )
    console.print(table)


# ── Consolidação episódica→semântica (plano 065, sprint 66) ──────────────────

consolidate_app = typer.Typer(
    help="Consolida sessões deste workspace em CONSOLIDATED.md (opt-in por "
         "workspace; escopo só workspace; segredos redigidos)."
)
memory_app.add_typer(consolidate_app, name="consolidate")

_DEFAULT_SESSIONS_DIR = Path("memory/sessions")


def _scope_or_exit(workspace: Path | None):
    from ..memory_scope import resolve_workspace_scope

    scope = resolve_workspace_scope(_workspace_or_cwd(workspace))
    if scope is None or not scope.root.is_dir():
        console.print("[red]Workspace inválido ou inexistente.[/red]")
        raise typer.Exit(code=1)
    return scope


def _print_consolidation_report(report, *, as_json: bool) -> None:
    import json as _json

    if as_json:
        console.print_json(_json.dumps(report.to_dict(), ensure_ascii=False, default=str))
        return
    mode = "[yellow]dry-run[/yellow]" if report.dry_run else f"run {report.run_id}"
    console.print(
        f"[bold]memory consolidate[/bold] {mode} status={report.status} "
        f"workspace={report.workspace_id}"
    )
    prov = report.provenance or {}
    console.print(
        f"[dim]sessões com proveniência deste workspace: {prov.get('eligible', 0)} · "
        f"sem proveniência (nunca consolidadas): {prov.get('no_provenance', 0)} · "
        f"em conflito: {prov.get('conflict', 0)}[/dim]"
    )
    if report.selected:
        table = Table(title="Sessões selecionadas")
        table.add_column("sessão", style="cyan")
        table.add_column("mensagens", justify="right")
        table.add_column("tokens (est.)", justify="right")
        table.add_column("msgs descartadas (sensíveis)", justify="right")
        for plan in report.selected:
            table.add_row(plan.session_id, str(plan.message_count),
                          str(plan.estimated_tokens), str(plan.dropped_messages))
        console.print(table)
    if report.skipped:
        console.print("[dim]puladas: " + ", ".join(
            f"{k}={v}" for k, v in sorted(report.skipped.items())) + "[/dim]")
    for sid, error in report.failed.items():
        console.print(f"[red]falhou[/red] {sid}: {error} (re-tenta na próxima execução)")
    if report.records:
        table = Table(title="Registros gravados em CONSOLIDATED.md (revise)")
        table.add_column("record_id", style="cyan")
        table.add_column("tipo")
        table.add_column("sessão")
        table.add_column("conf.", justify="right")
        table.add_column("válido até")
        table.add_column("texto")
        for rec in report.records:
            table.add_row(rec.record_id, rec.kind, rec.session_id,
                          f"{rec.confidence:.2f}", rec.valid_until, rec.text[:80])
        console.print(table)
        console.print(
            "[dim]Edite/remova em memory/CONSOLIDATED.md "
            "(bauer memory md show consolidated --dir <workspace>/memory).[/dim]"
        )
    if not report.dry_run:
        console.print(f"[dim]tokens usados (est.): {report.tokens_used}"
                      f"{' · budget esgotado' if report.budget_exhausted else ''}[/dim]")


@consolidate_app.callback(invoke_without_command=True)
def consolidate_cmd(
    ctx: typer.Context,
    workspace: Path | None = typer.Option(None, "--workspace", help="Workspace (padrão: pasta atual)"),
    sessions_dir: Path = typer.Option(_DEFAULT_SESSIONS_DIR, "--sessions-dir",
                                      help="Diretório de sessões (o mesmo do bauer agent)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Só seleciona e estima; sem LLM nem escrita"),
    max_sessions: int | None = typer.Option(None, "--max-sessions", min=0),
    max_tokens: int | None = typer.Option(None, "--max-tokens", min=0, help="Budget de tokens desta execução"),
    min_age_hours: float | None = typer.Option(None, "--min-age-hours", min=0),
    as_json: bool = typer.Option(False, "--json"),
):
    """Consolida sessões encerradas deste workspace (exige opt-in, exceto --dry-run)."""
    if ctx.invoked_subcommand is not None:
        return
    from ..memory_consolidation import ConsolidationError, MemoryConsolidator

    scope = _scope_or_exit(workspace)
    try:
        report = MemoryConsolidator(scope.path, sessions_dir).run(
            dry_run=dry_run, max_sessions=max_sessions, max_tokens=max_tokens,
            min_age_hours=min_age_hours,
        )
    except ConsolidationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    _print_consolidation_report(report, as_json=as_json)
    if report.status == "failed":
        raise typer.Exit(code=1)


@consolidate_app.command("enable")
def consolidate_enable_cmd(
    workspace: Path | None = typer.Option(None, "--workspace"),
    daily_token_budget: int | None = typer.Option(None, "--daily-token-budget", min=0),
    max_sessions: int | None = typer.Option(None, "--max-sessions", min=0),
    min_age_hours: float | None = typer.Option(None, "--min-age-hours", min=0),
):
    """Opt-in deste workspace (desligado por padrão)."""
    from ..memory_consolidation import ConsolidationSettings

    scope = _scope_or_exit(workspace)
    settings = ConsolidationSettings.load(scope)
    settings.enabled = True
    if daily_token_budget is not None:
        settings.daily_token_budget = daily_token_budget
    if max_sessions is not None:
        settings.max_sessions_per_run = max_sessions
    if min_age_hours is not None:
        settings.min_session_age_hours = min_age_hours
    settings.save(scope)
    console.print(f"[green]consolidação ligada[/green] em {scope.workspace_id} ({scope.path})")


@consolidate_app.command("disable")
def consolidate_disable_cmd(workspace: Path | None = typer.Option(None, "--workspace")):
    """Desliga a consolidação deste workspace (manual e agendada)."""
    from ..memory_consolidation import ConsolidationSettings

    scope = _scope_or_exit(workspace)
    settings = ConsolidationSettings.load(scope)
    settings.enabled = False
    settings.save(scope)
    console.print(f"[yellow]consolidação desligada[/yellow] em {scope.workspace_id}")


@consolidate_app.command("status")
def consolidate_status_cmd(
    workspace: Path | None = typer.Option(None, "--workspace"),
    limit: int = typer.Option(10, "--limit"),
):
    """Opt-in, budget e últimas execuções (métricas do ledger)."""
    from ..memory_consolidation import ConsolidationLedger, ConsolidationSettings

    scope = _scope_or_exit(workspace)
    settings = ConsolidationSettings.load(scope)
    ledger = ConsolidationLedger(scope)
    console.print(
        f"workspace={scope.workspace_id} enabled={settings.enabled} "
        f"daily_token_budget={settings.daily_token_budget} "
        f"max_sessions_per_run={settings.max_sessions_per_run} "
        f"min_session_age_hours={settings.min_session_age_hours}"
    )
    entries = ledger.entries()
    done = sum(1 for e in entries.values() if e["status"] == "consolidated")
    failed = sum(1 for e in entries.values() if e["status"] == "failed")
    console.print(f"sessões consolidadas={done} com falha={failed}")
    runs = ledger.recent_runs(limit=limit)
    if runs:
        table = Table(title="Execuções recentes")
        for col in ("run_id", "trigger", "status", "started_at", "sessões ok", "falhas", "registros", "tokens"):
            table.add_column(col)
        for run in runs:
            table.add_row(run["run_id"], run["trigger"], run["status"], run["started_at"][:19],
                          str(run["sessions_ok"]), str(run["sessions_failed"]),
                          str(run["records_written"]), str(run["tokens_used"]))
        console.print(table)


@consolidate_app.command("schedule")
def consolidate_schedule_cmd(
    workspace: Path | None = typer.Option(None, "--workspace"),
    sessions_dir: Path = typer.Option(_DEFAULT_SESSIONS_DIR, "--sessions-dir"),
    every: str = typer.Option("daily 03:00", "--schedule", "-s",
                              help="every 6h | daily 03:00 | cron: 0 3 * * *"),
    name: str = typer.Option("memory-consolidation", "--name"),
):
    """Agenda a consolidação no cron durável do workspace (roda sob o Kernel).

    Execute com `bauer cron daemon --workspace <workspace>` (ou `cron tick`).
    O job só consolida com o opt-in ligado e respeita o budget diário.
    """
    from ..memory_consolidation import ConsolidationSettings
    from ..memory_consolidation_schedule import schedule_consolidation

    scope = _scope_or_exit(workspace)
    try:
        job = schedule_consolidation(scope.path, sessions_dir=sessions_dir, schedule=every, name=name)
    except Exception as exc:  # noqa: BLE001 — erro de schedule/nome duplicado vira mensagem
        console.print(f"[red]Erro agendando consolidação:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"[green]consolidação agendada[/green] {job.name} schedule={job.schedule_str} "
        f"next={job.next_run_at}"
    )
    if not ConsolidationSettings.load(scope).enabled:
        console.print("[yellow]Opt-in desligado: o job vai pular até "
                      "'bauer memory consolidate enable'.[/yellow]")
    console.print(f"[dim]Rode: bauer cron daemon --workspace {scope.path}[/dim]")
