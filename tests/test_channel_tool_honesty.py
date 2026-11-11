"""O canal avisa quando o modelo escreve chamadas de tool como texto.

Caso real (Beelink, 2026-11-11): no Telegram, o modelo respondeu com
``delegate_task(agent_name=..., task=...)`` escrito na mensagem e nenhuma
tool executada. Depois relatou progresso de "agentes" que nunca foram
disparados, e o painel corretamente não mostrava nada em execução.
"""

from __future__ import annotations

import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_tool_honesty import textual_tool_calls, unexecuted_tool_calls_note
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter

FAKE_DELEGATION = (
    "Disparando agora para cada agente:\n\n"
    "delegate_task(\n"
    '  agent_name="sre-specialist",\n'
    '  task="Executar testes de carga na ingestão CVM",\n'
    "  timeout=300\n"
    ")\n\n"
    'patch(path="config/cvm_cache.py", old_string="A", new_string="B")\n'
)
TOOLS = ["delegate_task", "patch", "read_file"]


def test_detects_keyword_calls_of_available_tools():
    assert textual_tool_calls(FAKE_DELEGATION, TOOLS) == ["delegate_task", "patch"]


@pytest.mark.parametrize("text", [
    "Use read_file para abrir o arquivo.",
    "Chamei read_file() antes de responder.",
    "print(x=1) não é tool",
    'delegate(agent_name="x") não é uma tool disponível',
])
def test_prose_and_unknown_names_are_not_flagged(text):
    assert textual_tool_calls(text, TOOLS) == []


def test_note_only_when_nothing_ran():
    note = unexecuted_tool_calls_note(FAKE_DELEGATION, [], TOOLS)
    assert note is not None
    assert "nenhuma ferramenta foi executada" in note
    assert "`delegate_task`" in note and "`patch`" in note
    ran = [{"tool": "delegate_task", "result": "ok"}]
    assert unexecuted_tool_calls_note(FAKE_DELEGATION, ran, TOOLS) is None
    assert unexecuted_tool_calls_note("Tudo certo, sem tools.", [], TOOLS) is None


def _message(text="dispare"):
    return ChannelMessage(
        channel="telegram", user_id="42", chat_id="42", text=text,
        raw={"message": {"chat": {"type": "private"}}},
    )


def _backend(tmp_path, *, adapter: str):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    runtime = f"runtime:\n  telegram_default_adapter: {adapter}\n" if adapter else ""
    config.write_text(
        f"agent:\n  workspace: {workspace.as_posix()}\n"
        "model:\n  provider: ollama\n  name: fake\n"
        "kernel:\n  enabled: true\n" + runtime,
        encoding="utf-8",
    )
    backend = AgentBackend(config, tmp_path / "memory" / "sessions")
    backend._client = object()
    backend._model_name = "fake"
    backend._applied_context = 4096
    backend._system_prompt = "Controlled worker"
    backend._router = ToolRouter(workspace=workspace)
    backend._store = SqliteSessionStore(backend.sessions_dir, semantic_indexing_enabled=False)
    backend._maybe_reload = lambda: None
    return backend


def _agno_returns(monkeypatch, *, tools):
    monkeypatch.setattr(
        "bauer.core.runtime.adapters.agno_adapter.AgnoRuntimeAdapter.run_agent",
        lambda self, request: {
            "status": "completed", "event": "run.completed",
            "run_id": request.get("run_id") or "agno-run", "runtime_adapter": "agno",
            "output": FAKE_DELEGATION, "metadata": {"tools": tools},
        },
    )


def test_agno_channel_turn_warns_when_calls_were_only_written(tmp_path, monkeypatch):
    pytest.importorskip("agno")
    backend = _backend(tmp_path, adapter="agno")
    _agno_returns(monkeypatch, tools=[])
    reply = backend.process(_message())
    assert reply.startswith("Disparando agora")
    assert "nenhuma ferramenta foi executada" in reply
    stored = [m for m in backend._store.load("tg:42") if m.get("role") == "assistant"]
    assert "nenhuma ferramenta foi executada" in stored[-1]["content"]


def test_agno_channel_turn_stays_silent_when_tools_ran(tmp_path, monkeypatch):
    pytest.importorskip("agno")
    backend = _backend(tmp_path, adapter="agno")
    _agno_returns(monkeypatch, tools=[{"tool_name": "delegate_task", "result": "ok"}])
    assert "nenhuma ferramenta" not in backend.process(_message())


def test_native_channel_turn_warns_and_keeps_one_assistant_message(tmp_path, monkeypatch):
    backend = _backend(tmp_path, adapter="")

    def fake_turn(ctx, router, client, model, fallbacks):
        ctx.add_assistant(FAKE_DELEGATION)
        return FAKE_DELEGATION, []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", fake_turn)
    reply = backend.process(_message())
    assert "nenhuma ferramenta foi executada" in reply
    stored = backend._store.load("tg:42")
    assert stored[-1]["role"] == "assistant"
    assert stored[-2]["role"] != "assistant"
    assert "nenhuma ferramenta foi executada" in stored[-1]["content"]
