"""Pulso de vida do run — a diferença entre "trabalhando" e "travado".

O recovery julgava um run preso pela idade de ``updated_at``, que só se move em
MUDANÇA DE ESTADO. Um run pode passar meia hora legitimamente dentro do executor
(turno longo) ou dentro dos gates — ``tests_gate_timeout_s`` e o timeout do
AcceptanceGate valem 600s CADA, ambos ligados por default, e juntos passam do
``max_age_s`` de 900s sem uma única transição.

O desfecho era o pior possível: o recovery marcava ``failed`` um run VIVO, que
depois escrevia ``completed`` por cima. Dois escritores, conclusões opostas,
sobre trabalho que deu certo.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.runtime.resilience import RunHeartbeat, RuntimeRecovery
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore


@pytest.fixture
def kit(tmp_path: Path):
    store = JsonlStateStore(tmp_path / "runtime")
    bus = EventBus(store=store)
    runs = RunManager(store=store, event_bus=bus)
    return store, bus, runs


def _envelhecer(runs: RunManager, run_id: str, segundos: int) -> None:
    """Empurra `updated_at` para o passado — o run parece parado há tanto tempo."""
    velho = (datetime.now(UTC) - timedelta(seconds=segundos)).isoformat()
    run = runs.get_run(run_id)
    dados = run.__dict__.copy()
    dados["updated_at"] = velho
    runs.store.upsert("runs", type(run)(**dados))


# ── o defeito ─────────────────────────────────────────────────────────────────


def test_recovery_nao_mata_run_vivo_com_pulso_fresco(kit, tmp_path):
    """O caso que motivou tudo: run trabalhando há mais de max_age_s.

    Sem pulso, este run virava `failed` com a mensagem "stuck for more than
    900s" — e o trabalho continuava rodando, para escrever `completed` em cima
    depois. Com pulso fresco, o recovery reconhece que alguém ainda está lá.
    """
    _store, _bus, runs = kit
    run = runs.create_run(session_id="s", agent_id="a", input={}, status="running")
    _envelhecer(runs, run.id, 3600)  # uma hora sem transição

    heartbeats = RunHeartbeat(store=runs.store)
    heartbeats.bater(run.id)  # ...mas vivo agora

    recuperados = RuntimeRecovery(store=runs.store).recover_stuck_runs(max_age_s=900)

    assert recuperados == [], "run com pulso fresco não está travado, está trabalhando"
    assert runs.get_run(run.id).status == "running"


def test_recovery_ainda_mata_run_com_pulso_velho(kit):
    """O processo morreu: a thread do pulso morreu junto e a batida envelheceu.

    É exatamente o sinal que se quer — o pulso mede "alguém vivo ainda tem este
    run", não "o run existe".
    """
    _store, _bus, runs = kit
    run = runs.create_run(session_id="s", agent_id="a", input={}, status="running")
    _envelhecer(runs, run.id, 3600)

    velho = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    runs.store.upsert(RunHeartbeat.COLECAO,
                      {"id": run.id, "last_seen_at": velho, "pid": 1})

    recuperados = RuntimeRecovery(store=runs.store).recover_stuck_runs(max_age_s=900)

    assert len(recuperados) == 1 and recuperados[0]["run_id"] == run.id
    assert runs.get_run(run.id).status == "failed"


def test_run_sem_pulso_nenhum_mantem_a_regra_antiga(kit):
    """Retrocompat: runs gravados antes disto, e caminhos que não passam pelo
    Kernel, não têm pulso — e continuam recuperáveis por idade pura. Ausência de
    pulso não pode virar imunidade."""
    _store, _bus, runs = kit
    run = runs.create_run(session_id="s", agent_id="a", input={}, status="running")
    _envelhecer(runs, run.id, 3600)

    recuperados = RuntimeRecovery(store=runs.store).recover_stuck_runs(max_age_s=900)

    assert len(recuperados) == 1
    assert runs.get_run(run.id).status == "failed"


def test_recovery_nao_apaga_desfecho_decidido_entre_a_leitura_e_a_escrita(kit):
    """`list_runs()` e a escrita não são atômicos: o dono do run pode concluir no
    meio. Um `fail_run` cru apagaria o `completed` — a mesma corrida que o
    /stream já pagou uma vez."""
    _store, _bus, runs = kit
    run = runs.create_run(session_id="s", agent_id="a", input={}, status="running")
    _envelhecer(runs, run.id, 3600)

    recovery = RuntimeRecovery(store=runs.store)
    original = recovery.run_manager.list_runs

    def list_runs_e_conclui():
        resultado = original()
        runs.complete_run(run.id, output={"output": "deu certo"})
        return resultado

    recovery.run_manager.list_runs = list_runs_e_conclui
    recuperados = recovery.recover_stuck_runs(max_age_s=900)

    assert recuperados == [], "não recuperou nada: o dono decidiu primeiro"
    assert runs.get_run(run.id).status == "completed", (
        "quem chega primeiro ao terminal decide")


# ── o pulso em si ─────────────────────────────────────────────────────────────


def test_pulso_bate_enquanto_o_bloco_roda_e_para_ao_sair(kit):
    _store, _bus, runs = kit
    hb = RunHeartbeat(store=runs.store)

    with hb.pulso("run-x", intervalo_s=0.05):
        assert hb.ultimo("run-x") is None, "não bate na entrada: updated_at já é fresco"
        threading.Event().wait(0.2)
        durante = hb.ultimo("run-x")

    assert durante is not None, "o pulso tem que bater durante o bloco"

    depois_de_sair = hb.ultimo("run-x")
    threading.Event().wait(0.2)
    assert hb.ultimo("run-x").last_seen_at == depois_de_sair.last_seen_at, (
        "a thread do pulso tem que parar ao sair do contexto")


def test_pulso_desligado_nao_escreve_nada(kit):
    _store, _bus, runs = kit
    hb = RunHeartbeat(store=runs.store)
    with hb.pulso("run-y", intervalo_s=0):
        threading.Event().wait(0.1)
    assert hb.ultimo("run-y") is None


def test_run_curto_nao_gera_pulso(kit):
    """Custo zero no caso comum: run mais rápido que o intervalo não escreve."""
    _store, bus, runs = kit
    k = BauerKernel(runs=runs, bus=bus, heartbeat_interval_s=30.0)
    krun = k.execute(KernelRequest(task="t"), executor=lambda p: {"output": "ok"})
    assert krun.status == "completed"
    assert RunHeartbeat(store=runs.store).ultimo(krun.run_id) is None


# ── wiring: o Kernel bate sozinho ─────────────────────────────────────────────


def test_kernel_mantem_o_pulso_durante_o_executor(kit):
    """Sem isto o pulso seria uma peça que existe e ninguém usa — o Kernel é o
    único que sabe quando um executor começou e ainda não voltou."""
    _store, bus, runs = kit
    k = BauerKernel(runs=runs, bus=bus, heartbeat_interval_s=0.05)
    visto: list = []

    def executor_lento(payload):
        threading.Event().wait(0.25)
        visto.append(RunHeartbeat(store=runs.store).ultimo(payload["run_id"]))
        return {"output": "ok"}

    krun = k.execute(KernelRequest(task="t"), executor=executor_lento)

    assert krun.status == "completed"
    assert visto and visto[0] is not None, (
        "o run bateu o pulso enquanto o executor rodava")


def test_kernel_mantem_o_pulso_durante_os_gates(kit):
    """A outra metade: AcceptanceGate + TestsGate valem 600s cada por default e
    rodam DEPOIS do executor, em `evaluating`, sem transição nenhuma no meio."""
    _store, bus, runs = kit
    visto: list = []

    class _GateLento:
        name = "lento"

        def check(self, *, request, result):
            threading.Event().wait(0.25)
            from bauer.core.kernel.evaluator import GateResult
            return GateResult(self.name, True)

    from bauer.core.kernel.evaluator import Evaluator

    k = BauerKernel(runs=runs, bus=bus, heartbeat_interval_s=0.05,
                    evaluator=Evaluator([_GateLento()], max_replans=0))
    krun = k.execute(KernelRequest(task="t"), executor=lambda p: {"output": "ok"})
    visto.append(RunHeartbeat(store=runs.store).ultimo(krun.run_id))

    assert krun.status == "completed"
    assert visto[0] is not None, "o pulso tem que cobrir os gates, não só o executor"


def test_kernel_mantem_o_pulso_no_stream(kit):
    """A terceira porta. Entre um delta e o próximo não há transição de estado."""
    _store, bus, runs = kit
    k = BauerKernel(runs=runs, bus=bus, heartbeat_interval_s=0.05)

    def executor(payload):
        yield {"event": "message.delta", "content": "a"}
        threading.Event().wait(0.25)
        yield {"event": "message.delta", "content": "b"}

    final = list(k.stream(KernelRequest(task="t"), executor=executor))[-1]

    assert final["run"].status == "completed"
    assert RunHeartbeat(store=runs.store).ultimo(final["run"].run_id) is not None
