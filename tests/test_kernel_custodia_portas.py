"""As TRÊS portas de entrada do Kernel têm que se comportar igual.

`execute()` recebeu, sprint a sprint, as proteções que a operação real exigiu:
`_fail_se_nao_terminal` para a corrida de dois escritores, `getattr` para o
`request` ausente. `continue_run()` e `stream()` — as outras duas portas — não
receberam, e ninguém notou porque TODO teste de replan usava `execute()`.

Cada teste aqui reproduz um defeito medido em 2026-08-16. O que eles protegem
não é o comportamento de uma função: é a igualdade entre as portas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.kernel.evaluator import Evaluator, GateResult
from bauer.core.policy.approvals import ApprovalManager
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore


@pytest.fixture
def kit(tmp_path: Path):
    store = JsonlStateStore(tmp_path / "runtime")
    bus = EventBus(store=store)
    runs = RunManager(store=store, event_bus=bus)
    return store, bus, runs


class _ReprovaNaPrimeira:
    """Reprova a primeira passada e aprova a segunda — força UM replan."""

    name = "reprova_na_primeira"

    def __init__(self) -> None:
        self.chamadas = 0

    def check(self, *, request, result):
        self.chamadas += 1
        if self.chamadas == 1:
            return GateResult(self.name, False, "precisa de outra volta")
        return GateResult(self.name, True)


# ── porta 2: continue_run() ───────────────────────────────────────────────────


def test_replan_funciona_por_continue_run_e_nao_so_por_execute(kit):
    """Replan num run ADMITIDO — o caminho do /loop e do /v1 do serve.

    `admit()` cria o run e devolve o id ao cliente HTTP na hora; outra thread
    continua com `continue_governed()` -> `continue_run()`, que chama o mesmo
    `_run_to_completion` de `execute()` — só que com `request=None`.

    O replan publicava `run.planning.started` lendo `request.operation` direto,
    e estourava AttributeError ali, deixando o run preso em `planning` (estado
    recuperável: 15min depois o recover() o marcava "stuck", que é a causa
    errada). O acesso direto era o ÚNICO restante — max_retries, backoff e
    fallbacks já liam por getattr, pela mesma razão.
    """
    _store, bus, runs = kit
    k = BauerKernel(runs=runs, bus=bus,
                    evaluator=Evaluator([_ReprovaNaPrimeira()], max_replans=1))

    run, early = k.admit(KernelRequest(task="t", agent_id="a"))
    assert early is None and run.status == "queued"

    vistos: list[str | None] = []

    def executor(payload):
        vistos.append(payload.get("replan_feedback"))
        return {"output": f"volta {len(vistos)}"}

    krun = k.continue_run(run.id, executor=executor)

    assert krun.status == "completed", (
        "o replan por continue_run() tem que concluir como o de execute()")
    assert len(vistos) == 2, "o executor deveria ter rodado duas vezes (1 replan)"
    assert vistos[0] is None and "precisa de outra volta" in (vistos[1] or ""), (
        "a segunda passada tem que receber o motivo do gate no payload")
    assert runs.get_run(run.id).status == "completed"

    planning = [e for e in bus.list_events() if e.event_type == "run.planning.started"]
    assert [p.data["replan"] for p in planning] == [False, True], (
        "o planning inicial vem do preflight do admit(); o segundo é o replan — "
        "e é ESTE que estourava, deixando a auditoria sem o evento")
    assert planning[1].data["operation"] == "runtime.execute", (
        "sem request, a operação cai no default — mas o evento não pode sumir")


# ── aprovação: o desfecho não pode ser silencioso ─────────────────────────────


class _EvaluatorQuebrado:
    """Evaluator que estoura — bug de infra DENTRO do continue_run."""

    max_replans = 0
    gates: list = []

    def evaluate(self, *, run_id, request, result):
        raise RuntimeError("evaluator explodiu")


def test_approve_nao_engole_falha_inesperada_nem_deixa_run_em_limbo(kit, tmp_path):
    """Aprovação humana que vira no-op silencioso é o pior desfecho possível.

    `approve()` embrulhava `continue_run()` num `except Exception` que devolvia
    o dict do resume — documentado como "sem payload executável fica queued".
    Só que ele capturava TUDO: bug do Kernel, evaluator quebrado, executor
    ruim. O usuário aprovava, a API respondia `status: queued`, nada rodava, e
    o run ficava num estado não-terminal até o recover() convertê-lo em
    "stuck for more than 900s" — a causa errada, 15 minutos depois.

    Regra: falha inesperada FALHA o run (não fica em limbo) e propaga.
    """
    _store, bus, runs = kit
    approvals = ApprovalManager(root=tmp_path / "runtime", event_bus=bus)
    k = BauerKernel(runs=runs, bus=bus, approvals=approvals,
                    evaluator=_EvaluatorQuebrado())

    run = runs.create_run(session_id="s", agent_id="a", input={}, status="created")
    record = approvals.request(operation="runtime.execute", tool_name="kernel",
                               reason="teste", risk_level="high", payload={},
                               run_id=run.id, session_id="s")
    runs.update_run(run.id, status="waiting_approval")

    with pytest.raises(RuntimeError, match="evaluator explodiu"):
        k.approve(record.id, executor=lambda p: {"output": "algo"})

    final = runs.get_run(run.id)
    assert final.status == "failed", (
        "run aprovado que não conseguiu continuar tem que terminar em failed — "
        "não pode ficar em limbo esperando o recover() inventar outra causa")
    assert "falha ao continuar após aprovação" in (final.error or "")


# ── porta 3: stream() ─────────────────────────────────────────────────────────


def test_stream_preserva_custo_e_tools_com_evento_apos_run_completed(kit):
    """Metadados do run e echo de passthrough eram a MESMA variável.

    `last_meta` recebia tanto os metadados de `run.completed` quanto cada
    evento passthrough (tool/fase/rota). Qualquer evento emitido DEPOIS do
    `run.completed` — ordem que o contrato do executor não proíbe —
    sobrescrevia custo e contagem de tools. O run concluía com `cost=None` e
    `tool_calls_count=0`, e o custo perdido nunca chegava ao BudgetManager:
    todo run via streaming subcontava o orçamento.
    """
    _store, bus, runs = kit
    k = BauerKernel(runs=runs, bus=bus)

    def executor(payload):
        yield {"event": "message.delta", "content": "oi"}
        yield {"event": "run.completed", "tool_calls_count": 7, "cost_estimate": 0.42}
        yield {"event": "tool.finished", "name": "bash"}  # chega DEPOIS

    eventos = list(k.stream(KernelRequest(task="t"), executor=executor))
    final = eventos[-1]
    assert final["event"] == "final" and final["run"].status == "completed"

    persistido = runs.get_run(final["run"].run_id)
    assert persistido.tool_calls_count == 7, "a contagem de tools foi sobrescrita"
    assert persistido.cost_estimate == pytest.approx(0.42), "o custo foi sobrescrito"

    assert any(e.get("event") == "tool.finished" for e in eventos), (
        "o passthrough continua atravessando para o front-end")


def test_stream_nao_apaga_desfecho_terminal_ja_escrito(kit):
    """A corrida de dois escritores do /stream, medida no CI em 2026-07-30.

    O gerador SSE e a thread órfã do turno mexem no mesmo run. Se a thread
    conclui PRIMEIRO e o gerador falha depois (timeout, desconexão), um
    `fail_run` cru apagava um `completed` de trabalho que deu certo — desfecho
    decidido por escalonamento não é desfecho.

    `_fail_se_nao_terminal` já resolvia isso em `_run_to_completion`; o
    `stream()` chamava `fail_run` direto. Mesma corrida, mesma correção.
    """
    _store, bus, runs = kit
    k = BauerKernel(runs=runs, bus=bus)

    def executor(payload):
        yield {"event": "message.delta", "content": "trabalho feito"}
        # a thread órfã conclui o run antes de o gerador estourar
        runs.complete_run(payload["run_id"], output={"output": "trabalho feito"})
        raise RuntimeError("timeout do SSE")

    final = list(k.stream(KernelRequest(task="t"), executor=executor))[-1]

    assert runs.get_run(final["run"].run_id).status == "completed", (
        "quem chega primeiro ao terminal decide — falha posterior é ruído "
        "sobre um fato já estabelecido")
    assert final["run"].status == "completed", (
        "o KernelRun devolvido lê o estado persistido; não pode mentir")


def test_stream_cancelado_por_desconexao_nao_apaga_terminal(kit):
    """Mesma regra na outra saída do stream: GeneratorExit.

    Cliente que fecha a conexão DEPOIS de o trabalho concluir não desfaz o
    trabalho. O `update_run(status="cancelled")` cru não tinha essa guarda —
    `cancel_run` sempre teve, e era o único caminho que a usava.
    """
    _store, bus, runs = kit
    k = BauerKernel(runs=runs, bus=bus)

    def executor(payload):
        runs.complete_run(payload["run_id"], output={"output": "pronto"})
        yield {"event": "message.delta", "content": "pronto"}
        yield {"event": "message.delta", "content": "mais"}

    gen = k.stream(KernelRequest(task="t"), executor=executor)
    primeiro = next(gen)
    assert primeiro["event"] == "message.delta"
    run_id = [r.id for r in runs.list_runs()][0]
    gen.close()  # desconexão SSE

    assert runs.get_run(run_id).status == "completed", (
        "desconexão do cliente não cancela trabalho já concluído")
