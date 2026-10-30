"""Conjunto fixo e pequeno de tarefas representativas.

Fixo de propósito: a SPEC pede um conjunto pequeno e estável, não uma amostra
aleatória — resultados de rodadas diferentes só são comparáveis entre si se a
tarefa não mudou. Adicionar uma tarefa nova é uma decisão deliberada (editar
este arquivo), não um parâmetro de linha de comando.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    #: prepara arquivos no workspace temporário antes da tarefa rodar (tarefas
    #: que exercitam tools de arquivo); None para tarefas sem setup.
    setup: Optional[Callable[[Path], None]] = field(default=None, compare=False)


def _setup_read_file_task(workspace: Path) -> None:
    (workspace / "relatorio.txt").write_text(
        "Vendas de outubro: 1200 unidades. Meta do mês: 1000 unidades.\n",
        encoding="utf-8",
    )


TASKS: tuple[Task, ...] = (
    Task(
        name="qa_simples",
        prompt="O que é recursão em programação? Responda em no máximo 2 frases.",
    ),
    Task(
        name="leitura_arquivo",
        prompt=(
            "Leia o arquivo relatorio.txt neste workspace e diga se a meta de "
            "outubro foi batida, com os números."
        ),
        setup=_setup_read_file_task,
    ),
    Task(
        name="raciocinio_curto",
        prompt=(
            "Uma loja vende 3 produtos a R$12, R$7 e R$25. Um cliente leva 2 "
            "unidades do primeiro e 1 do terceiro. Quanto ele paga no total?"
        ),
    ),
    Task(
        name="codigo_curto",
        prompt=(
            "Escreva uma função Python chamada `is_palindrome(text: str) -> bool` "
            "que ignora maiúsculas/minúsculas e espaços. Só o código, sem explicação."
        ),
    ),
)
