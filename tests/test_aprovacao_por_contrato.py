"""S12 nível 3 — o contrato pede aprovação humana e o Kernel para de verdade.

`waiting_approval` estava completo desde o Sprint 1: transição válida,
`approve_run`, `deny_run`, `resume_run`, evento e comando de CLI. E
`TaskContract.requires_approval` estava no schema desde o S10.

O que não existia era o **fio entre os dois**. Medido por grep antes de escrever
isto: `requires_approval` aparecia na definição do modelo e em lugar nenhum
mais; `risk_level` do contrato, em lugar nenhum. Quem escrevesse
`requires_approval: true` num `.bauer/task.yaml` ganhava a falsa certeza de que
a tarefa pararia para pedir — e ela rodava direto.

É a mesma família do 20/20 do Context Builder e do `tool.denied` no sink errado:
a peça existe, ninguém a chama, e a ausência passa por presença.
"""

from __future__ import annotations

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.kernel.kernel import _exige_aprovacao
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore
from bauer.core.task import TaskContract


def _kernel(tmp_path, contrato=None, **kw):
    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    return BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus,
                       contract=contrato, **kw), bus


def _req():
    return KernelRequest(agent_id="t", operation="runtime.execute", input={})


def _contrato(**kw):
    return TaskContract.from_mapping({"objective": "fazer", **kw})


# ─── a regra ─────────────────────────────────────────────────────────────────


def test_contrato_que_exige_aprovacao_para_o_run(tmp_path):
    """O ponto: o executor NÃO roda. Parar depois de executar não é aprovação,
    é notificação."""
    rodou = []
    k, _ = _kernel(tmp_path, _contrato(requires_approval=True))

    out = k.execute(_req(), executor=lambda p: rodou.append(1) or {"output": "ok"})

    assert out.status == "waiting_approval"
    assert rodou == [], "o executor rodou apesar da exigência de aprovação"


def test_risco_alto_tambem_para(tmp_path):
    """Declarar `risk_level: high` e seguir direto faria o campo ser decoração —
    que é exatamente o que ele era antes disto."""
    k, _ = _kernel(tmp_path, _contrato(risk_level="high"))

    assert k.execute(_req(), executor=lambda p: {"output": "ok"}).status == "waiting_approval"


@pytest.mark.parametrize("risco,para", [
    ("critical", True), ("high", True),
    ("medium", False), ("low", False), ("", False),
])
def test_quais_riscos_pedem_humano(risco, para):
    """`medium` é o DEFAULT do contrato. Parar nele transformaria o Kernel num
    pedido de confirmação por run — e a primeira coisa que o usuário faria seria
    desligar a governança inteira."""
    assert bool(_exige_aprovacao(_contrato(risk_level=risco))) is para


def test_sem_contrato_nada_muda(tmp_path):
    """Quem não usa contrato não pode ganhar uma parada nova por acidente."""
    k, _ = _kernel(tmp_path, None)

    assert k.execute(_req(), executor=lambda p: {"output": "ok"}).status == "completed"


def test_contrato_sem_exigencia_nao_para(tmp_path):
    k, _ = _kernel(tmp_path, _contrato(scope={"allowed": ["src/"]}))

    assert k.execute(_req(), executor=lambda p: {"output": "ok"}).status == "completed"


# ─── o que a parada precisa deixar registrado ────────────────────────────────


def test_o_motivo_diz_QUAL_regra_parou(tmp_path):
    """Um run parado sem motivo é um run abandonado: quem aprova precisa saber
    o que está aprovando."""
    k, bus = _kernel(tmp_path, _contrato(requires_approval=True))
    k.execute(_req(), executor=lambda p: {"output": "ok"})

    evt = next(e for e in bus.list_events() if e.event_type == "approval.requested")
    assert "requires_approval" in (evt.message or "")
    assert evt.data["origem"] == "task_contract", (
        "quem audita precisa distinguir 'a policy pediu' de 'a tarefa se "
        "declarou arriscada'")


