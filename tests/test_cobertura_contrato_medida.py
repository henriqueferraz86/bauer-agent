"""`autonomous` no KernelRequest + a medição de cobertura de contrato.

O §15 pede `task_contract_coverage: 100%` e o indicador ficou aberto por uma
razão que não era técnica: **de quantas execuções estamos falando?**

"Toda execução" incluiria conversa e turno interativo. O resultado previsível
seria contrato genérico em todo projeto — e contrato genérico é pior que
nenhum, porque `scope.allowed: ["."]` significa "sem restrição" (o `_casa()`
trata `.` como tudo) e `commands: []` faz o gate de aceite passar. Daria 100%
de cobertura com ZERO proteção, e com a agravante de passar a acreditar que
está protegido.

O denominador escolhido é **run autônomo**: ninguém entre os turnos. É onde o
falso-sucesso custa uma hora de trabalho errado.

E a marcação é DECLARADA na origem, não inferida do endpoint. Inferir de
`input["endpoint"]` erraria justamente o caso que mais importa: o `/loop`
disparado de dentro do `bauer agent` interativo é autônomo e nasce pelo caminho
interativo.

Nada disto bloqueia. A capacidade nasce `informativa`: reporta e fica fora da
média, até haver dado suficiente para a decisão de exigir ser tomada com
evidência em vez de com definição.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore
from bauer.core.task import TaskContract

RAIZ = Path(__file__).resolve().parent.parent


def _kernel(tmp_path, contrato=None):
    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    return BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus,
                       contract=contrato)


def _req(**kw):
    return KernelRequest(agent_id="t", operation="runtime.execute", input={}, **kw)


# ─── o campo ─────────────────────────────────────────────────────────────────


def test_default_e_falso():
    """Ninguém ganha marcação por acidente — quem é autônomo declara."""
    assert KernelRequest().autonomous is False


def test_run_autonomo_fica_gravado(tmp_path):
    """Precisa persistir: sem isso, "quantos runs autônomos tinham contrato?"
    só daria para responder olhando o código, e a resposta mudaria a cada
    refactor."""
    k = _kernel(tmp_path)
    out = k.execute(_req(autonomous=True), executor=lambda p: {"output": "ok"})

    run = k.runs.get_run(out.run_id)
    assert run.input["autonomous"] is True


def test_run_normal_nao_polui_o_registro(tmp_path):
    """Turno interativo não entra no denominador — nem como False."""
    k = _kernel(tmp_path)
    out = k.execute(_req(), executor=lambda p: {"output": "ok"})

    assert "autonomous" not in (k.runs.get_run(out.run_id).input or {})


def test_grava_SE_havia_contrato_no_momento_do_run(tmp_path):
    """O numerador. Gravado junto porque contrato é do workspace e muda: medir
    depois olhando o disco leria o estado de HOJE, não o do run."""
    k = _kernel(tmp_path, TaskContract.from_mapping({"objective": "x"}))
    out = k.execute(_req(autonomous=True), executor=lambda p: {"output": "ok"})

    assert k.runs.get_run(out.run_id).input["task_contract"] is True


def test_sem_contrato_marca_falso(tmp_path):
    k = _kernel(tmp_path, None)
    out = k.execute(_req(autonomous=True), executor=lambda p: {"output": "ok"})

    assert k.runs.get_run(out.run_id).input["task_contract"] is False


# ─── os call sites ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("arquivo,marca", [
    ("bauer/commands/run_cmd.py", "autonomous=True"),
    ("bauer/core/runtime/scheduler.py", "autonomous=True"),
    ("bauer/server.py", "autonomous=True"),
])
def test_os_lacos_sem_supervisao_estao_marcados(arquivo, marca):
    """`bauer run`, scheduler e `/loop` da web. São os três que rodam até acabar
    sem ninguém entre os turnos."""
    assert marca in (RAIZ / arquivo).read_text(encoding="utf-8")


def test_conversa_nao_e_marcada():
    """`/chat` e `/v1` são request/response — o humano está do outro lado. Marcar
    inflaria o denominador com execuções onde contrato é atrito puro."""
    server = (RAIZ / "bauer" / "server.py").read_text(encoding="utf-8")
    # a única marcação no server é a do /loop
    assert server.count("autonomous=True") == 1


# ─── a medição ───────────────────────────────────────────────────────────────


def test_sem_historico_a_capacidade_e_INFORMATIVA():
    """Sem denominador não há fração. Exibir 0% ou 100% seria inventar o número
    que este arquivo inteiro existe para não inventar."""
    from evals.harness.medir import cap_task_contract

    c = cap_task_contract()
    assert c.informativa is True


def test_informativa_fica_fora_da_media():
    """Uma linha que reporta não pode mover a nota — senão o scorecard mede a
    própria instrumentação."""
    from evals.harness.medir import CAPACIDADES

    caps = [fn() for fn in CAPACIDADES]
    avaliadas = [c for c in caps if not c.informativa]

    assert len(avaliadas) < len(caps), "nenhuma informativa registrada"
    assert all(c.meta > 0 for c in avaliadas), (
        "capacidade avaliada com meta 0 passaria sempre")


def test_conta_certo_com_historico(tmp_path, monkeypatch):
    """A fração de verdade, com runs escritos no formato real do store."""
    import evals.harness.medir as m

    rt = tmp_path / "memory" / "runtime"
    rt.mkdir(parents=True)
    linhas = [
        {"id": "run-1", "agent_id": "cli.run",
         "input": {"autonomous": True, "task_contract": True}},
        {"id": "run-2", "agent_id": "cli.run",
         "input": {"autonomous": True, "task_contract": False}},
        {"id": "run-3", "agent_id": "cli.agent", "input": {}},   # interativo: fora
    ]
    (rt / "runs.jsonl").write_text(
        "\n".join(json.dumps(x) for x in linhas), encoding="utf-8")
    monkeypatch.setattr(m, "RAIZ", tmp_path)

    c = m.cap_task_contract()

    assert c.total == 2, "o run interativo entrou no denominador"
    assert c.pct == 50
    assert c.informativa is True, "medir nao pode virar cobrar sem decisao"


def test_ultima_versao_do_run_vence(tmp_path, monkeypatch):
    """O store é append-only: o mesmo run aparece várias vezes conforme muda de
    estado. Contar linhas em vez de runs inflaria tudo."""
    import evals.harness.medir as m

    rt = tmp_path / "memory" / "runtime"
    rt.mkdir(parents=True)
    (rt / "runs.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"id": "run-1", "status": "created",
         "input": {"autonomous": True, "task_contract": False}},
        {"id": "run-1", "status": "completed",
         "input": {"autonomous": True, "task_contract": False}},
    ]), encoding="utf-8")
    monkeypatch.setattr(m, "RAIZ", tmp_path)

    assert m.cap_task_contract().total == 1


def test_medir_nao_cria_nada(tmp_path, monkeypatch):
    """Medição com efeito colateral altera o que mede. O leitor não pode criar
    store nem diretório."""
    import evals.harness.medir as m

    monkeypatch.setattr(m, "RAIZ", tmp_path)
    m.cap_task_contract()

    assert not (tmp_path / "memory").exists()
