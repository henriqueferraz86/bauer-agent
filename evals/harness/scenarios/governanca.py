"""Cenários 1–10, 18–20: o harness conclui, barra e cancela pelos motivos certos.

O núcleo da suíte. Vários destes marcam ``falso_sucesso=True``: falhar neles
significa que o harness declarou sucesso sem base — é a métrica que o plano
persegue e que não existia forma de medir antes.
"""

from __future__ import annotations

from ..runner import cenario
from ._base import GatePlantado, kernel_temp, pedido, trajetoria


@cenario("1. tarefa simples conclui", critico=True)
def tarefa_simples():
    k, _ = kernel_temp()
    r = k.execute(pedido(), executor=lambda p: {"output": "feito"})
    return r.status == "completed", f"status={r.status}"


@cenario("2. teste falhando impede conclusão", critico=True, falso_sucesso=True)
def teste_falhando_bloqueia():
    k, _ = kernel_temp(gates=[GatePlantado("tests", False, "2 failed")])
    r = k.execute(pedido(), executor=lambda p: {"output": "terminei!"})
    return r.status == "failed" and "tests" in (r.error or ""), \
        f"status={r.status} erro={r.error!r}"


@cenario("3. LLM diz que terminou sem produzir nada", critico=True, falso_sucesso=True)
def output_vazio_nao_conclui():
    from bauer.core.kernel.evaluator import Evaluator

    k, _ = kernel_temp(evaluator=Evaluator(max_replans=0))
    r = k.execute(pedido(), executor=lambda p: {"output": "   "})
    return r.status == "failed", f"status={r.status}"


@cenario("4. alteração fora do escopo reprova", critico=True, falso_sucesso=True)
def fora_do_escopo():
    from bauer.core.kernel.gates.scope import ScopeGate
    from bauer.core.task import TaskContract
    from ._base import repo_temp

    repo = repo_temp()
    (repo / "proibido.py").write_text("X = 1\n", encoding="utf-8")
    contrato = TaskContract.from_mapping({"scope": {"allowed": ["app.py"]}})
    k, _ = kernel_temp(gates=[ScopeGate(repo, contrato)])
    r = k.execute(pedido(), executor=lambda p: {"output": "pronto"})
    return r.status == "failed" and "escopo" in (r.error or ""), \
        f"status={r.status} erro={(r.error or '')[:80]!r}"


@cenario("5. segredo no diff reprova", falso_sucesso=True)
def segredo_no_diff():
    from bauer.secrets_scanner import has_secrets, scan

    texto = 'OPENAI_KEY = "sk-abcdefghijklmnopqrstuvwx1234"'
    resultado = scan(texto)
    return has_secrets(texto) and resultado.found, f"matches={len(resultado.matches)}"


@cenario("6. ferramenta falha temporariamente e o retry salva", critico=True)
def retry_recupera():
    k, _ = kernel_temp()
    tentativas = []

    def _exec(p):
        tentativas.append(1)
        if len(tentativas) == 1:
            raise RuntimeError("falha transitória")
        return {"output": "ok na segunda"}

    r = k.execute(pedido(max_retries=1), executor=_exec)
    return r.status == "completed" and len(tentativas) == 2, \
        f"status={r.status} tentativas={len(tentativas)}"


@cenario("7. executor falha de vez e o run falha (sem falso sucesso)",
         critico=True, falso_sucesso=True)
def falha_definitiva():
    k, _ = kernel_temp()

    def _exec(p):
        raise RuntimeError("provider fora do ar")

    r = k.execute(pedido(), executor=_exec)
    return r.status == "failed" and "fora do ar" in (r.error or ""), \
        f"status={r.status}"


@cenario("8. run exige aprovação e para em waiting_approval", critico=True)
def exige_aprovacao():
    class _Policy:
        def evaluate(self, op, payload):
            class D:
                action, reason, risk_level, matched_rules = "ask", "risco alto", "high", []
            return D()

    k, _ = kernel_temp(policy=_Policy())
    chamou = []
    r = k.execute(pedido(), executor=lambda p: chamou.append(1) or {"output": "x"})
    return r.status == "waiting_approval" and not chamou, \
        f"status={r.status} executor_rodou={bool(chamou)}"


@cenario("9. aprovação negada impede execução", critico=True)
def aprovacao_negada():
    class _Policy:
        def evaluate(self, op, payload):
            class D:
                action, reason, risk_level, matched_rules = "deny", "negado", "high", []
            return D()

    k, _ = kernel_temp(policy=_Policy())
    chamou = []
    r = k.execute(pedido(), executor=lambda p: chamou.append(1) or {"output": "x"})
    return r.status == "failed" and not chamou, \
        f"status={r.status} executor_rodou={bool(chamou)}"


@cenario("10. kill switch barra antes do LLM", critico=True)
def kill_switch():
    class _Kill:
        def kill_switch_enabled(self):
            return True

    k, _ = kernel_temp(control=_Kill())
    chamou = []
    r = k.execute(pedido(), executor=lambda p: chamou.append(1) or {"output": "x"})
    return r.status == "cancelled" and not chamou, \
        f"status={r.status} executor_rodou={bool(chamou)}"


@cenario("18. cancelamento do usuário não vira falha", critico=True)
def cancelamento_e_terminal():
    k, _ = kernel_temp(gates=[GatePlantado("qualquer", False, "nao deveria rodar")])
    r = k.execute(pedido(), executor=lambda p: {
        "output": "parcial", "status": "cancelled", "error": "cancelado pelo usuário"})
    return r.status == "cancelled", f"status={r.status}"


@cenario("19. gate reprova e o replan devolve o motivo ao executor", critico=True)
def replan_com_feedback():
    from bauer.core.kernel.evaluator import Evaluator

    vistos = []

    class _Gate:
        name = "exigente"

        def check(self, *, request, result):
            from bauer.core.kernel.evaluator import GateResult
            ok = len(vistos) > 1
            return GateResult(self.name, ok, "" if ok else "faltou X")

    k, _ = kernel_temp(evaluator=Evaluator([_Gate()], max_replans=1))

    def _exec(p):
        vistos.append(p.get("replan_feedback"))
        return {"output": "tentativa"}

    r = k.execute(pedido(), executor=_exec)
    return (r.status == "completed" and len(vistos) == 2
            and "faltou X" in (vistos[1] or "")), f"status={r.status} vistos={vistos}"


@cenario("20. run concluído registra custo e contagem de tools")
def contabilidade_do_run():
    k, _ = kernel_temp()
    r = k.execute(pedido(), executor=lambda p: {
        "output": "ok", "tool_calls_count": 7, "cost_estimate": 0.42})
    run = k.runs.get_run(r.run_id)
    return (run.tool_calls_count == 7 and abs((run.cost_estimate or 0) - 0.42) < 1e-6), \
        f"tools={run.tool_calls_count} custo={run.cost_estimate}"
