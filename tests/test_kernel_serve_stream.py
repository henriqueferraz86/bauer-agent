"""/stream (SSE) com admissão pelo Bauer Kernel (Sprint 6c) — opt-in.

O /stream mantém o motor de streaming existente (worker thread + fila + gate);
o Kernel entra como CONTROLE DE ADMISSÃO: estados created→planning→
policy_check→queued persistidos, kill-switch e policy/budget avaliados ANTES
de qualquer chamada ao LLM. kernel.enabled=false (default) = caminho legado.
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


def _cfg(tmp_path: Path, *, kernel_enabled: bool) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""
        model:
          provider: openrouter
          name: deepseek/deepseek-v4-flash
        kernel:
          enabled: {str(kernel_enabled).lower()}
        """), encoding="utf-8")
    return p


def _make_client(*responses: str) -> MagicMock:
    client = MagicMock()
    it = iter(responses)

    def _stream(model, messages, *a, **k):
        return iter([next(it)])

    client.chat_stream.side_effect = _stream
    client._provider = "openrouter"
    return client


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


def _runtime(tmp_path: Path):
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore

    store = JsonlStateStore(tmp_path / "sessions" / ".." / "runtime")
    return RunManager(store=store), store


def test_stream_via_kernel_delivers_text_and_completes(tmp_path: Path):
    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("olá do stream")
    app = _app(tmp_path, cfg, client)
    resp = TestClient(app).get("/stream?message=oi")
    assert resp.status_code == 200
    assert "olá do stream" in resp.text
    assert "event: done" in resp.text

    runs, store = _runtime(tmp_path)
    run = runs.list_runs()[0]
    assert run.status == "completed"
    # trajetória de admissão persistida antes do running
    statuses = [r["status"] for r in store.list("runs") if r["id"] == run.id]
    assert statuses[:4] == ["created", "planning", "policy_check", "queued"]
    assert statuses[-1] == "completed"


def test_stream_kill_switch_blocks_without_llm(tmp_path: Path):
    from bauer.core.runtime.resilience import RuntimeControl
    from bauer.core.runtime.state_store import JsonlStateStore

    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("não deveria rodar")
    app = _app(tmp_path, cfg, client)
    RuntimeControl(store=JsonlStateStore(tmp_path / "sessions" / ".." / "runtime")).set_kill_switch(True)

    resp = TestClient(app).get("/stream?message=oi")
    assert resp.status_code == 200  # SSE sempre 200; o bloqueio vai no corpo
    assert "kill switch" in resp.text
    assert "event: done" in resp.text
    client.chat_stream.assert_not_called()

    runs, _ = _runtime(tmp_path)
    assert runs.list_runs()[0].status == "cancelled"


def test_stream_budget_exceeded_blocks_without_llm(tmp_path: Path):
    from bauer.core.runtime.autonomy import BudgetManager

    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("não deveria rodar")
    app = _app(tmp_path, cfg, client)
    budget = BudgetManager(root=tmp_path / "sessions" / ".." / "runtime")
    budget.set_profile(daily_budget_usd=0.5)
    budget.record_run_cost(run_id="r-previo", agent_id="serve.stream",
                           company_id=None, cost_usd=1.0)

    resp = TestClient(app).get("/stream?message=oi")
    assert "budget exceeded" in resp.text
    client.chat_stream.assert_not_called()

    runs, _ = _runtime(tmp_path)
    run = runs.list_runs()[0]
    assert run.status == "failed" and "budget" in (run.error or "")


def test_stream_legacy_path_untouched(tmp_path: Path):
    """kernel.enabled=false: /stream ignora kill-switch (sem regressão de escopo)."""
    from bauer.core.runtime.resilience import RuntimeControl
    from bauer.core.runtime.state_store import JsonlStateStore

    cfg = _cfg(tmp_path, kernel_enabled=False)
    client = _make_client("legado segue normal")
    app = _app(tmp_path, cfg, client)
    RuntimeControl(store=JsonlStateStore(tmp_path / "sessions" / ".." / "runtime")).set_kill_switch(True)

    resp = TestClient(app).get("/stream?message=oi")
    assert "legado segue normal" in resp.text
    assert "event: done" in resp.text
