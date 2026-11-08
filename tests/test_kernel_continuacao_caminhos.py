"""K08 nos caminhos reais que continuam um run admitido noutra thread.

O ``/loop`` da web e o ``/loop`` do Telegram admitem no handler e continuam
numa thread (``continue_governed``). Entre os dois momentos o operador pode
ligar o kill-switch; antes desta correção a thread rodava o laço inteiro
assim mesmo. ``bauer run`` e o executor Agno entram por ``run_governed`` →
``execute`` e são cobertos pelos testes de retry/replan de
``test_kernel_revalidacao_governanca.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from bauer import channel_execution, telegram_loop
from bauer.core.kernel import BauerKernel
from bauer.core.runtime.resilience import RuntimeControl
from bauer.core.runtime.run_manager import RunManager
from tests.test_serve_loop import _app, _cfg, _wait_done
from tests.test_telegram_loop import (  # noqa: F401 — fixture autouse
    _isolated_delivery_registry,
    backend_at,
    install_kernel,
    message,
    wait_for_delivery,
)


def _liga_kill_switch_apos_admitir(monkeypatch):
    original = BauerKernel.admit

    def admit(self, request):
        run, early = original(self, request)
        if early is None and self.control is not None:
            self.control.set_kill_switch(True)
        return run, early

    monkeypatch.setattr(BauerKernel, "admit", admit)


def _conta_laco(monkeypatch) -> list[int]:
    """O executor de ambos os /loop é o laço ``run_loop_rounds``. O próprio laço
    também confere o kill-switch entre rodadas — por isso o critério aqui é
    o Kernel nem entregar a custódia ao executor, não só zero rodadas."""
    import bauer.serve_loop as serve_loop

    chamadas: list[int] = []
    original = serve_loop.run_loop_rounds

    def contado(*a, **k):
        chamadas.append(1)
        return original(*a, **k)

    monkeypatch.setattr(serve_loop, "run_loop_rounds", contado)
    return chamadas


def test_web_loop_kill_switch_entre_admissao_e_worker(tmp_path: Path, monkeypatch):
    _liga_kill_switch_apos_admitir(monkeypatch)
    laco = _conta_laco(monkeypatch)
    client = MagicMock()
    client._provider = "openrouter"
    tc = TestClient(_app(tmp_path, _cfg(tmp_path), client))

    resp = tc.post("/loop", json={"message": "não deve rodar"})
    assert resp.status_code == 200
    final = _wait_done(tc, resp.json()["run_id"])

    assert final["state"] == "stopped"
    assert final["rounds"] == 0
    assert laco == [], "o Kernel continuou o run com o kill-switch ligado"
    client.chat_stream.assert_not_called()
    client.chat.assert_not_called()


def test_telegram_loop_kill_switch_entre_admissao_e_worker(tmp_path: Path, monkeypatch):
    backend = backend_at(tmp_path)
    runs = RunManager(root=tmp_path / "runtime")
    kernel = BauerKernel(runs=runs, control=RuntimeControl(store=runs.store),
                         heartbeat_interval_s=0)
    install_kernel(backend, kernel)
    _liga_kill_switch_apos_admitir(monkeypatch)
    laco = _conta_laco(monkeypatch)
    turnos: list[int] = []

    def _turn(*a, **k):
        turnos.append(1)
        return "trabalho", []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", _turn)
    recebidos: list[tuple[str, str]] = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: recebidos.append((sid, text)) or True)

    reply = telegram_loop.start_loop(backend, message(), update_id=7, goal="faça algo")
    assert "iniciado" in reply.lower()
    wait_for_delivery(recebidos)

    assert turnos == []
    assert laco == [], "o Kernel continuou o run com o kill-switch ligado"
    _, texto = recebidos[0]
    assert "stop_reason=kill_switch" in texto
    record = telegram_loop._store_for(backend).get_by_update_id(7)
    assert record.state == "stopped"
    assert runs.get_run(record.run_id).status == "cancelled"
