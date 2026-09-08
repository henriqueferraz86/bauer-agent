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
