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


def test_chat_via_kernel_records_cost_exactly_once(tmp_path: Path):
    """Regressão: executor grava custo via _record_turn_budget E o kernel
    gravaria de novo via _record_cost se tivesse budget — _used_since soma
    todas as linhas sem dedup, então o orçamento esgotaria na METADE. O serve
    passa budget=None ao kernel de propósito."""
    import json

    cfg = _cfg(tmp_path, kernel_enabled=True)
    client = MagicMock()

    def _stream(model, messages, *a, **k):
        from bauer.cost_meter import cost_sink
        sink = cost_sink.get()
        if sink is not None:
            sink("openrouter", model, {"prompt_tokens": 10, "completion_tokens": 5}, 0.0123)
        return iter(["resposta"])

    client.chat_stream.side_effect = _stream
    client._provider = "openrouter"
    app = _app_and_store(tmp_path, cfg, client)
    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 200

    runtime_root = tmp_path / "sessions" / ".." / "runtime"
    lines = [json.loads(line) for line in
             (runtime_root / "run_costs.jsonl").read_text(encoding="utf-8").splitlines()
             if line.strip()]
    assert len(lines) == 1, f"custo contado {len(lines)}x — esperado exatamente 1"
    # rel=1e-3: o motor também reporta o uso do mock (~$0.000005 a mais)
    assert lines[0]["cost_usd"] == pytest.approx(0.0123, rel=1e-3)

    # e o cost_estimate do run continua preenchido (via kernel.complete_run)
    from bauer.core.runtime.run_manager import RunManager
    from bauer.core.runtime.state_store import JsonlStateStore
    runs = RunManager(store=JsonlStateStore(runtime_root)).list_runs()
    assert runs[0].cost_estimate == pytest.approx(0.0123, rel=1e-3)


def test_chat_via_kernel_replan_restores_context_and_injects_feedback(tmp_path: Path):
    """Regressão: replan re-executava com a resposta reprovada ainda no ctx e
    sem o replan_feedback — custo dobrado sem chance de correção. Agora a
    tentativa reprovada é descartada e o motivo do gate entra como system."""
    cfg = _cfg(tmp_path, kernel_enabled=True, evaluator_enabled=True)
    seen_messages: list[list[dict]] = []
    responses = iter(["Traceback (most recent call last):", "corrigido"])

    def _stream(model, messages, *a, **k):
        seen_messages.append([dict(m) for m in messages])
        return iter([next(responses)])

    client = MagicMock()
    client.chat_stream.side_effect = _stream
    client._provider = "openrouter"
    app = _app_and_store(tmp_path, cfg, client)

    resp = TestClient(app).post("/chat", json={"message": "oi"})
    assert resp.status_code == 200
    assert resp.json()["response"] == "corrigido"
    assert client.chat_stream.call_count == 2

    # 2ª execução: a RESPOSTA reprovada saiu do histórico (o feedback do gate
    # cita o marcador, mas como system — não como fala do assistant)
    second_call = seen_messages[1]
    assistant_msgs = " ".join(m.get("content", "") for m in second_call
                              if m.get("role") == "assistant")
    assert "Traceback (most recent call last)" not in assistant_msgs
    system_msgs = " ".join(m.get("content", "") for m in second_call
                           if m.get("role") == "system")
    assert "quality gate" in system_msgs

    # sessão salva limpa: exatamente 1 resposta do assistant (a corrigida)
    from bauer.session_store import SessionStore
    saved = SessionStore(tmp_path / "sessions").load(resp.json()["session_id"])
    assistants = [m["content"] for m in saved if m.get("role") == "assistant"]
    assert assistants == ["corrigido"]


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
