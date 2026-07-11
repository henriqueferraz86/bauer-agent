"""/chat pelo Bauer Kernel (Sprint 6b) — opt-in via kernel.enabled.

Provam paridade com o caminho legado (mesma ChatResponse) E as novidades de
governança (kill-switch, policy deny) que só existem quando o Kernel está
ligado. kernel.enabled=false (default) roda o caminho de sempre.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bauer.server import create_app  # noqa: E402
from bauer.tool_router import ToolRouter  # noqa: E402


def _cfg(tmp_path: Path, *, kernel_enabled: bool, evaluator_enabled: bool = False) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""
        model:
          provider: openrouter
          name: deepseek/deepseek-v4-flash
        kernel:
          enabled: {str(kernel_enabled).lower()}
          evaluator_enabled: {str(evaluator_enabled).lower()}
        """), encoding="utf-8")
    return p


def _make_client(*responses: str) -> MagicMock:
    client = MagicMock()
    it = iter(responses)

    def _cap(model, messages, *a, **k):
        return iter([next(it)])

    client.chat_stream.side_effect = _cap
    client._provider = "openrouter"
    return client


def _app_and_store(tmp_path: Path, cfg: Path, client: MagicMock):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "p.json"), \
         patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="deepseek/deepseek-v4-flash", applied_context=4096,
            router=ToolRouter(workspace=ws), client=client, system_prompt="s",
            sessions_dir=tmp_path / "sessions", api_key="", rate_limit_requests=0,
            config_path=cfg,
        )
    return app


def test_chat_legacy_path_when_kernel_disabled(tmp_path: Path):
    cfg = _cfg(tmp_path, kernel_enabled=False)
    client = _make_client("olá do legado")
    app = _app_and_store(tmp_path, cfg, client)
    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 200
    assert resp.json()["response"] == "olá do legado"


def test_chat_via_kernel_same_contract(tmp_path: Path):
    """kernel.enabled=true: MESMA ChatResponse, MESMO texto — paridade."""
    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("olá do kernel")
    app = _app_and_store(tmp_path, cfg, client)
    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"] == "olá do kernel"
    assert body["session_id"]
    assert body["model"]


def test_chat_via_kernel_persists_full_state_trajectory(tmp_path: Path):
    """Diferença observável: run persistido tem os estados novos do Kernel."""
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore

    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("ok")
    app = _app_and_store(tmp_path, cfg, client)
    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 200

    runtime_root = tmp_path / "sessions" / ".." / "runtime"
    runs = RunManager(store=JsonlStateStore(runtime_root)).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "completed"


def test_chat_kill_switch_blocks_before_turn(tmp_path: Path):
    """Kill-switch ligado (RuntimeControl) bloqueia /chat SEM chamar o LLM —
    comportamento novo, só existe com o Kernel ligado."""
    from bauer.core.runtime.resilience import RuntimeControl
    from bauer.core.runtime.state_store import JsonlStateStore

    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("nao deveria chegar aqui")
    app = _app_and_store(tmp_path, cfg, client)

    runtime_root = tmp_path / "sessions" / ".." / "runtime"
    RuntimeControl(store=JsonlStateStore(runtime_root)).set_kill_switch(True)

    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 503
    client.chat_stream.assert_not_called()


def test_chat_policy_deny_via_budget_exceeded(tmp_path: Path):
    """Orçamento excedido (BudgetManager, ja existente) agora bloqueia /chat
    ANTES do turno — antes o /chat nao tinha gate de orcamento nenhum.

    O gate compara USO JA REGISTRADO contra o limite (o custo do turno atual
    so e conhecido DEPOIS que ele roda) — por isso simula uso previo."""
    from bauer.core.runtime.autonomy import BudgetManager
    from bauer.core.runtime.state_store import JsonlStateStore

    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("nao deveria chegar aqui")
    app = _app_and_store(tmp_path, cfg, client)

    runtime_root = tmp_path / "sessions" / ".." / "runtime"
    budget = BudgetManager(root=runtime_root)
    budget.set_profile(daily_budget_usd=0.5)
    budget.record_run_cost(run_id="r-previo", agent_id="serve.chat", company_id=None, cost_usd=1.0)

    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 403
    client.chat_stream.assert_not_called()


def test_chat_via_kernel_turn_error_returns_500(tmp_path: Path):
    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = MagicMock()
    client.chat_stream.side_effect = RuntimeError("provider caiu")
    client._provider = "openrouter"
    app = _app_and_store(tmp_path, cfg, client)
    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 500


def test_chat_via_kernel_evaluator_rejects_empty_output(tmp_path: Path):
    cfg = _cfg(tmp_path, kernel_enabled=True, evaluator_enabled=True)
    client = _make_client("   ")  # output vazio (após strip) -> NonEmptyOutputGate reprova
    app = _app_and_store(tmp_path, cfg, client)
    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 500


def test_chat_kernel_disabled_ignores_kill_switch(tmp_path: Path):
    """Caminho legado NUNCA consulta o kill-switch — sem regressão de escopo."""
    from bauer.core.runtime.resilience import RuntimeControl
    from bauer.core.runtime.state_store import JsonlStateStore

    cfg = _cfg(tmp_path, kernel_enabled=False)
    client = _make_client("legado ignora kill switch")
    app = _app_and_store(tmp_path, cfg, client)

    runtime_root = tmp_path / "sessions" / ".." / "runtime"
    RuntimeControl(store=JsonlStateStore(runtime_root)).set_kill_switch(True)

    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 200
    assert resp.json()["response"] == "legado ignora kill switch"
