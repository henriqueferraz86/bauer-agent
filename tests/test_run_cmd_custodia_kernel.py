"""O `bauer run` passa a ter custódia do Kernel — os gates valem no /loop.

Medido no S7 (docs/harness/EXECUTION_PATHS.md): o `bauer run` entrava por
`kernel.admit()` (admissão SEM custódia) e fechava o run chamando
`kernel.runs.complete_run()` à mão. Resultado: no caminho MAIS autônomo do
Bauer — onde o agente escreve código sem ninguém olhando — o Evaluator nunca
rodava. Prova, dois runs no mesmo store com `evaluator_enabled: true`:

    bauer run  (admit)    ... running -> completed               # gate NAO rodou
    /chat      (execute)  ... running -> evaluating -> completed  # gate rodou

Aqui o laço de rodadas vira EXECUTOR de `kernel.execute()`: quem declara
`completed` é o Kernel, depois dos gates.
"""

from __future__ import annotations

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.kernel.evaluator import Evaluator
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore


def _kernel(tmp_path, *, evaluator=None, control=None):
    store = JsonlStateStore(str(tmp_path / "runtime"))
    bus = EventBus(store=store)
    return BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus,
                       evaluator=evaluator, control=control)


def _traj(kernel, run_id):
    """Trajetória de status a partir do que foi persistido."""
    seen = []
    for rec in kernel.runs.store.list("runs"):
        if (rec.get("id") or rec.get("run_id")) == run_id:
            s = rec.get("status")
            if not seen or seen[-1] != s:
                seen.append(s)
    return seen


def _req():
    return KernelRequest(task="fazer X", agent_id="cli.run",
                         input={"endpoint": "bauer run"})


# ─── o gate agora roda no caminho autônomo ───────────────────────────────────


def test_evaluating_aparece_na_trajetoria(tmp_path):
    """O que faltava: `bauer run` chegando em `evaluating`."""
    k = _kernel(tmp_path, evaluator=Evaluator())
    out = k.execute(_req(), executor=lambda p: {"output": "feito", "tool_calls_count": 3})

    assert out.status == "completed"
    assert "evaluating" in _traj(k, out.run_id)


def test_gate_reprovado_derruba_completed_do_laco(tmp_path):
    """O laço achou que terminou; o gate discorda e o Kernel vence."""
    k = _kernel(tmp_path, evaluator=Evaluator(max_replans=0))
    out = k.execute(_req(), executor=lambda p: {"output": "   "})  # vazio

    assert out.status == "failed"
    assert "non_empty_output" in (out.error or "")


def test_replan_reentrega_feedback_ao_laco(tmp_path):
    """Gate reprovado com orçamento -> o laço roda de novo COM o motivo."""
    k = _kernel(tmp_path, evaluator=Evaluator(max_replans=1))
    feedbacks = []

    def _exec(payload):
        feedbacks.append(payload.get("replan_feedback"))
        # 1ª passada vazia (reprova), 2ª com conteúdo (passa)
        return {"output": "" if len(feedbacks) == 1 else "corrigido"}

    out = k.execute(_req(), executor=_exec)

    assert out.status == "completed"
    assert feedbacks[0] is None, "a primeira passada não tem feedback"
    assert "non_empty_output" in (feedbacks[1] or ""), \
        "a segunda passada recebe o motivo do gate — é o que o laço usa de nudge"


# ─── cancelamento no meio: nem falha, nem run preso ──────────────────────────


def test_executor_cancelado_nao_vira_failed(tmp_path):
    """Kill-switch/Ctrl+C entre rodadas é cancelamento deliberado.

    Sem isto o `bauer run` só podia reportar failed — e `bauer_runs_cancelled_total`
    nunca distinguiria "o usuário mandou parar" de "quebrou".
    """
    k = _kernel(tmp_path, evaluator=Evaluator())
    out = k.execute(_req(), executor=lambda p: {
        "output": "parcial", "status": "cancelled", "error": "runtime kill switch ativo",
    })

    assert out.status == "cancelled"
    assert "kill switch" in (out.error or "")
    assert _traj(k, out.run_id)[-1] == "cancelled"


def test_cancelado_nao_gasta_retry_nem_replan(tmp_path):
    """Ninguém pediu para insistir: cancelamento é terminal na hora."""
    k = _kernel(tmp_path, evaluator=Evaluator(max_replans=3))
    chamadas = []

    def _exec(payload):
        chamadas.append(1)
        return {"output": "", "status": "cancelled", "error": "parou"}

    out = k.execute(_req(), executor=_exec)

    assert out.status == "cancelled"
    assert len(chamadas) == 1, "output vazio + cancelado NÃO deve disparar replan"


def test_cancelado_nao_roda_gate(tmp_path):
    """Output vazio num run cancelado não é falha de qualidade."""
    rodou = []
    ev = Evaluator([type("G", (), {
        "name": "espia",
        "check": lambda self, *, request, result: (rodou.append(1), __import__(
            "bauer.core.kernel.evaluator", fromlist=["GateResult"]
        ).GateResult("espia", True))[1],
    })()])
    k = _kernel(tmp_path, evaluator=ev)
    k.execute(_req(), executor=lambda p: {"output": "", "status": "cancelled"})
    assert rodou == [], "gate não roda em run cancelado"


# ─── governança barrando antes de começar ────────────────────────────────────


def test_kill_switch_no_preflight_nao_chama_executor(tmp_path):
    """Distingue 'barrado antes de começar' de 'rodou e terminou assim' — é o que
    o rótulo `bloqueado` do resumo do CLI usa."""
    class _Kill:
        def kill_switch_enabled(self):
            return True

    k = _kernel(tmp_path, evaluator=Evaluator(), control=_Kill())
    chamou = []
    out = k.execute(_req(), executor=lambda p: chamou.append(1) or {"output": "x"})

    assert out.status == "cancelled"
    assert chamou == [], "executor não roda quando o kill-switch barra no preflight"
