"""Sprint 4 do plano de consolidação — o último item que faltava dele.

O plano pedia "`retry_utils.py` + `circuit_breaker.py` no laço do Kernel". Retry
com backoff, estado `retrying` auditável, fallback de executor e recuperação
pós-restart estavam todos lá; o `circuit_breaker.py` existia no repo com **zero
referências** em `bauer/core/` — nunca foi ligado.

O que faltava na prática: o Kernel re-tentava e fazia fallback, mas não parava de
tentar um provider que cai sistematicamente. Cada run redescobria a indisponibilidade
por conta própria, pagando o timeout inteiro para chegar à mesma conclusão.
"""

from __future__ import annotations

import pytest

from bauer.circuit_breaker import CircuitBreaker
from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore


class _Adapter:
    def __init__(self, nome: str, *, falha: bool = False):
        self.name = nome
        self.falha = falha
        self.chamadas = 0

    def run_agent(self, payload):
        self.chamadas += 1
        if self.falha:
            raise RuntimeError(f"{self.name} fora do ar")
        return {"output": f"ok via {self.name}"}


def _kernel(tmp_path, adapters: dict, *, breaker=None):
    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    return BauerKernel(
        runs=RunManager(store=store, event_bus=bus), bus=bus,
        adapter_factory=lambda nome, config=None: adapters[nome or "principal"],
        breaker=breaker,
    )


def _req(**kw):
    return KernelRequest(agent_id="t", operation="runtime.execute", input={}, **kw)


# ─── o circuito abre e para de insistir ──────────────────────────────────────


def test_provider_que_cai_para_de_ser_tentado(tmp_path):
    """O ponto todo: depois de N falhas, o run seguinte NÃO chama o adapter."""
    ruim = _Adapter("principal", falha=True)
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=60.0)
    k = _kernel(tmp_path, {"principal": ruim}, breaker=cb)

    for _ in range(3):
        k.execute(_req())
    chamadas_ate_abrir = ruim.chamadas

    assert cb.is_open("principal"), "3 falhas deveriam abrir o circuito"

    k.execute(_req())

    assert ruim.chamadas == chamadas_ate_abrir, \
        "com o circuito aberto o adapter nao pode ser chamado de novo"


def test_sucesso_fecha_o_circuito(tmp_path):
    """Indisponibilidade curta não pode virar fallback permanente."""
    adapter = _Adapter("principal", falha=True)
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=60.0)
    k = _kernel(tmp_path, {"principal": adapter}, breaker=cb)

    k.execute(_req())
    adapter.falha = False
    k.execute(_req())

    assert not cb.is_open("principal")
    assert cb.failure_count("principal") == 0


def test_fallback_com_circuito_aberto_e_pulado(tmp_path):
    """Se o fallback também está aberto, ir nele custa o timeout à toa."""
    ruim = _Adapter("principal", falha=True)
    fb_ruim = _Adapter("fb1", falha=True)
    fb_bom = _Adapter("fb2")
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=60.0)
    cb.record_failure("fb1")   # fb1 ja se mostrou ruim em runs anteriores
    k = _kernel(tmp_path, {"principal": ruim, "fb1": fb_ruim, "fb2": fb_bom}, breaker=cb)

    out = k.execute(_req(fallback_adapters=["fb1", "fb2"]))

    assert out.status == "completed"
    assert fb_ruim.chamadas == 0, "fallback com circuito aberto foi tentado"
    assert fb_bom.chamadas == 1


# ─── o que NÃO pode contar contra o circuito ─────────────────────────────────


def test_cancelamento_nao_abre_circuito(tmp_path):
    """Kill-switch e Ctrl+C são decisão do usuário — o executor não falhou.

    Contar cancelamento como falha faria três interrupções seguidas derrubarem
    o provider, que é o mesmo erro de categoria que travou o Bauer quando runs
    parados contavam como paralelos.
    """
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=60.0)
    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    k = BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus, breaker=cb)

    for _ in range(3):
        k.execute(_req(), executor=lambda p: {"status": "cancelled", "error": "parou"})

    assert not cb.is_open("bauer_native")


def test_executor_injetado_nao_entra_no_circuito(tmp_path):
    """Executor in-process é o laço de turno do próprio Bauer, não um provider.

    Abrir circuito contra ele puniria o Bauer pelo erro do MODELO — e o
    resultado seria o agente parar de rodar depois de algumas tarefas difíceis.
    """
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=60.0)
    store = JsonlStateStore(str(tmp_path / "rt"))
    bus = EventBus(store=store)
    k = BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus, breaker=cb)

    def _falha(payload):
        raise RuntimeError("modelo se perdeu")

    for _ in range(4):
        k.execute(_req(), executor=_falha)

    assert cb.status_all() == {}, "executor injetado nao pode abrir circuito"


# ─── compatibilidade ─────────────────────────────────────────────────────────


def test_sem_breaker_comportamento_inalterado(tmp_path):
    """Kernel montado à mão sem breaker continua re-tentando como antes."""
    ruim = _Adapter("principal", falha=True)
    k = _kernel(tmp_path, {"principal": ruim})   # breaker=None

    for _ in range(4):
        k.execute(_req())

    assert ruim.chamadas == 4, "sem breaker, todo run tenta"


def test_build_kernel_traz_breaker_por_default(tmp_path):
    from bauer.core.kernel import build_kernel

    k = build_kernel(root=str(tmp_path / "rt"), workspace=str(tmp_path / "ws"))

    assert k.breaker is not None
    assert k.breaker._failure_threshold >= 3, (
        "threshold baixo transformaria indisponibilidade curta em fallback permanente")
