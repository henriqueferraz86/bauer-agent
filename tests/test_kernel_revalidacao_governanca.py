"""K08 (auditoria 2026-10-12): kill-switch e policy mutável valem em TODA tentativa.

Antes, o preflight era o único ponto que olhava o kill-switch e a policy.
``continue_run`` (o caminho do ``/loop`` web, do ``/v1``, do ``/loop`` do
Telegram e da aprovação) e o laço de retry/fallback/replan chamavam o executor
de novo sem perguntar nada — e o ``policy_check`` do replan era só uma
transição de estado. Cada teste abaixo falha sem a revalidação.
"""

from __future__ import annotations

from bauer.core.kernel import BauerKernel, KernelRequest, continue_governed
from bauer.core.kernel.evaluator import CallableGate, Evaluator
from bauer.core.policy.approvals import ApprovalManager
from bauer.core.policy.engine import PolicyEngine
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.resilience import RuntimeControl
from bauer.core.runtime.run_manager import RunManager


def _kernel(tmp_path, *, rules=None, evaluator=None, approvals=True):
    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)
    budget.set_profile(daily_budget_usd=100, max_parallel_runs=10)
    policy = PolicyEngine(workspace=tmp_path, runtime_root=tmp_path,
                          rules=rules if rules is not None else [])
    control = RuntimeControl(store=runs.store)
    kernel = BauerKernel(
        runs=runs, policy=policy, budget=budget, control=control,
        evaluator=evaluator, heartbeat_interval_s=0,
        approvals=ApprovalManager(root=tmp_path, event_bus=runs.event_bus) if approvals else None,
    )
    return kernel, control, policy, budget


class _Exec:
    def __init__(self, fn=None):
        self.payloads: list[dict] = []
        self.fn = fn

    def __call__(self, payload):
        self.payloads.append(payload)
        if self.fn is not None:
            return self.fn(len(self.payloads))
        return {"output": f"saida {len(self.payloads)}"}


# ── kill-switch ────────────────────────────────────────────────────────────


def test_continue_run_respeita_kill_switch_ligado_depois_da_admissao(tmp_path):
    kernel, control, _, _ = _kernel(tmp_path)
    run, early = kernel.admit(KernelRequest(task="x"))
    assert early is None and run.status == "queued"
    control.set_kill_switch(True)
    executor = _Exec()

    out = kernel.continue_run(run.id, executor=executor)

    assert executor.payloads == []
    assert out.status == "cancelled"
    assert "kill switch" in (out.error or "")


def test_continue_governed_bloqueado_nao_chama_executor(tmp_path):
    """A porta usada pelo /loop web, pelo /v1 e pelo /loop do Telegram."""
    kernel, control, _, _ = _kernel(tmp_path)
    run, _ = kernel.admit(KernelRequest(task="x"))
    control.set_kill_switch(True)
    executor = _Exec()

    gov = continue_governed(kernel, run.id, executor)

    assert executor.payloads == []
    assert gov.status == "cancelled" and not gov.ok
    assert gov.blocked_before_start is True


def test_replan_respeita_kill_switch_ligado_pelo_gate(tmp_path):
    chamadas = [0]

    def gate(req, res):
        chamadas[0] += 1
        if chamadas[0] == 1:
            control.set_kill_switch(True)
            return "refaça"
        return True

    kernel, control, _, _ = _kernel(
        tmp_path, evaluator=Evaluator(gates=[CallableGate("g", gate)], max_replans=2))
    executor = _Exec()

    out = kernel.execute(KernelRequest(task="x"), executor=executor)

    assert len(executor.payloads) == 1, "replan renovou trabalho com kill-switch ligado"
    assert out.status == "cancelled"


def test_retry_respeita_kill_switch_ligado_durante_a_tentativa(tmp_path):
    kernel, control, _, _ = _kernel(tmp_path)

    def falha_e_liga(_n):
        control.set_kill_switch(True)
        return {"status": "failed", "error": "provider caiu"}

    executor = _Exec(falha_e_liga)
    out = kernel.execute(KernelRequest(task="x", max_retries=3), executor=executor)

    assert len(executor.payloads) == 1
    assert out.status == "cancelled"


