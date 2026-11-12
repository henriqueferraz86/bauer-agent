"""Sprint 79: o operador pode tirar o limite de iterações e o de custo.

Pedido em 2026-11-12, depois de um /loop parar com "[Limite de iterações
atingido]" (teto escondido de 200 chamadas ao modelo): "não quero limite de
interações nem de custo".
"""

from __future__ import annotations

import pytest

from bauer import agent
from bauer.agent_loop_config import UNLIMITED_TOOL_TURNS, apply_max_tool_turns, resolve_max_tool_turns
from bauer.core.runtime.autonomy import AutonomyProfile, BudgetManager


def _config(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    if "model:" not in body:
        body = "model:\n  provider: ollama\n  name: fake\n" + body
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_zero_max_tool_turns_means_no_per_turn_cap(tmp_path):
    path = _config(tmp_path, "tools:\n  max_tool_turns: 0\n")
    assert resolve_max_tool_turns(path) == UNLIMITED_TOOL_TURNS


def test_gateway_and_serve_apply_the_configured_turn_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "MAX_TOOL_TURNS", 150)
    path = _config(tmp_path, "tools:\n  max_tool_turns: 0\n")
    assert apply_max_tool_turns(path) == UNLIMITED_TOOL_TURNS
    assert agent.MAX_TOOL_TURNS == UNLIMITED_TOOL_TURNS
    path = _config(tmp_path, "tools:\n  max_tool_turns: 40\n")
    assert apply_max_tool_turns(path) == 40


def test_channel_backend_applies_turn_cap_on_initialize(tmp_path, monkeypatch):
    from bauer.channel_base import AgentBackend

    monkeypatch.setattr(agent, "MAX_TOOL_TURNS", 150)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    path = _config(tmp_path, f"agent:\n  workspace: {workspace.as_posix()}\n"
                             "model:\n  provider: ollama\n  name: fake\n"
                             "tools:\n  max_tool_turns: 0\n")
    backend = AgentBackend(path, tmp_path / "memory" / "sessions")
    backend._build_client_fn = lambda: (lambda cfg: object())
    try:
        backend.initialize()
    except Exception:  # noqa: BLE001 — só interessa o teto aplicado antes do client
        pass
    assert agent.MAX_TOOL_TURNS == UNLIMITED_TOOL_TURNS


def test_daily_budget_can_be_unlimited(tmp_path):
    manager = BudgetManager(root=tmp_path / "runtime")
    profile = manager.set_profile(daily_budget_usd=None, max_tool_calls_per_run=0,
                                  max_runtime_s_per_run=0)
    assert profile.daily_budget_usd is None
    status = manager.status()
    assert status["daily"]["limit_usd"] is None and status["daily"]["exceeded"] is False
    manager.ensure_can_start(agent_id="a", estimated_cost_usd=1_000_000.0)


def test_negative_daily_budget_is_still_rejected():
    with pytest.raises(ValueError):
        AutonomyProfile(daily_budget_usd=-1.0).validate()
