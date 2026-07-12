"""Modo autônomo no serve (/loop da UI web) — motor de rodadas + endpoints.

Motor (bauer/serve_loop.py): mesma semântica de conclusão da CLI — rodada
só-texto é candidata a "terminei", nudge de confirmação, segunda só-texto
confirma. Endpoints: POST /loop (background, governado), GET /loop/{id},
POST /loop/{id}/stop, GET /loops.
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.autonomous_budget import AutonomousBudget
from bauer.serve_loop import LOOP_CONFIRM_NUDGE, run_loop_rounds


class _Ctx:
    def __init__(self):
        self.messages: list[dict] = []

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})


def _budget(**kw):
    return AutonomousBudget(max_cost_usd=kw.get("cost", 10.0),
                            max_wall_seconds=kw.get("secs", 300),
                            max_tool_calls=kw.get("tools", 100))


# ─── motor de rodadas (puro) ─────────────────────────────────────────────────


def test_two_textonly_rounds_complete():
    """Rodada só-texto → nudge → segunda só-texto → completed (regra da CLI)."""
    ctx = _Ctx()
    turns = iter([("terminei a tarefa", []), ("confirmo: completa", [])])
    stop, rounds, last, tools = run_loop_rounds(
        goal="faça X", ctx=ctx, turn_fn=lambda: next(turns), budget=_budget())
    assert stop == "completed" and rounds == 2
    assert last == "confirmo: completa" and tools == []
    # o nudge foi injetado entre as rodadas
    assert any(LOOP_CONFIRM_NUDGE in m["content"] for m in ctx.messages)


def test_tool_round_resets_completion_candidate():
    """texto → nudge → rodada COM tools (ainda trabalhando) → texto → nudge →
    texto → completed. Só duas só-texto CONSECUTIVAS encerram."""
    ctx = _Ctx()
    turns = iter([
        ("vou começar", []),
        ("criei o arquivo", [{"tool": "write_file"}]),
        ("pronto", []),
        ("confirmo", []),
    ])
    stop, rounds, _, tools = run_loop_rounds(
        goal="faça X", ctx=ctx, turn_fn=lambda: next(turns), budget=_budget())
    assert stop == "completed" and rounds == 4 and len(tools) == 1


def test_budget_tool_calls_stops_loop():
    ctx = _Ctx()

    def _busy():
        return "trabalhando", [{"tool": "t"}] * 5

    stop, rounds, _, _ = run_loop_rounds(
        goal="x", ctx=ctx, turn_fn=_busy, budget=_budget(tools=8))
    assert stop == "budget_exhausted"
    assert rounds == 2  # 5 tools + 5 tools > 8 → o topo da 3ª rodada corta


def test_should_stop_interrupts_between_rounds():
    ctx = _Ctx()
    calls = {"n": 0}

    def _turn():
        calls["n"] += 1
        return "trabalhando", [{"tool": "t"}]

    def _stop():
        return "kill_switch" if calls["n"] >= 2 else None

    stop, rounds, _, _ = run_loop_rounds(
        goal="x", ctx=ctx, turn_fn=_turn, budget=_budget(), should_stop=_stop)
    assert stop == "kill_switch" and rounds == 2  # parou ANTES da 3ª rodada


def test_turn_exception_stops_as_provider_error():
    ctx = _Ctx()

    def _boom():
        raise RuntimeError("429 rate limited")

    stop, rounds, last, _ = run_loop_rounds(
        goal="x", ctx=ctx, turn_fn=_boom, budget=_budget())
    assert stop == "provider_error" and "429" in last


def test_empty_response_stops():
    ctx = _Ctx()
    stop, _, _, _ = run_loop_rounds(
        goal="x", ctx=ctx, turn_fn=lambda: ("", []), budget=_budget())
    assert stop == "empty_response"


def test_max_rounds_hard_cap():
    ctx = _Ctx()
    stop, rounds, _, _ = run_loop_rounds(
        goal="x", ctx=ctx, turn_fn=lambda: ("vai", [{"tool": "t"}]),
        budget=_budget(tools=10_000), max_rounds=3)
    assert stop == "max_rounds" and rounds == 3


def test_on_round_receives_each_round():
    ctx = _Ctx()
    seen: list[int] = []
    turns = iter([("a", [{"tool": "t"}]), ("fim", []), ("confirmo", [])])
    run_loop_rounds(goal="x", ctx=ctx, turn_fn=lambda: next(turns),
                    budget=_budget(), on_round=lambda n, t, tl: seen.append(n))
    assert seen == [1, 2, 3]


# ─── endpoints (integração) ──────────────────────────────────────────────────

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bauer.server import create_app  # noqa: E402
from bauer.tool_router import ToolRouter  # noqa: E402


def _cfg(tmp_path: Path, *, kernel_enabled: bool = True) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""
        model:
          provider: openrouter
          name: deepseek/deepseek-v4-flash
        kernel:
          enabled: {str(kernel_enabled).lower()}
        loop:
          max_minutes: 5
          max_tool_calls: 50
          max_cost_usd: 1.0
        """), encoding="utf-8")
    return p


def _app(tmp_path: Path, cfg: Path, client: MagicMock):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "p.json"), \
         patch("bauer.agent._try_parse_tool", return_value=None):
        return create_app(
            model_name="deepseek/deepseek-v4-flash", applied_context=4096,
            router=ToolRouter(workspace=ws), client=client, system_prompt="s",
            sessions_dir=tmp_path / "sessions", api_key="", rate_limit_requests=0,
            config_path=cfg,
        )


