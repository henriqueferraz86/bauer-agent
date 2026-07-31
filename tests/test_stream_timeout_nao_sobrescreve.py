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


def test_fail_run_cru_continua_sobrescrevendo(runs):
    """A versão sem guarda é MANTIDA de propósito: há caminhos onde forçar
    `failed` é correto (recuperação pós-restart marca run preso). Trocar o
    comportamento de `fail_run` global mudaria esses call sites sem que ninguém
    pedisse."""
    r = _run(runs)
    runs.start_run(r.id)
    runs.complete_run(r.id, output={})

    runs.fail_run(r.id, "forçado")

    assert runs.get_run(r.id).status == "failed"


# ─── o call site ─────────────────────────────────────────────────────────────


def test_o_stream_usa_a_versao_protegida():
    """Ratchet: o `/stream` tem DOIS pontos de timeout e os dois precisam da
    guarda. Um só protegido deixaria a corrida viva pela metade."""
    import re
    from pathlib import Path

    fonte = (Path(__file__).resolve().parent.parent
             / "bauer" / "server.py").read_text(encoding="utf-8")

    protegidos = fonte.count("fail_run_se_nao_terminal(")
    crus = len(re.findall(r'fail_run\(\s*run\.id,\s*"stream turn timeout"', fonte))

    assert protegidos >= 2, f"só {protegidos} ponto(s) de timeout protegido(s)"
    assert crus == 0, "timeout do /stream voltou a usar fail_run sem guarda"
