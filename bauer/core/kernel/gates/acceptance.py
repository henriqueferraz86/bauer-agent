"""AcceptanceGate — os comandos de aceite do contrato precisam sair com 0.

O contrato já tinha o campo (`validation.commands`) desde o S12 e nada o
executava. Era a metade que faltava do `TaskContract`: o escopo virou o
ScopeGate, o timeout foi para o TestsGate, e "como provar que a tarefa foi
feita" ficou como documentação.

**Aceite é diferente de teste.** O TestsGate roda a suíte do PROJETO e responde
"nada quebrou". Este responde "a coisa pedida funciona" — e quem sabe qual é a
coisa pedida é o contrato da tarefa, não o repositório.

Duas decisões que definem se o gate é honesto:

1. **O contrato vem do SNAPSHOT, não do disco.** O gate recebe o contrato lido
   ANTES do run (em `evaluator_from_config`) e nunca relê o arquivo. Sem isso, um
   agente autônomo que edita `.bauer/task.yaml` escreve o próprio critério de
   aprovação — falso-sucesso com assinatura reconhecida, exatamente o que este
   harness existe para impedir. Pior: `.bauer/` está na lista de ruído do
   TestsGate, então a edição não dispararia nem a suíte.

2. **`acceptance_criteria` em prosa NÃO é julgado aqui.** "a API deve responder
   em menos de 200ms" é critério para um humano ou para um juiz LLM, e este gate
   é o determinístico. Ele conta quantos ficaram sem verificação e diz isso no
   motivo — declarar a fronteira vale mais que fingir que a cobriu.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class AcceptanceGate:
    """Roda `validation.commands` do contrato; qualquer saída != 0 reprova."""

    name = "acceptance"

    def __init__(self, contrato: Any, workspace: "str | Path", *,
                 timeout_s: int = 600, run_fn: Any = None) -> None:
        # NÃO relê do disco — ver decisão 1 no docstring do módulo.
        self.contrato = contrato
        self.workspace = Path(workspace)
        self.timeout_s = max(1, int(timeout_s))
        self._run = run_fn or _rodar

    def check(self, *, request: Any, result: dict[str, Any]) -> Any:
        from ..evaluator import GateResult as _GR

        contrato = self.contrato
        if contrato is None:
            return _GR(self.name, True, "sem contrato — nada declarado a provar")

        comandos = list(getattr(getattr(contrato, "validation", None), "commands", []) or [])
        prosa = len(list(getattr(contrato, "acceptance_criteria", []) or []))
        if not comandos:
            return _GR(self.name, True, _sem_comandos(prosa))

        # Orçamento do CONJUNTO, não de cada comando: cinco comandos com teto de
        # 600s cada seriam 50 minutos de laço autônomo preso num gate.
        restante = float(self.timeout_s)
        for cmd in comandos:
            if restante <= 0:
                return _GR(self.name, False,
                           f"orçamento de {self.timeout_s}s esgotado antes de '{cmd}'")
            try:
                code, saida, gasto = self._run(cmd, self.workspace, restante)
            except Exception as exc:  # noqa: BLE001 — gate quebrado não reprova o run
                return _GR(self.name, True, f"gate falhou ao rodar (ignorado): {exc}")
            restante -= gasto
            if code != 0:
                return _GR(self.name, False, _motivo(cmd, code, saida, prosa))

        ok = f"{len(comandos)} comando(s) de aceite passaram"
        return _GR(self.name, True, ok if not prosa else
                   f"{ok}; {prosa} critério(s) em prosa não verificáveis aqui")


def _sem_comandos(prosa: int) -> str:
    if not prosa:
        return "contrato sem critérios de aceite"
    # Não reprova: um contrato só com prosa é um contrato incompleto, não uma
    # tarefa malfeita. Mas o motivo fica no log dizendo o que NÃO foi provado.
    return (f"{prosa} critério(s) de aceite declarados só em prosa — nenhum "
            f"verificável automaticamente (use validation.commands)")


def _motivo(cmd: str, code: int, saida: str, prosa: int) -> str:
    partes = [f"critério de aceite falhou: `{cmd}` saiu com {code}"]
    if saida.strip():
        partes.append(saida[-1200:])   # a cauda é onde está o erro
    if prosa:
        partes.append(f"({prosa} critério(s) em prosa não foram verificados)")
    return "\n".join(partes)


def _rodar(cmd: str, workspace: Path, timeout_s: float) -> "tuple[int, str, float]":
    """(exit code, saída combinada, segundos gastos)."""
    import time

    inicio = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=str(workspace), shell=True, capture_output=True,
            text=True, timeout=max(1.0, timeout_s), errors="replace",
        )
    except subprocess.TimeoutExpired:
        # Estourou o tempo: REPROVA. Não dá para afirmar que passou o que não
        # terminou — mesma regra do TestsGate.
        return 124, f"tempo esgotado após {timeout_s:.0f}s", time.monotonic() - inicio
    saida = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, saida, time.monotonic() - inicio