def _wait_done(tc: TestClient, run_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = tc.get(f"/loop/{run_id}").json()
        if body["state"] != "running":
            return body
        time.sleep(0.1)
    raise AssertionError(f"loop {run_id} não terminou em {timeout_s}s: {body}")


def test_loop_endpoint_runs_to_completion(tmp_path: Path):
    cfg = _cfg(tmp_path)
    client = MagicMock()
    responses = iter(["terminei a tarefa", "confirmo: completa"])
    client.chat_stream.side_effect = lambda m, msgs, *a, **k: iter([next(responses)])
    client._provider = "openrouter"
    app = _app(tmp_path, cfg, client)
    tc = TestClient(app)

    resp = tc.post("/loop", json={"message": "faça o site"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running" and body["run_id"]
    # limites vieram do config (teto)
    assert body["limits"] == {"max_minutes": 5, "max_tool_calls": 50, "max_cost_usd": 1.0}

    final = _wait_done(tc, body["run_id"])
    assert final["state"] == "completed" and final["stop_reason"] == "completed"
    assert final["rounds"] == 2
    assert "confirmo" in final["last_text"]

    # run persistido completed + trajetória do kernel (admissão)
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore
    store = JsonlStateStore(tmp_path / "sessions" / ".." / "runtime")
    run = RunManager(store=store).get_run(body["run_id"])
    assert run.status == "completed" and run.agent_id == "serve.loop"
    statuses = [r["status"] for r in store.list("runs") if r["id"] == body["run_id"]]
    assert statuses[:4] == ["created", "planning", "policy_check", "queued"]
    # eventos de rodada auditáveis
    from bauer.core.events.bus import EventBus
    types = [e.event_type for e in EventBus(store=store).list_events(run_id=body["run_id"])]
    assert types.count("loop.round.completed") == 2


def test_loop_stop_cancels_between_rounds(tmp_path: Path):
    import threading

    cfg = _cfg(tmp_path)
    client = MagicMock()
    started = threading.Event()

    def _slow(m, msgs, *a, **k):
        started.set()
        time.sleep(0.3)  # dá tempo do stop chegar entre rodadas
        return iter(["trabalhando sem parar"])

    client.chat_stream.side_effect = _slow
    client._provider = "openrouter"
    app = _app(tmp_path, cfg, client)
    tc = TestClient(app)

    run_id = tc.post("/loop", json={"message": "tarefa longa"}).json()["run_id"]
    assert started.wait(5.0)
    resp = tc.post(f"/loop/{run_id}/stop")
    assert resp.status_code == 200

    final = _wait_done(tc, run_id)
    assert final["state"] == "stopped"

    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore
    run = RunManager(store=JsonlStateStore(tmp_path / "sessions" / ".." / "runtime")).get_run(run_id)
    assert run.status == "cancelled"


def test_loop_kill_switch_blocks_admission(tmp_path: Path):
    from bauer.core.runtime.resilience import RuntimeControl
    from bauer.core.runtime.state_store import JsonlStateStore

    cfg = _cfg(tmp_path)
    client = MagicMock()
    client._provider = "openrouter"
    app = _app(tmp_path, cfg, client)
    RuntimeControl(store=JsonlStateStore(tmp_path / "sessions" / ".." / "runtime")).set_kill_switch(True)

    resp = TestClient(app).post("/loop", json={"message": "não deve iniciar"})
    assert resp.status_code == 503
    client.chat_stream.assert_not_called()


def test_loop_request_can_only_tighten_limits(tmp_path: Path):
    cfg = _cfg(tmp_path)
    client = MagicMock()
    responses = iter(["fim", "confirmo"])
    client.chat_stream.side_effect = lambda m, msgs, *a, **k: iter([next(responses)])
    client._provider = "openrouter"
    app = _app(tmp_path, cfg, client)

    body = TestClient(app).post("/loop", json={
        "message": "x", "max_minutes": 999, "max_tool_calls": 5, "max_cost_usd": 99.0,
    }).json()
    # 999 > teto(5) → clampa no teto; 5 < teto(50) → aceita; 99 > 1.0 → teto
    assert body["limits"] == {"max_minutes": 5, "max_tool_calls": 5, "max_cost_usd": 1.0}


def test_loop_works_without_kernel(tmp_path: Path):
    cfg = _cfg(tmp_path, kernel_enabled=False)
    client = MagicMock()
    responses = iter(["fim", "confirmo"])
    client.chat_stream.side_effect = lambda m, msgs, *a, **k: iter([next(responses)])
    client._provider = "openrouter"
    app = _app(tmp_path, cfg, client)
    tc = TestClient(app)

    run_id = tc.post("/loop", json={"message": "x"}).json()["run_id"]
    final = _wait_done(tc, run_id)
    assert final["state"] == "completed"


def test_loops_list_endpoint(tmp_path: Path):
    cfg = _cfg(tmp_path)
    client = MagicMock()
    responses = iter(["fim", "confirmo"])
    client.chat_stream.side_effect = lambda m, msgs, *a, **k: iter([next(responses)])
    client._provider = "openrouter"
    app = _app(tmp_path, cfg, client)
    tc = TestClient(app)

    run_id = tc.post("/loop", json={"message": "x"}).json()["run_id"]
    _wait_done(tc, run_id)
    loops = tc.get("/loops").json()["loops"]
    assert any(item["run_id"] == run_id for item in loops)
