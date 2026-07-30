"""Baseline de testes — o gate reprova o que PIOROU, não o que já estava vermelho.

O problema que isto resolve apareceu no primeiro uso real do gate de testes: a
tarefa mandava mexer **só** num arquivo, o agente obedeceu e fez o trabalho
certo, e o run falhou por causa de um teste quebrado em outro módulo que a
tarefa proibia tocar.

O gate valida o PROJETO; a tarefa tem ESCOPO. Enquanto não existe TaskContract
(S10) para reconciliar os dois, um projeto com suíte já vermelha reprovaria todo
run autônomo — o que na prática obriga a desligar o gate, e um gate desligado não
governa nada.

A saída é a mesma que este repositório já usa e confia para `except:pass`: um
**ratchet**. Guarda-se o conjunto de testes que já falhavam; reprova-se só quando
aparece falha NOVA. Falha pré-existente é reportada, não bloqueia. E quando o
conjunto encolhe, o baseline aperta sozinho — dívida paga fica travada.

Formato deliberadamente simples (JSON com lista de IDs): dá para ler, dá para
editar à mão quando alguém quer forçar o aperto, e não exige migração.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: `FAILED tests/test_x.py::test_y - assert ...` e `ERROR tests/test_x.py`
_PYTEST_FALHA = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s+-.*)?$", re.MULTILINE)

#: nome do arquivo dentro do runtime do workspace
BASELINE_NOME = "tests_baseline.json"


def extrair_falhas(saida: str) -> set[str]:
    """IDs de teste que falharam, a partir do output do runner.

    Hoje entende o formato do pytest, que é o do stack Python. Para stacks sem
    formato reconhecido devolve conjunto vazio — e aí o gate cai para comparação
    por CONTAGEM (ver ``comparar``), que é grosseira mas honesta: sem IDs não dá
    para afirmar QUAL teste é novo.
    """
    return {m.group(1) for m in _PYTEST_FALHA.finditer(saida or "")}


def caminho_baseline(workspace: "str | Path") -> Path:
    return Path(workspace) / "memory" / "runtime" / BASELINE_NOME


def ler_baseline(workspace: "str | Path") -> "dict | None":
    p = caminho_baseline(workspace)
    try:
        dados = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def gravar_baseline(workspace: "str | Path", *, falhas: set[str], total: int) -> None:
    p = caminho_baseline(workspace)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps({"falhas": sorted(falhas), "total": int(total)},
                       indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:  # noqa: BLE001 — baseline é otimização, não requisito
        from ....logging_config import log_suppressed
        log_suppressed("gate.baseline.gravar", exc)


def comparar(agora: set[str], total_agora: int, base: "dict | None") -> "tuple[bool, str]":
    """``(regrediu, motivo)`` — comparado ao baseline conhecido.

    Sem baseline, a primeira execução NÃO reprova: ela estabelece o ponto de
    partida. Reprovar aqui puniria alguém por herdar um projeto vermelho, que é
    exatamente o que este mecanismo existe para evitar.
    """
    if base is None:
        return False, (f"baseline estabelecido: {len(agora)} falha(s) pré-existente(s) "
                       f"não bloqueiam; a partir daqui só falha NOVA reprova")

    conhecidas = set(base.get("falhas") or [])
    novas = agora - conhecidas
    if novas:
        lista = "\n".join(f"  - {n}" for n in sorted(novas)[:10])
        return True, f"{len(novas)} teste(s) que passavam agora falham:\n{lista}"

    # Sem IDs (stack sem formato reconhecido): compara contagem. Grosseiro, mas
    # não deixa passar uma piora só porque não sabemos os nomes.
    if not agora and total_agora > int(base.get("total") or 0):
        return True, (f"falhas subiram de {base.get('total')} para {total_agora} "
                      f"(runner sem IDs identificáveis)")

    if agora:
        return False, (f"{len(agora)} falha(s) pré-existente(s), nenhuma nova — "
                       f"não bloqueia, mas continua vermelho")
    return False, "sem falhas"
