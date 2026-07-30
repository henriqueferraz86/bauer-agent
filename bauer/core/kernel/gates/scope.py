"""ScopeGate — o run não conclui se mexeu fora do que a tarefa permitia.

Este gate foi **descartado uma vez**, deliberadamente, e o motivo importa: sem
TaskContract não havia com o que comparar. Pior, a versão que eu tinha em mente
seria redundante — o sandbox do ``tool_router`` já barra escrita fora do
workspace ANTES de acontecer, que é o lugar certo (recusar vence detectar).

O que sobrava sem contrato era nada. O que o contrato acrescenta é o caso real:
*"você devia mexer só no doc.py"* — perímetro por TAREFA, dentro de um workspace
que o sandbox já considera legítimo. Foi exatamente o cenário que apareceu no
primeiro uso do gate de testes.

Fonte dos arquivos alterados: git. Aqui isso não é limitação — escrita fora do
repositório já é impossível pelo sandbox, então o que o git enxerga é
precisamente o universo que este gate precisa julgar.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .tests import _e_ruido


def arquivos_alterados(workspace: Path) -> "list[str] | None":
    """Caminhos alterados, relativos ao workspace. None se não for repo git."""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(workspace),
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    saida: list[str] = []
    for linha in proc.stdout.splitlines():
        alvo = linha[3:].strip() if len(linha) > 3 else ""
        if not alvo or _e_ruido(alvo):
            continue
        # renomeio vem como "antigo -> novo"; o que importa é o destino
        if " -> " in alvo:
            alvo = alvo.split(" -> ", 1)[1]
        saida.append(alvo.strip('"'))
    return saida


class ScopeGate:
    """Reprova quando o run alterou arquivo fora do escopo do contrato."""

    name = "scope"

    def __init__(self, workspace: "str | Path", contrato: Any = None,
                 *, alterados_fn: Any = None) -> None:
        self.workspace = Path(workspace)
        self.contrato = contrato
        self._alterados = alterados_fn or arquivos_alterados

    def check(self, *, request: Any, result: dict[str, Any]) -> Any:
        from ..evaluator import GateResult as _GR

        contrato = self.contrato
        if contrato is None:
            from ...task import TaskContract
            contrato = TaskContract.descobrir(self.workspace)
        if contrato is None or not contrato.tem_escopo:
            # Sem contrato (ou contrato sem escopo declarado) não há o que
            # julgar. Reprovar aqui puniria todo run que não usa contrato — e o
            # sandbox do tool_router segue valendo de qualquer forma.
            return _GR(self.name, True, "sem escopo declarado no contrato")

        alterados = self._alterados(self.workspace)
        if alterados is None:
            return _GR(self.name, True, "workspace não é repo git — sem como aferir escopo")
        if not alterados:
            return _GR(self.name, True, "nenhum arquivo alterado")

        violacoes = contrato.violacoes_de_escopo(alterados)
        if not violacoes:
            return _GR(self.name, True, f"{len(alterados)} arquivo(s), todos no escopo")

        detalhe = "\n".join(f"  - {v}" for v in violacoes[:10])
        return _GR(self.name, False,
                   f"{len(violacoes)} alteração(ões) fora do escopo da tarefa:\n{detalhe}")
