"""DiffGate — tarefa de código que não mudou nada não está concluída.

O plano chama isto de "novo, trivial". Trivial de implementar; não de decidir.

É o gate que ataca o falso-sucesso mais comum e mais barato de produzir: o
agente escreve "pronto, implementei a função" e o repositório está exatamente
como estava. Nenhum outro gate pega esse caso — o de testes não roda (não houve
mudança), o de escopo passa (não violou nada), e os dois gates de texto olham a
resposta, que é justamente a parte que mentiu.

**Quando ele NÃO pode disparar** é o que o torna usável:

- sem contrato. Turno de conversa, pergunta, revisão de código, explicação —
  trabalho legítimo que não altera arquivo. Reprovar aqui transformaria o Bauer
  em algo que só sabe editar arquivos.
- fora de repo git e sem tool mutante registrada. Sem evidência não se afirma
  nada, nem a favor nem contra.

Ou seja: só reprova quando alguém escreveu um contrato de tarefa — que é a
declaração explícita de "isto é uma tarefa de código com perímetro e aceite" — e
mesmo assim nada mudou.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .tests import houve_mudanca


class DiffGate:
    """Reprova o run que tinha contrato de tarefa e não produziu mudança."""

    name = "diff"

    def __init__(self, workspace: "str | Path", contrato: Any = None,
                 *, mudanca_fn: Any = None) -> None:
        self.workspace = Path(workspace)
        self.contrato = contrato
        self._mudou = mudanca_fn or houve_mudanca

    def check(self, *, request: Any, result: dict[str, Any]) -> Any:
        from ..evaluator import GateResult as _GR

        if self.contrato is None:
            return _GR(self.name, True, "sem contrato — mudança não é exigida")
        if self._mudou(self.workspace, result or {}):
            return _GR(self.name, True, "o workspace mudou")
        return _GR(
            self.name, False,
            "a tarefa tinha contrato e NENHUM arquivo foi alterado. Se o "
            "trabalho foi feito, ele não chegou ao disco; se não foi, conclua-o. "
            "Um relatório de conclusão não substitui o diff.",
        )