def test_origem_separa_contrato_de_policy(tmp_path):
    """Duas causas diferentes para o mesmo estado. Sem a marca de origem, a
    auditoria não sabe se endurecer a policy ou revisar o contrato."""
    motivo = _exige_aprovacao(_contrato(risk_level="critical"))
    assert "risk_level" in motivo and "critical" in motivo


# ─── o ciclo fecha: aprovar destrava ─────────────────────────────────────────


def _com_aprovacoes(tmp_path, contrato):
    """Kernel com ApprovalManager — é ele que dá o `approval_id` que
    `approve`/`deny` recebem. As duas operações são chaveadas na APROVAÇÃO, não
    no run: o mesmo pedido pode existir sem run (aprovação de tool)."""
    from bauer.core.policy.approvals import ApprovalManager

    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    return BauerKernel(
        runs=RunManager(store=store, event_bus=bus), bus=bus, contract=contrato,
        approvals=ApprovalManager(root=str(tmp_path / "rt"), event_bus=bus),
    )


def test_aprovar_destrava_e_o_trabalho_acontece(tmp_path):
    """Parada que não tem saída é travamento com outro nome."""
    k = _com_aprovacoes(tmp_path, _contrato(requires_approval=True))
    out = k.execute(_req(), executor=lambda p: {"output": "ok"})
    assert out.status == "waiting_approval"
    assert out.approval_id, "sem approval_id não há como aprovar"

    k.approve(out.approval_id, executor=lambda p: {"output": "ok"})

    assert k.runs.get_run(out.run_id).status in ("queued", "running", "completed")


def test_negar_encerra_o_run(tmp_path):
    k = _com_aprovacoes(tmp_path, _contrato(requires_approval=True))
    out = k.execute(_req(), executor=lambda p: {"output": "ok"})

    k.deny(out.approval_id)

    assert k.runs.get_run(out.run_id).status == "failed"


def test_sem_approval_manager_o_resume_e_a_saida(tmp_path):
    """Kernel montado à mão (sem ApprovalManager) ainda para — e `resume` é o
    destravamento. Sem isso, quem monta o Kernel sem aprovações ganharia um run
    preso para sempre."""
    k, _ = _kernel(tmp_path, _contrato(requires_approval=True))
    out = k.execute(_req(), executor=lambda p: {"output": "ok"})
    assert out.status == "waiting_approval"

    k.resume(out.run_id)

    assert k.runs.get_run(out.run_id).status == "queued"


# ─── composição ──────────────────────────────────────────────────────────────


def test_build_kernel_le_o_contrato_do_workspace(tmp_path):
    """O fio inteiro, do YAML no disco até o campo no Kernel."""
    from bauer.core.kernel import build_kernel

    ws = tmp_path / "ws"
    (ws / ".bauer").mkdir(parents=True)
    (ws / ".bauer" / "task.yaml").write_text(
        "objective: publicar em producao\nrisk_level: high\n", encoding="utf-8")

    k = build_kernel(root=str(tmp_path / "rt"), workspace=str(ws))

    assert k.contract is not None
    assert k.contract.risk_level == "high"
    assert _exige_aprovacao(k.contract)


def test_contrato_e_snapshot_nao_relido(tmp_path):
    """Mesma defesa do AcceptanceGate: reler do disco deixaria o agente desligar
    a própria exigência de aprovação no meio da execução."""
    from bauer.core.kernel import build_kernel

    ws = tmp_path / "ws"
    (ws / ".bauer").mkdir(parents=True)
    alvo = ws / ".bauer" / "task.yaml"
    alvo.write_text("objective: x\nrequires_approval: true\n", encoding="utf-8")

    k = build_kernel(root=str(tmp_path / "rt"), workspace=str(ws))
    alvo.write_text("objective: x\nrequires_approval: false\n", encoding="utf-8")

    out = k.execute(_req(), executor=lambda p: {"output": "ok"})
    assert out.status == "waiting_approval", (
        "reescrever o contrato durante o run não pode dispensar a aprovação")
