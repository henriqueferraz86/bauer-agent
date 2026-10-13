"""Timeout do /stream não pode apagar um run que já concluiu.

Encontrado por uma FALHA DE CI (2026-07-30, merge do `--local`), não por
inspeção. O teste
`test_stream_timeout_worker_persists_full_history_after_background_completes`
reprovou com o run em `failed` — e passa 5/5 na máquina local, inclusive em
paralelo. A tentação era classificar como flake e seguir.

Não é flake. É corrida com dois escritores no mesmo run:

    gerador SSE   timeout  -> fail_run("stream turn timeout")
    _worker       fim real -> complete_run(...)

O `_worker` já tinha guarda, mas só para `cancelled`. Quando a thread órfã do
turno termina ANTES de o timeout chegar ao seu ponto de checagem, o `fail_run`
chega depois e apaga o `completed`. Na máquina local a thread costuma perder a
corrida; no runner do CI, carregado, ela ganha — e o desfecho passa a ser
decidido por escalonamento.

Desfecho decidido por velocidade de máquina não é desfecho. É a mesma família
do `cancelled` sendo sobrescrito por `failed` que o Kernel corrigiu com
`_fail_se_nao_terminal`; este caminho usa `run_manager` direto (é `admit()`,
sem custódia) e ficou de fora daquela correção.
"""

from __future__ import annotations

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.runtime.run_manager import TERMINAL_RUN_STATUSES, RunManager
from bauer.core.runtime.state_store import JsonlStateStore


@pytest.fixture
def runs(tmp_path):
    store = JsonlStateStore(str(tmp_path / "rt"))
    return RunManager(store=store, event_bus=EventBus(store=store))


def _run(runs):
    return runs.create_run(session_id="s", agent_id="serve.stream")


# ─── a corrida ───────────────────────────────────────────────────────────────


def test_falha_nao_apaga_conclusao(runs):
    """O caso do CI: o trabalho terminou, o timeout chegou depois."""
    r = _run(runs)
    runs.start_run(r.id)
    runs.complete_run(r.id, output={"response": "pronto"})

    runs.fail_run_se_nao_terminal(r.id, "stream turn timeout")

    assert runs.get_run(r.id).status == "completed", (
        "timeout que chega depois do resultado apagou um trabalho concluído")


def test_falha_nao_apaga_cancelamento(runs):
    """Cancelar é decisão de quem manda; falha é consequência. A decisão vence —
    mesma regra do `_fail_se_nao_terminal` do Kernel."""
    r = _run(runs)
    runs.start_run(r.id)
    runs.cancel_run(r.id)

    runs.fail_run_se_nao_terminal(r.id, "stream turn timeout")

    assert runs.get_run(r.id).status == "cancelled"


def test_a_PRIMEIRA_falha_vale(runs):
    """Proteger terminal não pode virar "nunca falha": run em andamento falha
    normalmente, e a mensagem da primeira é a que fica."""
    r = _run(runs)
    runs.start_run(r.id)

    runs.fail_run_se_nao_terminal(r.id, "motivo original")
    runs.fail_run_se_nao_terminal(r.id, "motivo posterior")

    atual = runs.get_run(r.id)
    assert atual.status == "failed"
    assert "original" in (atual.error or ""), (
        "a segunda falha reescreveu o motivo — a causa raiz é a primeira")


@pytest.mark.parametrize("terminal", sorted(TERMINAL_RUN_STATUSES))
def test_todo_terminal_e_protegido(terminal, runs):
    """Varre os três de uma vez: se alguém acrescentar um status terminal novo,
    este teste cobre sozinho."""
    r = _run(runs)
    runs.start_run(r.id)
    runs.update_run(r.id, status=terminal)

    runs.fail_run_se_nao_terminal(r.id, "tarde demais")

    assert runs.get_run(r.id).status == terminal


def test_run_inexistente_levanta(runs):
    """Silenciar aqui esconderia bug de wiring — id errado tem de aparecer."""
    with pytest.raises(KeyError):
        runs.fail_run_se_nao_terminal("run-que-nao-existe", "x")


def test_fail_run_preserva_primeiro_terminal(runs):
    """K02: todos os call sites preservam o primeiro terminal em transação."""
    r = _run(runs)
    runs.start_run(r.id)
    runs.complete_run(r.id, output={})

    runs.fail_run(r.id, "forçado")

    assert runs.get_run(r.id).status == "completed"


# ─── o call site ─────────────────────────────────────────────────────────────


def test_timeout_do_observador_preserva_custodia_do_worker(tmp_path, monkeypatch):
    """A conexão corta; o worker segue running, persiste e só então conclui."""
    from threading import Event
    import time
    from fastapi.testclient import TestClient
    from bauer.server import create_app
    from bauer.session_store import SessionStore
    from bauer.tool_router import ToolRouter

    entered, release = Event(), Event()

    def turn(ctx, *_a, **_k):
        entered.set()
        assert release.wait(5)
        ctx.add_assistant("final completo")
        return "final completo", []

    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    monkeypatch.setattr("bauer.server._STREAM_TURN_TIMEOUT_SECONDS", 0.02)
    app = create_app(model_name="controlled", client=object(), applied_context=4096,
                     router=ToolRouter(workspace=tmp_path), system_prompt="",
                     sessions_dir=tmp_path / "sessions", rate_limit_requests=0)
    manager = RunManager(root=tmp_path / "runtime")
    with TestClient(app) as client:
        try:
            response = client.get("/stream", params={"message": "controlled", "session_id": "same"})
            assert response.status_code == 200
            assert entered.is_set()
            run = manager.list_runs()[0]
            assert run.status == "running"
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while manager.get_run(run.id).status == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert manager.get_run(run.id).status == "completed"
        assert SessionStore(tmp_path / "sessions").load("same")[-1]["content"] == "final completo"
