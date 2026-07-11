"""Roteamento por-turno no serve (Fase 12 / Sprint 34c).

Opt-in via model.router_enabled: com routing, cada turno escolhe o modelo do
tier; sem routing (ou na dúvida) usa o primário. Conservador."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from bauer.server import create_app  # noqa: E402
from bauer.tool_router import ToolRouter  # noqa: E402


def _cfg(tmp_path: Path, *, enabled: bool, profiles: bool = True) -> Path:
    prof = ""
    if profiles:
        prof = textwrap.indent(textwrap.dedent("""
            profiles:
              fast:   {provider: openrouter, model: deepseek/deepseek-v4-flash}
              coding: {provider: openrouter, model: qwen/qwen3-coder-flash}
              heavy:  {provider: openrouter, model: deepseek/deepseek-r1}
        """), "  ")
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(f"""
        model:
          provider: openrouter
          name: deepseek/deepseek-v4-flash
          router_enabled: {str(enabled).lower()}
        """) + prof, encoding="utf-8")
    return p


def _run(tmp_path: Path, cfg: Path, message: str):
    """Sobe o serve com config e captura o modelo passado ao client no turno."""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    seen: dict = {}

    def _cap(model, payload):
        seen["model"] = model
        return iter(["ok"])

    mc = MagicMock()
    mc.chat_stream.side_effect = _cap
    mc._provider = "openrouter"  # mesmo provider dos profiles → reusa o client

    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "p.json"), \
         patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="deepseek/deepseek-v4-flash", applied_context=4096,
            router=ToolRouter(workspace=ws), client=mc, system_prompt="s",
            sessions_dir=tmp_path / "sessions", api_key="", rate_limit_requests=0,
            config_path=cfg,
        )
        resp = TestClient(app).get(f"/stream?message={message}")
    return seen.get("model"), resp


def test_routing_off_uses_primary(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    model, resp = _run(tmp_path, cfg, "crie um endpoint POST /users")
    assert resp.status_code == 200
    assert model == "deepseek/deepseek-v4-flash"   # primário, sem rotear


def test_routing_on_picks_tier_model(tmp_path):
    cfg = _cfg(tmp_path, enabled=True)
    model, _ = _run(tmp_path, cfg, "crie um endpoint POST /users com validação")
    assert model == "qwen/qwen3-coder-flash"        # tier coding


def test_routing_on_conversation_uses_fast(tmp_path):
    cfg = _cfg(tmp_path, enabled=True)
    model, _ = _run(tmp_path, cfg, "oi, tudo bem?")
    assert model == "deepseek/deepseek-v4-flash"    # tier fast


def test_routing_on_architecture_uses_heavy(tmp_path):
    cfg = _cfg(tmp_path, enabled=True)
    model, _ = _run(tmp_path, cfg, "refatore a arquitetura do runtime para múltiplos backends")
    assert model == "deepseek/deepseek-r1"          # tier heavy


def test_routing_emits_sse_route_and_runtime_event(tmp_path):
    cfg = _cfg(tmp_path, enabled=True)
    _, resp = _run(tmp_path, cfg, "crie um endpoint POST /users")
    run_id = resp.headers["X-Bauer-Run-ID"]
    assert "event: route" in resp.text              # chip na UI

    import time as _t

    from bauer.core.events import EventBus
    bus = EventBus(root=tmp_path / "runtime")
    deadline = _t.monotonic() + 3.0
    ev = []
    while _t.monotonic() < deadline and not ev:
        ev = [e for e in bus.list_events(run_id=run_id) if e.event_type == "model.route.selected"]
        if not ev:
            _t.sleep(0.02)
    assert ev, "esperava model.route.selected na Observabilidade"
    assert (ev[0].data or {}).get("model") == "qwen/qwen3-coder-flash"


def test_conservative_default_when_tier_missing_profile(tmp_path):
    """Tier sem profile configurado → cai no primário, nunca quebra."""
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
        model:
          provider: openrouter
          name: deepseek/deepseek-v4-flash
          router_enabled: true
          profiles:
            coding: {provider: openrouter, model: qwen/qwen3-coder-flash}
        """), encoding="utf-8")
    # "oi" → tier fast, que NÃO está nos profiles → default
    model, _ = _run(tmp_path, p, "oi, tudo bem?")
    assert model == "deepseek/deepseek-v4-flash"
