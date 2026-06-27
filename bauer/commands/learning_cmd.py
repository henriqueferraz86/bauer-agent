"""Comando bauer learning."""

from __future__ import annotations

from pathlib import Path
from rich.table import Table
from ..runtime_state import read_state
import typer

from ._common import _MEMORY_DIR, _RUNTIME_STATE_DEFAULT, console

learning_app = typer.Typer(help="Adaptive Learning Engine — recomendacoes e reset")


@learning_app.command("show")
def learning_show(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
):
    """Mostra resumo do aprendizado acumulado (experiencias e falhas)."""
    from ..learning_engine import LearningEngine

    engine = LearningEngine(memory_dir)
    summary = engine.summary()

    table = Table(title="Adaptive Learning — resumo")
    table.add_column("fonte", style="cyan")
    table.add_column("entradas", justify="right")
    _LABELS = {
        "model_experiences": "MODEL_EXPERIENCE.md",
        "failed_attempts": "FAILED_ATTEMPTS.md",
    }
    for key, count in summary.items():
        table.add_row(_LABELS.get(key, key), str(count))
    console.print(table)

    state = read_state(state_file)
    machine_id = state.get("machine_id", "") if state else ""
    if machine_id:
        console.print(f"[dim]Machine: {machine_id}[/dim]")
    console.print("[dim]Use 'bauer learning explain' para ver recomendacoes.[/dim]")


@learning_app.command("explain")
def learning_explain(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
):
    """Mostra recomendacoes com motivo e evidencia explicita."""
    from ..learning_engine import LearningEngine

    engine = LearningEngine(memory_dir)
    state = read_state(state_file)
    machine_id = state.get("machine_id", "") if state else ""

    recs = engine.recommend(machine_id=machine_id)

    _SEVERITY_COLOR = {"info": "dim", "suggestion": "cyan", "warning": "yellow"}
    for i, rec in enumerate(recs, 1):
        color = _SEVERITY_COLOR.get(rec.severity, "white")
        console.print(
            f"\n[bold]{i}.[/bold] [{color}][{rec.severity.upper()}][/{color}] {rec.action}"
        )
        console.print(f"   [dim]Motivo:[/dim] {rec.reason}")
        if rec.evidence:
            console.print("   [dim]Evidencia:[/dim]")
            for ev in rec.evidence:
                console.print(f"     - {ev}")

    console.print(
        "\n[dim]Nenhuma config foi alterada. "
        "Use 'bauer learning reset' para limpar o aprendizado.[/dim]"
    )


