"""Sprint 77: o agente do Telegram decide quando a demanda vira /loop.

O operador manda a demanda em texto livre. O modelo chama
`iniciar_trabalho_autonomo(objetivo)` quando o trabalho não cabe num turno;
depois que o turno termina e a sessão é liberada, o bridge inicia o /loop
pelo mesmo caminho governado do comando.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bauer import channel_execution, telegram_loop
from bauer.channel_autonomous_handoff import TOOL_NAME, handoff_available, take_goal_from
from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.execution_control import ExecutionControl
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter
from bauer.tools.base import ToolError
from tests._deadlines import ANTI_HANG

GOAL = "Implementar o conector CVM DFP/FII com testes; concluir com pytest verde"


@pytest.fixture(autouse=True)
def _isolated_delivery_registry():
    saved_channels = dict(channel_execution._delivery_channels)
    saved_keys = set(channel_execution._delivered_keys)
    channel_execution._delivery_channels.clear()
    channel_execution._delivered_keys.clear()
    yield
    channel_execution._delivery_channels.clear()
    channel_execution._delivery_channels.update(saved_channels)
    channel_execution._delivered_keys.clear()
    channel_execution._delivered_keys.update(saved_keys)


def _message(text="faça a fase 11", *, private=True, update_id=501):
    return ChannelMessage(
        channel="telegram", user_id="42", chat_id="42", text=text,
        raw={"update_id": update_id,
             "message": {"chat": {"type": "private" if private else "group"}}},
    )


def _backend(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"agent:\n  workspace: {workspace.as_posix()}\n"
        "model:\n  provider: ollama\n  name: fake\n"
        "kernel:\n  enabled: true\n",
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


# ── disponibilidade da tool ─────────────────────────────────────────────────

def test_tool_is_hidden_and_refused_outside_a_telegram_turn(tmp_path):
    router = ToolRouter(workspace=tmp_path)
    assert TOOL_NAME not in router.available_tools()
    assert TOOL_NAME not in {s["function"]["name"] for s in router.get_tool_schemas()}
    with pytest.raises(ToolError):
        router.execute_native_call(TOOL_NAME, {"objetivo": GOAL})


@pytest.mark.parametrize("channel,private,expected", [
    ("telegram", True, True),
    ("telegram", False, False),   # grupo: mesma regra do /loop
    ("discord", True, False),
])
def test_tool_only_in_private_telegram_turns(tmp_path, channel, private, expected):
    router = ToolRouter(workspace=tmp_path)
    control = ExecutionControl(channel=channel, private_chat=private)
    with control.activate():
        assert handoff_available() is expected
        assert (TOOL_NAME in router.available_tools()) is expected


def test_calling_the_tool_only_records_the_goal(tmp_path):
    router = ToolRouter(workspace=tmp_path)
    control = ExecutionControl(channel="telegram", private_chat=True)
    with control.activate():
        result = router.execute_native_call(TOOL_NAME, {"objetivo": f"  {GOAL}  "})
    assert "agendado" in result
    assert control.autonomous_goal == GOAL
    assert take_goal_from(control) == GOAL
    control.cancel_event.set()
    assert take_goal_from(control) is None  # turno cancelado não vira loop


def test_empty_goal_is_rejected(tmp_path):
    router = ToolRouter(workspace=tmp_path)
    control = ExecutionControl(channel="telegram", private_chat=True)
    with control.activate():
        with pytest.raises(ToolError):
            router.execute_native_call(TOOL_NAME, {"objetivo": "   "})
    assert control.autonomous_goal is None


# ── canal: o turno registra, o backend entrega uma vez ─────────────────────

def _turn_that_hands_off(monkeypatch, *, call_tool=True):
    def fake_turn(ctx, router, client, model, fallbacks=None, **kwargs):
        if call_tool:
            router.execute_native_call(TOOL_NAME, {"objetivo": GOAL})
        reply = "Vou abrir um trabalho autônomo para implementar a Fase 11."
        ctx.add_assistant(reply)
        return reply, [{"tool": TOOL_NAME, "result": "ok"}] if call_tool else []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", fake_turn)


def test_channel_turn_hands_off_the_goal_once(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    _turn_that_hands_off(monkeypatch)
    msg = _message()
    reply = backend.process(msg)
    assert "trabalho autônomo" in reply
    assert backend.pop_autonomous_handoff(msg) == GOAL
    assert backend.pop_autonomous_handoff(msg) is None


def test_plain_turn_hands_off_nothing(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    _turn_that_hands_off(monkeypatch, call_tool=False)
    msg = _message()
    backend.process(msg)
    assert backend.pop_autonomous_handoff(msg) is None


def test_group_turn_cannot_hand_off(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    _turn_that_hands_off(monkeypatch)
    msg = _message(private=False)
    backend.process(msg)  # a tool é recusada; o turno segue
    assert backend.pop_autonomous_handoff(msg) is None


# ── bridge: inicia o /loop pelo caminho do comando ─────────────────────────

class _Bridge:
    """Só o que `_start_autonomous_handoff` usa do TelegramBridge."""

    def __init__(self, backend):
        from bauer.telegram_bridge import TelegramBridge

        self.backend = backend
        self.sent: list[tuple[str, str]] = []
        self._start = TelegramBridge._start_autonomous_handoff.__get__(self)

    def send_text(self, chat_id, text):
        self.sent.append((chat_id, text))


def test_bridge_starts_loop_with_update_id_and_goal(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    _turn_that_hands_off(monkeypatch)
    msg = _message(update_id=777)
    backend.process(msg)
    calls = []
    monkeypatch.setattr(
        telegram_loop, "start_loop",
        lambda b, m, update_id, goal: calls.append((update_id, goal)) or "🔁 /loop iniciado. run_id=r1",
    )
    bridge = _Bridge(backend)
    bridge._start(msg)
    assert calls == [(777, GOAL)]
    assert bridge.sent == [("42", "🔁 /loop iniciado. run_id=r1")]
    bridge._start(msg)  # nada pendente: não inicia de novo
    assert len(calls) == 1


def test_bridge_does_nothing_without_handoff(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    monkeypatch.setattr(telegram_loop, "start_loop",
                        lambda *a, **k: pytest.fail("não deveria iniciar loop"))
    bridge = _Bridge(backend)
    bridge._start(_message())
    assert bridge.sent == []


# ── ponta a ponta: turno → handoff → start_loop real → relatório ───────────

class _Run:
    def __init__(self, run_id):
        self.id, self.status, self.output, self.error = run_id, "queued", None, None
        self.finished_at, self.tool_calls_count = None, 0


class _Kernel:
    """Kernel fake no padrão de `tests/test_telegram_loop.py`."""

    def __init__(self):
        self.runs = SimpleNamespace(_runs={}, get_run=lambda rid: self.runs._runs.get(rid))
        self.control = None
        self.bus = None
        self.budget = None
        self.admitted: list = []

    def admit(self, request):
        self.admitted.append(request)
        run = _Run(f"loop-{len(self.admitted)}")
        self.runs._runs[run.id] = run
        return run, None

    def continue_run(self, run_id, *, extra_input=None, executor):
        result = executor({}) or {}
        run = self.runs.get_run(run_id)
        run.status = str(result.get("status") or "completed")
        run.output = {"output": result.get("output")}
        run.finished_at = datetime.now(UTC).isoformat()
        return SimpleNamespace(run_id=run_id, status=run.status,
                               output=result.get("output"), error=None)


def test_handoff_starts_a_real_loop_after_the_turn_releases_the_session(tmp_path, monkeypatch):
    backend = _backend(tmp_path)
    msg = _message(update_id=900)
    # Turno de conversa: o modelo decide pelo trabalho autônomo.
    _turn_that_hands_off(monkeypatch)
    backend.process(msg)

    # Rodadas do loop: concluem com texto (semântica de run_loop_rounds).
    def loop_turn(ctx, router, client, model, fallbacks=None, **kwargs):
        ctx.add_assistant("Fase 11 implementada e testada.")
        return "Fase 11 implementada e testada.", []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", loop_turn)
    kernel = _Kernel()
    backend._resolver_kernel = lambda workspace=None: kernel
    delivered: list = []
    channel_execution.register_delivery_channel(
        "tg:", lambda sid, text: delivered.append((sid, text)) or True,
    )

    bridge = _Bridge(backend)
    bridge._start(msg)

    assert len(kernel.admitted) == 1
    assert kernel.admitted[0].task == GOAL
    assert kernel.admitted[0].autonomous is True
    assert "iniciado" in bridge.sent[0][1]
    deadline = time.monotonic() + ANTI_HANG
    while not delivered and time.monotonic() < deadline:
        time.sleep(0.02)
    assert delivered and delivered[0][0] == "tg:42"
    assert "stop_reason=completed" in delivered[0][1]
    record = telegram_loop._store_for(backend).get_by_update_id(900)
    assert record is not None and record.goal == GOAL


def test_agno_tool_bridge_records_the_goal_under_the_turn_control(tmp_path):
    """Caminho da Beelink: a tool chega ao modelo pela ponte do Agno."""
    pytest.importorskip("agno")
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    router = ToolRouter(workspace=tmp_path)
    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(tmp_path)}, tool_router=router)
    control = ExecutionControl(channel="telegram", private_chat=True)
    with control.activate():
        assert TOOL_NAME in router.available_tools()
        (function,) = [f for f in adapter._map_tools([TOOL_NAME])
                       if getattr(f, "name", "") == TOOL_NAME]
        assert "objetivo" in function.parameters["properties"]
        result = function.entrypoint(objetivo=GOAL)
    assert "agendado" in result
    assert control.autonomous_goal == GOAL


def test_same_goal_in_a_later_turn_is_scheduled_again(tmp_path, monkeypatch):
    """A deduplicação de tools não pode devolver "agendado" sem agendar."""
    backend = _backend(tmp_path)
    _turn_that_hands_off(monkeypatch)
    msg = _message()
    backend.process(msg)
    assert backend.pop_autonomous_handoff(msg) == GOAL
    backend.process(_message(text="de novo"))
    assert backend.pop_autonomous_handoff(msg) == GOAL
