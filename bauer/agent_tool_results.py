"""Funções puras para reduzir resultados de tools antes do contexto do LLM."""

from __future__ import annotations


def head_tail(
    text: str, budget: int, *, tail_share: float = 0.6,
) -> tuple[list[str], list[str], int]:
    """Seleciona início e fim sem sacrificar erros no final do resultado."""
    lines = [line for line in text.splitlines() if line.strip()]
    tail_budget = int(budget * tail_share)
    head_budget = budget - tail_budget

    tail: list[str] = []
    used = 0
    for line in reversed(lines):
        if used + len(line) + 1 > tail_budget:
            break
        tail.insert(0, line)
        used += len(line) + 1

    head: list[str] = []
    used = 0
    for line in lines[: len(lines) - len(tail)]:
        if used + len(line) + 1 > head_budget:
            break
        head.append(line)
        used += len(line) + 1

    return head, tail, len(lines) - len(head) - len(tail)


def compress_result(
    action: str, result: str, *, listing_tools: frozenset[str], preview_size: int,
    tail_share: float = 0.6,
) -> str:
    """Resume resultado grande preservando a causa provável no final."""
    lines = [line for line in result.splitlines() if line.strip()]
    n_lines, n_chars = len(lines), len(result)
    if action in listing_tools:
        preview = lines[:8]
        summary = f"[{n_chars} chars — {n_lines} itens] " + ", ".join(preview)
        if n_lines > len(preview):
            summary += f" ... +{n_lines - len(preview)} mais"
    else:
        header = f"[{n_chars} chars — {n_lines} linhas]"
        budget = max(preview_size - len(header) - 40, 80)
        head, tail, omitted = head_tail(result, budget, tail_share=tail_share)
        if not head and not tail:
            half = budget // 2
            summary = f"{header} {result[:half].rstrip()}"
            summary += f"\n... [{max(n_chars - budget, 0)} chars omitidos] ...\n"
            summary += result[-half:].lstrip()
        else:
            parts = [header, *head]
            if omitted > 0:
                parts.append(f"... +{omitted} linhas omitidas ...")
            summary = "\n".join([*parts, *tail])
    if len(summary) > preview_size:
        keep = preview_size - 3
        first = keep // 2
        summary = summary[:first] + "..." + summary[-(keep - first):]
    return summary


def format_tool_display(action: str, result: str) -> str:
    """Resume um resultado para o terminal sem alterar o texto enviado ao LLM."""
    stripped = result.strip()
    lines = stripped.splitlines()
    if action == "execute_code":
        exit_code = 0
        exit_line = next((line for line in lines if line.startswith("exit:")), None)
        if exit_line:
            try:
                exit_code = int(exit_line.split(":", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        stdout, stderr, section = [], [], None
        for line in lines:
            if line.startswith("exit:"):
                continue
            if line.strip() in ("--- stdout ---", "-- stdout --"):
                section = "out"
            elif line.strip() in ("--- stderr ---", "-- stderr --"):
                section = "err"
            elif section == "out" and line.strip():
                stdout.append(line)
            elif section == "err" and line.strip():
                stderr.append(line)
            elif section is None and line.strip() and not line.startswith("---"):
                stdout.append(line)
        if exit_code == 0:
            if not stdout:
                return "[green]✓[/green]"
            suffix = f" [dim](+{len(stdout) - 1} linhas)[/dim]" if len(stdout) > 1 else ""
            return f"[green]✓[/green] [dim]{stdout[0][:120]}[/dim]{suffix}"
        clean_errors = [line for line in stderr if "Temp\\" not in line and "tmp" not in line.lower()[:20]] or stderr
        if not clean_errors:
            return f"[red]✗ exit {exit_code}[/red]"
        suffix = f" [dim](+{len(clean_errors) - 1} linhas)[/dim]" if len(clean_errors) > 1 else ""
        return f"[red]✗ exit {exit_code}[/red] [dim]{clean_errors[0][:120]}[/dim]{suffix}"
    if action == "read_file":
        nonempty = len([line for line in lines if line.strip()])
        first = lines[0][:80].strip() if lines else ""
        return f"[dim]{nonempty} linhas — {first}{'…' if len(lines[0]) > 80 else ''}[/dim]" if first else f"[dim]{nonempty} linhas[/dim]"
    if action in ("write_file", "edit_file", "patch_file", "create_file"):
        first = lines[0][:120] if lines else stripped[:120]
        failed = "erro" in first.lower() or "error" in first.lower()
        color, symbol = ("red", "✗") if failed else ("green", "✓")
        return f"[{color}]{symbol}[/{color}] [dim]{first}[/dim]"
    if action in ("list_dir", "glob_files", "regex_search"):
        items = [line.strip() for line in lines if line.strip()]
        if not items:
            return "[dim](vazio)[/dim]"
        suffix = f" … +{len(items) - 4}" if len(items) > 4 else ""
        return f"[dim]{len(items)} itens — {', '.join(items[:4])}{suffix}[/dim]"
    if action == "http_request":
        first = lines[0][:120] if lines else stripped[:120]
        return f"[dim]{first}[/dim]"
    if action == "delegate_task":
        first = lines[0][:120] if lines else stripped[:120]
        return f"[cyan]⇢[/cyan] [dim]{first}[/dim]"
    short = stripped[:150]
    return f"[dim]{short}{'…' if len(stripped) > 150 else ''}[/dim]"
