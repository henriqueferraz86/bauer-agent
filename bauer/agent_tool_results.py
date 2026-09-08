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