def test_fallback_respeita_kill_switch(tmp_path):
    kernel, control, _, _ = _kernel(tmp_path)
    executados: list[str] = []

    class Adapter:
        name = "outro"

        def run_agent(self, payload):
            executados.append(payload["run_id"])
            return {"output": "fallback"}

    def falha_e_liga(_n):
        control.set_kill_switch(True)
        return {"status": "failed", "error": "provider caiu"}

    kernel.adapter_factory = lambda name, config=None: Adapter()
    executor = _Exec(falha_e_liga)
    out = kernel.execute(KernelRequest(task="x", fallback_adapters=["outro"]),
                         executor=executor)

    assert len(executor.payloads) == 1
    assert executados == [], "fallback executou com kill-switch ligado"
    assert out.status == "cancelled"


# ── policy mutável ─────────────────────────────────────────────────────────


def test_continue_run_reavalia_policy_que_virou_deny(tmp_path):
    kernel, _, policy, _ = _kernel(tmp_path)
    run, _ = kernel.admit(KernelRequest(task="x"))
    policy.rules = [{"id": "fechado", "operation": "runtime.execute", "action": "deny",
                     "reason": "operador fechou o runtime"}]
    executor = _Exec()

    out = kernel.continue_run(run.id, executor=executor)

    assert executor.payloads == []
    assert out.status == "failed"
    assert "policy deny" in (out.error or "")


def test_continue_run_reavalia_a_operacao_e_metadata_da_admissao(tmp_path):
    """A revalidação usa a MESMA operation/metadata do request admitido, que
    sobrevivem no run persistido — não a operação padrão."""
    kernel, _, policy, _ = _kernel(tmp_path)
    run, early = kernel.admit(KernelRequest(task="x", operation="loop.run",
                                            metadata={"canal": "telegram"}))
    assert early is None
    policy.rules = [{"id": "sem-telegram", "operation": "loop.run", "action": "deny",
                     "when": {"canal": "telegram"}}]
    executor = _Exec()

    out = kernel.continue_run(run.id, executor=executor)

    assert executor.payloads == []
    assert out.status == "failed"


def test_bloco_de_governanca_nao_vaza_para_o_executor(tmp_path):
    kernel, _, _, _ = _kernel(tmp_path)
    run, _ = kernel.admit(KernelRequest(task="x", input={"message": "oi"}))
    executor = _Exec()

    out = kernel.continue_run(run.id, executor=executor)

    assert out.status == "completed"
    assert executor.payloads[0]["message"] == "oi"
    assert not any(k.startswith("_kernel") for k in executor.payloads[0])


def test_retry_reavalia_policy_alterada_entre_tentativas(tmp_path):
    kernel, _, policy, _ = _kernel(tmp_path)

    def falha_e_fecha(_n):
        policy.rules = [{"id": "fechado", "operation": "runtime.execute", "action": "deny"}]
        return {"status": "failed", "error": "transitório"}

    executor = _Exec(falha_e_fecha)
    out = kernel.execute(KernelRequest(task="x", max_retries=2), executor=executor)

    assert len(executor.payloads) == 1
    assert out.status == "failed" and "policy deny" in (out.error or "")


def test_replan_reavalia_budget_travado(tmp_path):
    """O policy_check do replan era decorativo: agora o gate de orçamento de
    runtime.execute (modo locked) é consultado de novo."""
    chamadas = [0]

    def gate(req, res):
        chamadas[0] += 1
        if chamadas[0] == 1:
            budget.set_profile(mode="locked")
            return "refaça"
        return True

    kernel, _, _, budget = _kernel(
        tmp_path, evaluator=Evaluator(gates=[CallableGate("g", gate)], max_replans=1))
    kernel.policy.budget_manager = budget
    executor = _Exec()

    out = kernel.execute(KernelRequest(task="x"), executor=executor)

    assert len(executor.payloads) == 1
    assert out.status == "failed" and "locked" in (out.error or "")