@learning_app.command("export")
def learning_export(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    output_dir: Path = typer.Option(Path("datasets"), "--output", help="Diretorio de saida"),
):
    """Exporta aprendizado como datasets JSONL para preparacao de fine-tuning (Fase 8).

    Gera:
      datasets/model_experience.jsonl  — historico de modelos
      datasets/failed_attempts.jsonl   — erros e correcoes
    """
    import json
    from ..learning_engine import LearningEngine

    output_dir.mkdir(parents=True, exist_ok=True)
    engine = LearningEngine(memory_dir)

    # Exporta MODEL_EXPERIENCE
    exps = engine.load_experience()
    exp_path = output_dir / "model_experience.jsonl"
    with exp_path.open("w", encoding="utf-8") as f:
        for e in exps:
            record = {
                "timestamp": e.timestamp,
                "model": e.title.split(" — ")[0].strip() if " — " in e.title else e.title,
                "context_tokens": e.context_tokens,
                "result": e.result,
                "ram_used_mb": e.ram_used_mb,
                "machine_id": e.machine_id,
                "lesson": e.lesson,
                "input": f"Modelo {e.title} com contexto {e.context_tokens} tokens.",
                "output": f"Resultado: {e.result}. {e.lesson}".strip(". ") + ".",
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    console.print(f"[green]exportado:[/green] {exp_path}  ({len(exps)} registros)")

    # Exporta FAILED_ATTEMPTS
    failures = engine.load_failures()
    fail_path = output_dir / "failed_attempts.jsonl"
    with fail_path.open("w", encoding="utf-8") as f:
        for fa in failures:
            record = {
                "timestamp": fa.timestamp,
                "title": fa.title,
                "error": fa.error,
                "fix": fa.fix,
                "machine_id": fa.machine_id,
                "input": f"Erro: {fa.error}",
                "output": fa.fix if fa.fix else "Sem correcao registrada.",
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    console.print(f"[green]exportado:[/green] {fail_path}  ({len(failures)} registros)")
    console.print(f"\n[dim]Datasets em {output_dir}/ — prontos para fine-tuning LoRA/QLoRA.[/dim]")


@learning_app.command("forget-model")
def learning_forget_model(
    model_name: str = typer.Argument(..., help="Nome exato do modelo (ex: qwen2.5-coder:7b)"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    confirm: bool = typer.Option(False, "--confirm", help="Pular confirmacao interativa"),
):
    """Remove todas as entradas de um modelo dos arquivos de aprendizado.

    Cria backup .bak antes de modificar. Nenhum dado e deletado permanentemente.
    """
    from ..learning_engine import LearningEngine

    if not confirm:
        typer.confirm(
            f"Remover todas as entradas de '{model_name}' de MODEL_EXPERIENCE.md e FAILED_ATTEMPTS.md?",
            abort=True,
        )

    engine = LearningEngine(memory_dir)
    results = engine.forget_model(model_name)

    total = sum(results.values())
    if total == 0:
        console.print(f"[dim]Nenhuma entrada encontrada para '{model_name}'.[/dim]")
        return

    for filename, count in results.items():
        if count > 0:
            bak = (memory_dir / filename).with_suffix(".md.bak")
            console.print(
                f"[green]removido:[/green] {count} entrada(s) de {filename}  "
                f"[dim](backup: {bak.name})[/dim]"
            )


@learning_app.command("reset")
def learning_reset(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    confirm: bool = typer.Option(False, "--confirm", help="Pular confirmacao interativa"),
):
    """Limpa os arquivos de aprendizado (cria backup .bak antes).

    Arquivos afetados: FAILED_ATTEMPTS.md, MODEL_EXPERIENCE.md, RUNTIME_LESSONS.md.
    O cabecalho de cada arquivo e preservado. Nenhum dado e deletado permanentemente.
    """
    from ..learning_engine import LearningEngine

    if not confirm:
        typer.confirm(
            "Limpar FAILED_ATTEMPTS.md, MODEL_EXPERIENCE.md e RUNTIME_LESSONS.md? "
            "(backups .bak serao criados antes)",
            abort=True,
        )

    engine = LearningEngine(memory_dir)
    reset_paths = engine.reset()

    if reset_paths:
        for p in reset_paths:
            bak = p.with_suffix(".md.bak")
            console.print(f"[green]resetado:[/green] {p.name}  [dim](backup: {bak.name})[/dim]")
    else:
        console.print("[dim]Nenhum arquivo de aprendizado encontrado.[/dim]")


@learning_app.command("clean")
def learning_clean(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretório de memória"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra o que seria removido sem alterar"),
):
    """Remove entradas de ruído do MODEL_EXPERIENCE.md (test-model, CI, duplicatas).

    Cria backup .bak antes de modificar. Nunca apaga o arquivo — apenas limpa entradas
    de teste que poluem a análise de aprendizado.
    """
    import re as _re

    _NOISE_PATTERNS = ["test-model", "test_model", "github-actions", "ci-runner", "runner-"]
    p = memory_dir / "MODEL_EXPERIENCE.md"
    if not p.exists():
        console.print("[yellow]MODEL_EXPERIENCE.md não encontrado.[/yellow]")
        return

    content = p.read_text(encoding="utf-8")
    parts = content.split("\n---\n", 1)
    header = parts[0] + "\n---\n"
    body = parts[1] if len(parts) > 1 else ""

    section_pat = _re.compile(r"(?=\n## \[)")
    sections = section_pat.split(body)

    kept, removed = [], 0
    for sec in sections:
        is_noise = any(pat.lower() in sec.lower() for pat in _NOISE_PATTERNS)
        if is_noise:
            removed += 1
        else:
            kept.append(sec)

    if removed == 0:
        console.print("[green]Nenhuma entrada de ruído encontrada.[/green]")
        return

    if dry_run:
        console.print(
            f"[yellow]dry-run:[/yellow] {removed} entrada(s) seriam removidas "
            f"({len(kept)} mantidas). Use sem --dry-run para aplicar."
        )
        return

    bak = p.with_suffix(".md.bak")
    bak.write_text(content, encoding="utf-8")
    p.write_text(header + "".join(kept), encoding="utf-8")
    console.print(
        f"[green]Limpeza concluída:[/green] {removed} entrada(s) removidas, "
        f"{len(kept)} mantidas.  [dim](backup: {bak.name})[/dim]"
    )


@learning_app.command("learn")
def learning_learn(
    min_occ: int = typer.Option(3, "--min", "-n", help="Mínimo de ocorrências para virar candidato"),
    show_yaml: bool = typer.Option(False, "--yaml", "-y", help="Exibe o YAML draft de cada candidato"),
    max_sessions: int = typer.Option(200, "--max-sessions", help="Máximo de sessões a analisar"),
):
    """Detecta pedidos repetidos no histórico e sugere skills automáticas.

    Varre as sessões salvas, agrupa pedidos similares por TF-IDF e lista
    os workflows candidatos a virar skill (>= --min ocorrências em >= 2 sessões).

    Use --yaml para ver o YAML pronto para instalar via 'bauer skills install'.
    """
    from ..skill_learning import find_skill_candidates, draft_skill_yaml

    console.print("[bold cyan]Analisando histórico de sessões...[/bold cyan]")
    candidates = find_skill_candidates(min_occurrences=min_occ, max_sessions=max_sessions)

    if not candidates:
        console.print(
            f"[yellow]Nenhum candidato encontrado[/yellow] (mínimo: {min_occ} ocorrências em ≥2 sessões).\n"
            "[dim]Continue usando o Bauer para acumular histórico.[/dim]"
        )
        return

    console.print(f"[green]{len(candidates)} candidato(s) encontrado(s):[/green]\n")
    for c in candidates:
        console.print(f"  [bold]{c.slug}[/bold]  ({c.occurrences}x em {len(c.sessions)} sessões)")
        console.print(f"  [dim]Exemplo: {c.representative[:100]}[/dim]")
        if show_yaml:
            console.print(f"\n[dim]{draft_skill_yaml(c)}[/dim]")
        console.print()

    console.print(
        "[dim]Use 'bauer learning learn --yaml' para ver o YAML de cada candidato.[/dim]"
    )


@learning_app.command("stats")
def learning_stats(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretório de memória"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
):
    """Dashboard unificado do estado de aprendizado — modelo, experiências, feedback e recomendações.

    Mostra em uma tela:
      • Modelo atual e se está pinado pelo usuário (L8)
      • Taxa de sucesso das sessões (MODEL_EXPERIENCE.md)
      • Feedbacks positivos/negativos (/thumbsup, /thumbsdown)
      • Skills pendentes de aprovação (SKILLS_LEARNED.md)
      • Top-3 recomendações do LearningEngine
      • Últimas lições do SelfTuner (RUNTIME_LESSONS.md)
    """
    import json as _json
    import re as _re
    from ..learning_engine import LearningEngine

    engine = LearningEngine(memory_dir)
    state = read_state(state_file)
    machine_id = state.get("machine_id", "") if state else ""

    # ── 1. Modelo ──────────────────────────────────────────────────────────────
    _pref_file = memory_dir / "model_preference.json"
    _pref_model, _pref_provider, _pref_pinned = "", "", False
    try:
        if _pref_file.exists():
            _p = _json.loads(_pref_file.read_text(encoding="utf-8"))
            _pref_model = _p.get("model", "")
            _pref_provider = _p.get("provider", "")
            _pref_pinned = _p.get("set_by") == "user"
    except Exception:
        pass
    try:
        from ..config_loader import load_config as _lc
        _cfg = _lc()
        _active_model = _pref_model or _cfg.model.name
        _active_provider = _pref_provider or _cfg.model.provider
    except Exception:
        _active_model = _pref_model or "?"
        _active_provider = _pref_provider or "?"

    _pin_tag = " [bold green](pinado pelo usuário)[/bold green]" if _pref_pinned else ""
    console.print(
        f"\n[bold cyan]◆ Modelo ativo:[/bold cyan] {_active_model} "
        f"[dim]via {_active_provider}[/dim]{_pin_tag}"
    )

    # ── 2. Experiências ─────────────────────────────────────────────────────────
    exps = engine.load_experience()
    _total = len(exps)
    _ok = sum(1 for e in exps if e.result == "ok")
    _err = sum(1 for e in exps if e.result == "error")
    _int = sum(1 for e in exps if e.result == "interrupted")
    _ok_pct = f"{100*_ok//_total}%" if _total else "—"

    exp_table = Table(title="Sessões registradas", show_lines=False, box=None)
    exp_table.add_column("", style="dim")
    exp_table.add_column("", justify="right")
    exp_table.add_row("Total", str(_total))
    exp_table.add_row("Ok", f"[green]{_ok}[/green]  ({_ok_pct})")
    exp_table.add_row("Erro", f"[red]{_err}[/red]")
    exp_table.add_row("Interrompidas", str(_int))
    if exps:
        _last = exps[-1]
        exp_table.add_row("Última", f"{_last.title[:40]} [{_last.timestamp[:16]}]")
    console.print(exp_table)

    # ── 3. Feedback ─────────────────────────────────────────────────────────────
    _fb_file = memory_dir / "FEEDBACK.md"
    _fb_pos, _fb_neg = 0, 0
    try:
        if _fb_file.exists():
            _txt = _fb_file.read_text(encoding="utf-8")
            _fb_pos = _txt.count("positivo")
            _fb_neg = _txt.count("negativo")
    except Exception:
        pass
    _fb_total = _fb_pos + _fb_neg
    if _fb_total:
        _fb_bar = "👍" * min(_fb_pos, 10) + "👎" * min(_fb_neg, 10)
        console.print(
            f"\n[bold]Feedback:[/bold]  {_fb_pos} positivo  {_fb_neg} negativo"
            f"   [dim]{_fb_bar}[/dim]"
        )
    else:
        console.print(
            "\n[dim]Feedback: nenhum ainda — use /thumbsup ou /thumbsdown durante a sessão.[/dim]"
        )

    # ── 4. Skills pendentes ─────────────────────────────────────────────────────
    try:
        from ..skill_registry import SkillRegistry as _SR
        _pending = _SR(memory_dir).pending_suggestions()
        if _pending:
            _names = ", ".join(s["name"] for s in _pending[:5])
            console.print(
                f"\n[bold]Skills pendentes:[/bold] {len(_pending)}  "
                f"[dim]({_names}{'…' if len(_pending) > 5 else ''})[/dim]\n"
                f"[dim]  → bauer learning learn  para ver candidatos detectados automaticamente[/dim]"
            )
    except Exception:
        pass

    # ── 5. Recomendações ────────────────────────────────────────────────────────
    recs = engine.recommend(machine_id=machine_id)
    if recs:
        _SCOLOR = {"info": "dim", "suggestion": "cyan", "warning": "yellow"}
        console.print("\n[bold]Recomendações ativas:[/bold]")
        for r in recs[:3]:
            _c = _SCOLOR.get(r.severity, "white")
            console.print(f"  [{_c}]▸ {r.action}[/{_c}]")
            console.print(f"    [dim]{r.reason}[/dim]")
    else:
        console.print("\n[dim]Nenhuma recomendação ativa.[/dim]")

    # ── 6. Lições do SelfTuner ──────────────────────────────────────────────────
    _rt_file = memory_dir / "RUNTIME_LESSONS.md"
    try:
        if _rt_file.exists():
            _rt_text = _rt_file.read_text(encoding="utf-8")
            _lessons = _re.findall(r"## \[([^\]]+)\][^\n]*\n(.*?)(?=\n## |\Z)", _rt_text, _re.S)
            if _lessons:
                console.print(f"\n[bold]Últimas lições do auto-tuner:[/bold]")
                for _ts, _body in _lessons[-3:]:
                    _first = _body.strip().splitlines()[0][:80] if _body.strip() else ""
                    console.print(f"  [dim]{_ts[:16]}[/dim]  {_first}")
    except Exception:
        pass

    console.print(
        "\n[dim]Dicas: 'bauer learning explain' → recomendações detalhadas | "
        "'bauer learning clean' → remove ruído | "
        "'bauer learning learn' → skills automáticas[/dim]\n"
    )


@learning_app.command("analyze")
def learning_analyze(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    model: str = typer.Option("", "--model", "-m", help="Modelo a usar (default: config.yaml)"),
    show_last: bool = typer.Option(False, "--last", "-l", help="Exibe a ultima analise salva sem gerar nova"),
):
    """Analisa os arquivos de memória usando o LLM e gera relatório com insights.

    Lê MODEL_EXPERIENCE.md, FAILED_ATTEMPTS.md, RUNTIME_LESSONS.md e SKILLS_LEARNED.md,
    envia ao modelo configurado e salva o relatório em memory/LEARNING_ANALYSIS.md.

    Nunca altera config. Nunca executa nada automaticamente — apenas analisa e sugere.
    """
    from rich.markdown import Markdown

    from ..learning_engine import LearningEngineV2

    engine = LearningEngineV2(memory_dir)

    if show_last:
        last = engine.load_last_analysis()
        if last:
            console.print(Markdown(last))
        else:
            console.print("[dim]Nenhuma análise salva. Rode: bauer learning analyze[/dim]")
        return

    summary = engine._v1.summary()
    total = sum(summary.values())
    if total == 0:
        console.print(
            "[yellow]Nenhum dado de aprendizado encontrado.[/yellow]\n"
            "[dim]Use 'bauer memory add-model-exp' para registrar experiências.[/dim]"
        )
        return

    console.print(
        f"[dim]Dados: {', '.join(f'{k}: {v}' for k, v in summary.items())}[/dim]"
    )
    console.print("[bold cyan]Analisando com modelo...[/bold cyan] [dim](pode levar alguns segundos)[/dim]")
    console.print()

    try:
        result = engine.analyze(model=model or None)
    except Exception as exc:
        console.print(f"[red]Erro ao analisar: {exc}[/red]")
        raise typer.Exit(1)

    console.print(Markdown(result.report))
    console.print()
    console.print(
        f"[dim]Modelo: {result.model_used} | Salvo em: memory/LEARNING_ANALYSIS.md[/dim]"
    )
