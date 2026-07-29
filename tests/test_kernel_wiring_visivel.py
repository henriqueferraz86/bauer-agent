"""Governança pedida e não entregue tem de estourar, não sumir no log.

Todo call site do Kernel embrulhava `build_kernel` em
`except Exception: log_suppressed(...)`. Com `kernel.enabled: true` isso
degradava para execução INGOVERNADA em silêncio: o log é gravado, mas o run
prossegue como se a flag estivesse desligada.

Consequência medida no S7: era impossível responder "quantos % dos runs passam
pelo Kernel?" — o número media o que o wiring conseguiu montar, não o que a
config pediu. Daí `require_kernel`.
"""

from __future__ import annotations

import pytest

from bauer.core.kernel import KernelWiringError, require_kernel


class _Cfg:
    def __init__(self, enabled: bool):
        self.kernel = type("K", (), {"enabled": enabled})()


# ─── semântica da flag ───────────────────────────────────────────────────────


def test_flag_desligada_devolve_none_sem_construir():
    """Caminho legado intocado: nem chama o build."""
    chamou = []
    out = require_kernel(_Cfg(False), lambda: chamou.append(1), label="teste")
    assert out is None
    assert chamou == [], "build_fn não deve ser chamado com a flag desligada"


def test_flag_ligada_devolve_o_kernel_construido():
    sentinela = object()
    assert require_kernel(_Cfg(True), lambda: sentinela, label="teste") is sentinela


def test_flag_ligada_com_wiring_quebrado_estoura():
    """O coração da mudança: antes isso devolvia None e o run seguia."""
    def _explode():
        raise ValueError("policy root inacessível")

    with pytest.raises(KernelWiringError) as exc:
        require_kernel(_Cfg(True), _explode, label="bauer run")

    msg = str(exc.value)
    assert "bauer run" in msg, "a mensagem precisa dizer QUAL call site falhou"
    assert "policy root inacessível" in msg, "a causa original tem de aparecer"
    assert isinstance(exc.value.__cause__, ValueError), "encadeia a exceção original"


def test_flag_desligada_nao_estoura_mesmo_com_build_quebrado():
    """Quem não pediu Kernel não paga pelo wiring dele."""
    def _explode():
        raise ValueError("nunca deveria rodar")

    assert require_kernel(_Cfg(False), _explode, label="teste") is None


def test_config_sem_secao_kernel_nao_estoura():
    """Config antigo/parcial: ausência de seção = desligado, não erro."""
    assert require_kernel(object(), lambda: pytest.fail("não chamar"),
                          label="teste") is None


# ─── admit() devolve estado real, não snapshot de created ────────────────────


def test_admit_devolve_run_em_queued(tmp_path):
    """A docstring de admit() promete `queued`; o objeto dizia `created`.

    `_open_run` devolve o snapshot de criação e `_preflight` transiciona até
    queued NO STORE — devolver o objeto velho fazia `run.status` contradizer o
    estado persistido. O /stream só usava `run.id`, então nunca quebrou: era
    armadilha latente, e este PR trata exatamente de o harness não mentir sobre
    o próprio estado.
    """
    from bauer.core.kernel import BauerKernel, KernelRequest
    from bauer.core.events.bus import EventBus
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore

    store = JsonlStateStore(str(tmp_path / "runtime"))
    bus = EventBus(store=store)
    kernel = BauerKernel(runs=RunManager(store=store, event_bus=bus), bus=bus)

    run, early = kernel.admit(KernelRequest(agent_id="t", operation="runtime.execute",
                                            input={}))

    assert early is None, "sem policy não há barreira de governança"
    assert run.status == "queued"
    assert kernel.runs.get_run(run.id).status == "queued", "objeto e store concordam"