def test_policy_que_vira_ask_no_meio_pede_aprovacao_antes_do_retry(tmp_path):
    kernel, _, policy, _ = _kernel(tmp_path)

    def falha_e_pede(_n):
        policy.rules = [{"id": "revisar", "operation": "runtime.execute", "action": "ask"}]
        return {"status": "failed", "error": "transitório"}

    executor = _Exec(falha_e_pede)
    out = kernel.execute(KernelRequest(task="x", max_retries=2), executor=executor)

    assert len(executor.payloads) == 1
    assert out.status == "waiting_approval"
    assert out.approval_id
    assert kernel.approvals.get(out.approval_id).status == "pending"


# ── aprovação já concedida continua valendo ────────────────────────────────


def test_aprovacao_concedida_nao_e_pedida_de_novo_na_continuacao(tmp_path):
    kernel, _, _, _ = _kernel(
        tmp_path, rules=[{"id": "revisar", "operation": "runtime.execute", "action": "ask"}])
    executor = _Exec()
    primeiro = kernel.execute(KernelRequest(task="x"), executor=executor)
    assert primeiro.status == "waiting_approval" and executor.payloads == []

    out = kernel.approve(primeiro.approval_id, executor=executor)

    assert out.status == "completed"
    assert len(executor.payloads) == 1
    pendentes = [r for r in kernel.approvals.list("pending") if r.run_id == primeiro.run_id]
    assert pendentes == [], "a mesma aprovação foi pedida duas vezes"


def test_aprovacao_concedida_vale_tambem_para_o_replan(tmp_path):
    chamadas = [0]

    def gate(req, res):
        chamadas[0] += 1
        return "refaça" if chamadas[0] == 1 else True

    kernel, _, _, _ = _kernel(
        tmp_path, rules=[{"id": "revisar", "operation": "runtime.execute", "action": "ask"}],
        evaluator=Evaluator(gates=[CallableGate("g", gate)], max_replans=1))
    executor = _Exec()
    primeiro = kernel.execute(KernelRequest(task="x"), executor=executor)

    out = kernel.approve(primeiro.approval_id, executor=executor)

    assert out.status == "completed"
    assert len(executor.payloads) == 2


def test_aprovacao_nao_sobrepoe_deny_posterior(tmp_path):
    kernel, _, policy, _ = _kernel(
        tmp_path, rules=[{"id": "revisar", "operation": "runtime.execute", "action": "ask"}])
    executor = _Exec()
    primeiro = kernel.execute(KernelRequest(task="x"), executor=executor)
    policy.rules = [{"id": "fechado", "operation": "runtime.execute", "action": "deny"}]

    out = kernel.approve(primeiro.approval_id, executor=executor)

    assert executor.payloads == []
    assert out.status == "failed"


def test_resume_sem_approval_manager_preserva_a_decisao(tmp_path):
    kernel, _, _, _ = _kernel(
        tmp_path, approvals=False,
        rules=[{"id": "revisar", "operation": "runtime.execute", "action": "ask"}])
    executor = _Exec()
    primeiro = kernel.execute(KernelRequest(task="x"), executor=executor)
    assert primeiro.status == "waiting_approval"

    kernel.resume(primeiro.run_id)
    out = kernel.continue_run(primeiro.run_id, executor=executor)

    assert out.status == "completed"
    assert len(executor.payloads) == 1


def test_execute_sem_retry_nao_reavalia_duas_vezes(tmp_path):
    """A primeira tentativa de execute() acabou de passar pelo preflight:
    reavaliar ali duplicaria o evento policy.evaluated sem proteger nada."""
    kernel, _, _, _ = _kernel(tmp_path)
    out = kernel.execute(KernelRequest(task="x"), executor=_Exec())
    eventos = [e.event_type for e in kernel.runs.event_bus.list_events(run_id=out.run_id)]
    assert out.status == "completed"
    assert eventos.count("policy.evaluated") == 1
