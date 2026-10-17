"""/stream (SSE) com admissão pelo Bauer Kernel (Sprint 6c) — opt-in.

O /stream mantém o motor de streaming existente (worker thread + fila + gate);
o Kernel entra como CONTROLE DE ADMISSÃO: estados created→planning→
policy_check→queued persistidos, kill-switch e policy/budget avaliados ANTES
de qualquer chamada ao LLM. kernel.enabled=false (default) = caminho legado.
"""

from __future__ import annotations

import json
import logging
import textwrap
from contextlib import contextmanager
import threading
import time
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


@pytest.mark.parametrize("with_tool", [False, True])
def test_stream_chatgpt_uses_responses_through_real_native_agent(tmp_path: Path, with_tool: bool):
    import httpx

    from bauer.chatgpt_backend import ChatGPTBackendClient, DEFAULT_CHATGPT_BASE

    client = ChatGPTBackendClient(access_token="fake", model="gpt-5.6-luna")
    app = _app(tmp_path, _cfg(tmp_path, kernel_enabled=True), client)
    requests = []

    @contextmanager
    def responses_stream(method, url, **kwargs):
        assert (method, url) == ("POST", f"{DEFAULT_CHATGPT_BASE}/responses")
        requests.append(kwargs["json"])
        if with_tool and len(requests) == 1:
            events = [{"type": "response.output_item.done", "item": {
                "type": "function_call", "call_id": "call_calc",
                "name": "calculate", "arguments": '{"expression":"2+2"}',
            }}]
        else:
            events = [{"type": "response.output_text.delta", "delta": "Olá, resultado: 4."}]
        events.append({"type": "response.completed", "response": {
            "usage": {"input_tokens": 3, "output_tokens": 2},
        }})
        yield httpx.Response(200, text="\n\n".join(
            "data: " + json.dumps(event) for event in events
        ))

    with patch("httpx.stream", side_effect=responses_stream), \
         patch("httpx.post", side_effect=AssertionError("wrong native endpoint")):
        response = TestClient(app).get("/stream", params={"message": "oi"})

    assert response.status_code == 200
    assert "Olá, resultado: 4." in response.text
    assert "event: done" in response.text
    assert len(requests) == (2 if with_tool else 1)
    assert requests[0]["tools"]
    if with_tool:
        tool_result = next(item for item in requests[1]["input"] if item["type"] == "function_call_output")
        assert tool_result == {"type": "function_call_output", "call_id": "call_calc", "output": "2+2 = 4"}
    runs, _ = _runtime(tmp_path)
    run = runs.list_runs()[0]
    assert run.status == "completed"
    assert run.tool_calls_count == int(with_tool)
    assert run.llm_calls_count == len(requests)
    assert run.usage_known is True


def test_stream_failure_logs_sanitized_cause_without_exposing_it(tmp_path: Path):
    from bauer.openai_client import OpenAIClientError

    app = _app(tmp_path, _cfg(tmp_path, kernel_enabled=True), _make_client("unused"))
    failure = OpenAIClientError("HTTP 403 Authorization: Bearer secret-token")
    with patch("bauer.agent.run_one_turn_with_fallback", side_effect=failure), \
         patch.object(logging.getLogger("bauer.server"), "error") as log_error:
        response = TestClient(app).get("/stream", params={"message": "oi"})

    assert "resposta indisponível" in response.text
    assert "secret-token" not in response.text
    assert "HTTP 403" not in response.text
    assert "event: done" in response.text
    log_error.assert_called_once()
    args = log_error.call_args.args
    assert args[2] == "OpenAIClientError"
    assert "HTTP 403" in args[3]
    assert "secret-token" not in args[3]
    assert "[REDACTED]" in args[3]
    runs, _ = _runtime(tmp_path)
    assert runs.list_runs()[0].status == "failed"


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


def test_stream_approval_is_visible_and_resumes_after_rest_api_decision(tmp_path: Path):
    """Aprovar na aba Approvals retoma o mesmo stream, sem repetir o pedido."""
    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = _make_client("execução retomada")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    policy_dir = ws / ".bauer"
    policy_dir.mkdir()
    (policy_dir / "policy.yaml").write_text(
        "rules:\n  - id: runtime.ask\n    operation: runtime.execute\n    action: ask\n    reason: aprovação humana necessária\n",
        encoding="utf-8",
    )
    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "p.json"), \
         patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="deepseek/deepseek-v4-flash", applied_context=4096,
            router=ToolRouter(workspace=ws), client=client, system_prompt="s",
            sessions_dir=tmp_path / "sessions", api_key="", rate_limit_requests=0,
            config_path=cfg,
        )

    result: dict[str, str] = {}

    def consume_stream() -> None:
        with TestClient(app).stream("GET", "/stream?message=precisa-aprovar") as response:
            result["body"] = "".join(response.iter_text())

    thread = threading.Thread(target=consume_stream, daemon=True)
    thread.start()
    approval = None
    control = TestClient(app)
    for _ in range(40):
        approvals = control.get("/api/obs/approvals?status=pending").json()["approvals"]
        if approvals:
            approval = approvals[0]
            break
        time.sleep(0.05)
    assert approval is not None

    decision = control.post(f"/api/approvals/{approval['id']}/approve")
    assert decision.status_code == 200
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "event: approval" in result["body"]
    assert '"operation": "runtime.execute"' in result["body"]
    assert '"status": "approved"' in result["body"]
    assert "execução retomada" in result["body"]
    client.chat_stream.assert_called_once()
