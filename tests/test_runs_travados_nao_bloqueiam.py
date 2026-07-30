"""Run travado não é run paralelo — três Ctrl+C não podem travar o Bauer.

`max_parallel_runs` (default 3) contava TODO run não-terminal do histórico, sem
limite de idade. Consequência medida: três interrupções (Ctrl+C, crash, thread
órfã de SSE) deixavam três runs em `running` para sempre, e a partir daí
**qualquer** execução era negada com

    policy deny: max parallel runs reached: 3/3

até alguém chamar `recover()` à mão. O usuário não tinha como saber disso — a
mensagem fala de paralelismo, e não havia nada rodando.

Encontrado ao ligar o Kernel por default (HARNESS-020): o bloqueio derrubou 4
testes de `bauer run`. Antes disso ficava latente porque o `bauer run` não
passava pelo gate de budget.
"""

from __future__ import annotations

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.runtime.autonomy import BudgetExceededError, BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import JsonlStateStore


def _ambiente(tmp_path, **kw):
    store = JsonlStateStore(str(tmp_path / "runtime"))
    bus = EventBus(store=store)
    return (RunManager(store=store, event_bus=bus),
            BudgetManager(store=store, event_bus=bus, **kw))


def _abrir(runs, n: int, status: str = "running"):
    return [runs.create_run(session_id=f"s{i}", agent_id="cli.run",
                            runtime_adapter="bauer_native", input={}, status=status)
            for i in range(n)]


# ─── o bug ───────────────────────────────────────────────────────────────────


def test_runs_travados_nao_contam_como_paralelos(tmp_path):
    """Cenário exato: 3 processos morreram; o quarto tem de rodar."""
    runs, bm = _ambiente(tmp_path, stale_run_after_s=0)  # tudo já é "velho"
    _abrir(runs, 3)

    bm.ensure_can_start(agent_id="cli.run", company_id=None, estimated_cost_usd=0.0)


def test_runs_vivos_continuam_barrando(tmp_path):
    """Regressão: o limite de paralelismo tem de continuar valendo de verdade."""
    runs, bm = _ambiente(tmp_path)  # limiar default (900s) — os runs são novos
    _abrir(runs, 3)

    with pytest.raises(BudgetExceededError, match="max parallel runs"):
        bm.ensure_can_start(agent_id="cli.run", company_id=None, estimated_cost_usd=0.0)


def test_abaixo_do_teto_passa(tmp_path):
    runs, bm = _ambiente(tmp_path)
    _abrir(runs, 2)

    bm.ensure_can_start(agent_id="cli.run", company_id=None, estimated_cost_usd=0.0)


# ─── o que NÃO pode mudar junto ──────────────────────────────────────────────


def test_terminais_nunca_contaram(tmp_path):
    runs, bm = _ambiente(tmp_path)
    for r in _abrir(runs, 3):
        runs.complete_run(r.id, output={})

    bm.ensure_can_start(agent_id="cli.run", company_id=None, estimated_cost_usd=0.0)


def test_limiar_e_o_mesmo_do_recover(tmp_path):
    """O corte tem de casar com quem de fato marca o run como failed.

    Se o BudgetManager parasse de contar antes de o RuntimeRecovery agir, haveria
    uma janela em que o run não conta nem foi recuperado — invisível dos dois
    lados.
    """
    from bauer.core.runtime.resilience import RuntimeRecovery
    import inspect

    padrao_recover = inspect.signature(
        RuntimeRecovery.recover_stuck_runs).parameters["max_age_s"].default
    assert BudgetManager.stale_run_after_s == padrao_recover


def test_timestamp_ilegivel_conta(tmp_path):
    """Lado seguro: não dá para provar que está travado, então é paralelo."""
    runs, bm = _ambiente(tmp_path, stale_run_after_s=0)
    abertos = _abrir(runs, 3)
    store = JsonlStateStore(str(tmp_path / "runtime"))
    for r in abertos:
        store.upsert("runs", {**store.latest("runs", r.id), "updated_at": "nao-e-data",
                              "started_at": None, "created_at": None})

    with pytest.raises(BudgetExceededError, match="max parallel runs"):
        bm.ensure_can_start(agent_id="cli.run", company_id=None, estimated_cost_usd=0.0)
