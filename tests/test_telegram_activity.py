"""Private Telegram activity cards use the governed runtime read-only."""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_governance import configure_router
from bauer.channel_projects import ChannelProjectContext
from bauer.core.runtime.run_manager import RunManager
from bauer.server_activity import build_chat_activity
from bauer.telegram_bridge import TelegramBridge
from bauer.tool_router import ToolRouter


class ActivityBackend(AgentBackend):
    def __init__(self, root: Path):
        super().__init__(config_path=root / "absent.yaml", sessions_dir=root / "sessions")
        self.contexts = {
            key: ChannelProjectContext(key, key, root / key, f"session-{key}")
            for key in ("alpha", "beta")
        }
        for context in self.contexts.values():
            context.workspace.mkdir()
        self.current = "alpha"
        self.router = ToolRouter(workspace=self.contexts["alpha"].workspace)
        self.bus = configure_router(self.router, sessions_dir=root / "sessions")
        self.runs = RunManager(store=self.bus.store, event_bus=self.bus)
        self.kernel = SimpleNamespace(runs=self.runs, bus=self.bus)

    @property
    def is_ready(self):
        return True

    def project_context(self, msg):
        if msg.project_snapshot_captured:
            return msg.project_snapshot
        return self.contexts[self.current]

    def channel_activity_source(self, msg):
        return self.project_context(msg), self.kernel

    def channel_activity(self, msg, *, run_id=None):
        context, kernel = self.channel_activity_source(msg)
        return build_chat_activity(kernel.runs, kernel.bus, context.session_key, run_id=run_id)

    def list_channel_projects(self, msg):
        return [{"id": key, "name": value.name} for key, value in self.contexts.items()]

    @contextmanager
    def project_scope(self, msg):
        yield self.project_context(msg)


@pytest.fixture
def transport(tmp_path):
    sent = []

    def handler(request):
        body = json.loads(request.content) if request.content else {}
        sent.append((request.url.path.rsplit("/", 1)[-1], body))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 71}})

    backend = ActivityBackend(tmp_path)
    bridge = TelegramBridge(token="fake", backend=backend, allowed_users=[42], state_dir=tmp_path / "state")
    bridge._http.close()
    bridge._http = httpx.Client(transport=httpx.MockTransport(handler))
    yield bridge, sent
    for record in bridge._activity_callbacks.values():
        timer = record.get("timer")
        if timer is not None:
            timer.cancel()
    bridge._executor.shutdown(wait=True)
    bridge._http.close()


def update(text, *, user=42, chat=42, kind="private"):
    return {"update_id": 1, "message": {
        "text": text, "from": {"id": user}, "chat": {"id": chat, "type": kind},
    }}


def callback(data, *, user=42, chat=42, message_id=71, kind="private"):
    return {"id": "callback", "data": data, "from": {"id": user},
            "message": {"message_id": message_id, "chat": {"id": chat, "type": kind}}}


def new_run(backend, *, session="session-alpha", task="Revisar token=sk-secret"):
    return backend.runs.create_run(
        session_id=session, agent_id="serve.chat", status="running", input={"message": task},
    )


def activity_card(bridge, sent):
    bridge._handle_update(update("/atividade"))
    method, body = [row for row in sent if row[0] == "sendMessage"][-1]
    markup = body.get("reply_markup") or {}
    rows = markup.get("inline_keyboard") or []
    token = rows[0][0]["callback_data"] if rows else None
    return body, token


def capture_timers(monkeypatch):
    timers = []

    class FakeTimer:
        def __init__(self, delay, function, args=(), kwargs=None):
            self.delay = delay
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr("bauer.telegram_bridge.threading.Timer", FakeTimer)
    return timers


def test_activity_is_private_authorized_and_precedes_turn_queue(transport):
    bridge, sent = transport
    run = new_run(bridge.backend)
    bridge._executor.submit = lambda *args: pytest.fail("/atividade não pode entrar na fila de turnos")

    bridge._handle_update(update("/atividade", kind="group"))
    assert any("privada" in body.get("text", "") for method, body in sent if method == "sendMessage")
    before = len(sent)
    bridge._handle_update(update("/atividade", user=99))
    assert len(sent) == before
    card, token = activity_card(bridge, sent)
    assert token.startswith("act:") and "session-alpha" not in token
    assert "Revisar token=[REDACTED]" in card["text"]
    assert "Custo: desconhecido" in card["text"]
    assert run.id in card["text"]


def test_activity_card_shows_known_cost_and_fixed_failure_summary(transport):
    bridge, sent = transport
    run = new_run(bridge.backend, task="Executar tarefa")
    bridge.backend.runs.update_run(
        run.id, status="failed", cost_estimate=0.125, usage_known=True,
        error="Traceback: PRIVATE_ARGUMENTS token=sk-secret",
    )
    card, _ = activity_card(bridge, sent)
    assert "Custo: $0.125000" in card["text"]
    assert "A execução falhou; consulte os logs locais." in card["text"]
    assert "Traceback" not in card["text"]
    assert "PRIVATE_ARGUMENTS" not in card["text"]
    assert "sk-secret" not in card["text"]
    assert not card.get("reply_markup")


def test_activity_formatter_redacts_project_name_and_respects_telegram_limit(transport):
    bridge, _ = transport
    rendered = bridge._activity_text("finance token=sk-project-secret", {
        "run_id": "run-1", "task": "task", "status": "running", "agents": [],
        "cost_known": False,
    })
    assert "sk-project-secret" not in rendered
    assert len(rendered) < 4096


@pytest.mark.parametrize("changes", [
    {"user": 99}, {"chat": 99}, {"message_id": 72}, {"kind": "group"},
])
def test_activity_callback_rejects_identity_or_message_mismatch(transport, changes):
    bridge, sent = transport
    new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    bridge._handle_callback(callback(token, **changes))
    assert not any(method == "editMessageText" for method, _ in sent)


def test_activity_refresh_edits_original_card_and_revalidates_project(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = capture_timers(monkeypatch)
    bridge.backend.bus.publish("tool.call.requested", run_id=run.id, session_id="session-alpha",
                               tool_name="read_file", data={"args": {"path": "SECRET_PATH"}})
    bridge._handle_callback(callback(token))
    assert len(timers) == 1
    bridge._activity_callbacks[token[4:]]["last_edit"] = time.monotonic() - 10
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    method, edited = [row for row in sent if row[0] == "editMessageText"][-1]
    assert edited["message_id"] == 71
    assert edited["text"] == bridge._activity_text("alpha", bridge.backend.channel_activity(
        ChannelMessage("telegram", "42", "42", "/atividade", raw={"message": {"chat": {"type": "private"}}}),
        run_id=run.id,
    )["runs"][0])
    assert "Executando ferramenta" in edited["text"]
    assert "SECRET_PATH" not in edited["text"]

    bridge.backend.current = "beta"
    before = len([row for row in sent if row[0] == "editMessageText"])
    bridge._handle_callback(callback(token))
    assert len([row for row in sent if row[0] == "editMessageText"]) == before


def test_activity_callback_expires_and_uses_only_the_bound_run(transport, monkeypatch):
    bridge, sent = transport
    first = new_run(bridge.backend, task="Primeira execução")
    _, token = activity_card(bridge, sent)
    timers = capture_timers(monkeypatch)
    second = new_run(bridge.backend, task="Execução mais recente")
    bridge._handle_callback(callback(token))
    assert len(timers) == 1
    bridge._activity_callbacks[token[4:]]["last_edit"] = time.monotonic() - 10
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    edit = [body for method, body in sent if method == "editMessageText"][-1]
    assert first.id in edit["text"] and second.id not in edit["text"]
    assert "Primeira execução" in edit["text"]
    assert "Execução mais recente" not in edit["text"]

    bridge._activity_callbacks[token[4:]]["callback_expires_at"] = 0
    before = len([row for row in sent if row[0] == "editMessageText"])
    bridge._handle_callback(callback(token))
    assert len([row for row in sent if row[0] == "editMessageText"]) == before


def test_activity_creates_one_card_per_run_and_bounds_long_agent_lists(transport):
    bridge, sent = transport
    older = new_run(bridge.backend, task="Execução anterior")
    latest = new_run(bridge.backend, task="Execução atual")
    for index in range(24):
        bridge.backend.bus.publish(
            "task.delegated", run_id=latest.id, session_id=latest.session_id,
            agent_id=f"worker-{index}", message="Tarefa longa " + ("x" * 250),
        )
    bridge._handle_update(update("/atividade"))
    cards = [body for method, body in sent if method == "sendMessage" and body.get("reply_markup")]
    assert len(cards) == 2
    assert any(older.id in card["text"] for card in cards)
    assert any(latest.id in card["text"] for card in cards)
    latest_card = next(card for card in cards if latest.id in card["text"])
    assert "agente(s) omitido(s)" in latest_card["text"]
    assert all(len(body["text"]) < 4096 for body in cards)
    tokens = [body["reply_markup"]["inline_keyboard"][0][0]["callback_data"] for body in cards]
    assert len({bridge._activity_callbacks[token[4:]]["run_id"] for token in tokens}) == 2


def test_activity_live_events_use_one_subscriber_and_coalesce_to_terminal_summary(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    bus = bridge.backend.bus
    assert bridge.backend.router._event_bus is bus
    assert len(bus._subscribers["*"]) == 1
    assert bridge._activity_callbacks[token[4:]]["live"] is True

    timers = []

    class FakeTimer:
        def __init__(self, delay, function, args=(), kwargs=None):
            self.delay = delay
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr("bauer.telegram_bridge.threading.Timer", FakeTimer)
    bridge.backend.router._event_bus.publish(
        "agent.started", run_id=run.id, session_id=run.session_id, agent_id="reviewer",
    )
    bridge.backend.router._event_bus.publish(
        "tool.call.requested", run_id=run.id, session_id=run.session_id, tool_name="run_command",
    )
    assert len(timers) == 1

    bridge.backend.runs.complete_run(run.id, output={"response": "done"}, cost_estimate=0.25, usage_known=True)
    assert len(timers) == 1
    bridge._activity_callbacks[token[4:]]["last_edit"] = time.monotonic() - 10
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    edit = [body for method, body in sent if method == "editMessageText"][-1]
    assert "Concluída" in edit["text"]
    assert "Custo: $0.250000" in edit["text"]
    assert bridge._activity_callbacks[token[4:]]["live"] is False


def test_activity_manual_refresh_clicks_share_interval_limited_timer(transport, monkeypatch):
    bridge, sent = transport
    new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = []

    class FakeTimer:
        def __init__(self, delay, function, args=(), kwargs=None):
            self.delay = delay
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr("bauer.telegram_bridge.threading.Timer", FakeTimer)
    bridge._activity_callback(callback(token))
    bridge._activity_callback(callback(token))
    bridge._activity_callback(callback(token))
    assert len(timers) == 1
    assert timers[0].delay >= 2.0
    assert not any(method == "editMessageText" for method, _ in sent)
    bridge._activity_callbacks[token[4:]]["last_edit"] = time.monotonic() - 10
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    assert len([row for row in sent if row[0] == "editMessageText"]) == 1


def test_activity_coalescer_does_not_reschedule_while_edit_is_inflight(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = []
    entered = threading.Event()
    release = threading.Event()

    class FakeTimer:
        def __init__(self, delay, function, args=(), kwargs=None):
            self.delay = delay
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr("bauer.telegram_bridge.threading.Timer", FakeTimer)
    original_api = bridge._api

    def blocking_api(method, **kwargs):
        if method == "editMessageText":
            entered.set()
            assert release.wait(3)
        return original_api(method, **kwargs)

    monkeypatch.setattr(bridge, "_api", blocking_api)
    record = bridge._activity_callbacks[token[4:]]
    record["last_edit"] = time.monotonic() - 10
    bridge.backend.router._event_bus.publish(
        "tool.call.requested", run_id=run.id, session_id=run.session_id, tool_name="read_file",
    )
    assert len(timers) == 1
    worker = threading.Thread(target=timers[0].function, args=timers[0].args, kwargs=timers[0].kwargs)
    worker.start()
    assert entered.wait(2)
    assert record["inflight"] is True
    # Simulate elapsed edit interval while the HTTP call remains blocked.
    record["last_edit"] = time.monotonic() - 10
    bridge.backend.router._event_bus.publish(
        "tool.call.requested", run_id=run.id, session_id=run.session_id, tool_name="read_file",
    )
    assert len(timers) == 1
    # Firing the due timer while the edit lock is held must not create a new
    # immediate timer; repeated events remain a single dirty bit.
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    for _ in range(4):
        bridge.backend.router._event_bus.publish(
            "tool.call.completed", run_id=run.id, session_id=run.session_id, tool_name="read_file",
        )
    assert len(timers) == 1
    assert record["dirty"] is True
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert record["inflight"] is False
    assert len(timers) == 2
    assert timers[1].delay > 2.0


def test_terminal_edit_failure_sends_summary_once_and_stops_watch(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = []

    class FakeTimer:
        def __init__(self, delay, function, args=(), kwargs=None):
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr("bauer.telegram_bridge.threading.Timer", FakeTimer)
    bridge.backend.runs.complete_run(run.id, output={"response": "done"}, usage_known=False)
    assert len(timers) == 1
    bridge._activity_callbacks[token[4:]]["last_edit"] = time.monotonic() - 10
    original_api = bridge._api

    def fail_edit(method, **kwargs):
        if method == "editMessageText":
            raise RuntimeError("Telegram edit unavailable")
        return original_api(method, **kwargs)

    monkeypatch.setattr(bridge, "_api", fail_edit)
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    record = bridge._activity_callbacks[token[4:]]
    assert record["live"] is False
    assert record["refresh_enabled"] is False
    assert len([row for row in sent if row[0] == "sendMessage"]) == 2
    bridge.backend.router._event_bus.publish(
        "run.failed", run_id=run.id, session_id=run.session_id, status="failed",
    )
    assert len(timers) == 1
    assert len([row for row in sent if row[0] == "sendMessage"]) == 2


def test_terminal_watch_continues_after_refresh_callback_expires(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = capture_timers(monkeypatch)
    record = bridge._activity_callbacks[token[4:]]
    record["callback_expires_at"] = time.monotonic() - 1
    record["last_edit"] = time.monotonic() - 10

    bridge.backend.runs.complete_run(run.id, output={"response": "done"}, usage_known=False)
    assert len(timers) == 1
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    edit = [body for method, body in sent if method == "editMessageText"][-1]
    assert "Concluída" in edit["text"]
    assert record["live"] is False
    # The expired opaque token cannot request another refresh.
    bridge._activity_callback(callback(token))
    assert len(timers) == 1


def test_stop_cancels_activity_timer_and_blocks_subscriber_http(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = capture_timers(monkeypatch)
    record = bridge._activity_callbacks[token[4:]]
    record["last_edit"] = time.monotonic() - 10
    bridge.backend.router._event_bus.publish(
        "tool.call.requested", run_id=run.id, session_id=run.session_id, tool_name="read_file",
    )
    assert len(timers) == 1
    sent_before_stop = len(sent)

    bridge.stop()
    assert timers[0].cancelled is True
    assert bridge._activity_callbacks == {}
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    bridge.backend.router._event_bus.publish(
        "run.completed", run_id=run.id, session_id=run.session_id,
    )
    assert len(timers) == 1
    assert len(sent) == sent_before_stop


def test_reset_reopens_telegram_resources_without_duplicate_activity_subscriber(
    transport, monkeypatch,
):
    bridge, sent = transport
    run = new_run(bridge.backend)
    activity_card(bridge, sent)
    bus = bridge.backend.router._event_bus
    assert len(bus._subscribers["*"]) == 1

    bridge.stop()
    bridge.reset()
    assert bridge.stopped is False
    assert bridge._activity_shutdown is False
    assert bridge._http.is_closed is False
    assert bridge._executor.submit(lambda: "ready").result(timeout=2) == "ready"

    def handler(request):
        sent.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 71}})

    bridge._http.close()
    bridge._http = httpx.Client(transport=httpx.MockTransport(handler))
    _, token = activity_card(bridge, sent)
    assert token
    assert len(bus._subscribers["*"]) == 1

    timers = capture_timers(monkeypatch)
    bridge._activity_callbacks[token[4:]]["last_edit"] = time.monotonic() - 10
    bus.publish("agent.started", run_id=run.id, session_id=run.session_id, agent_id="worker")
    assert len(timers) == 1
    bridge.stop()


def test_stop_serializes_initial_activity_send_and_suppresses_later_fallbacks(
    transport, monkeypatch,
):
    bridge, sent = transport
    new_run(bridge.backend)
    entered = threading.Event()
    release = threading.Event()
    stop_started = threading.Event()
    stop_done = threading.Event()
    original_api = bridge._api

    def blocking_api(method, **params):
        if method == "sendMessage":
            entered.set()
            assert release.wait(3)
        return original_api(method, **params)

    monkeypatch.setattr(bridge, "_api", blocking_api)
    sender = threading.Thread(target=bridge._handle_update, args=(update("/atividade"),))
    sender.start()
    assert entered.wait(2)

    def stop_bridge():
        stop_started.set()
        bridge.stop()
        stop_done.set()

    stopper = threading.Thread(target=stop_bridge)
    stopper.start()
    assert stop_started.wait(2)
    # stop waits for the in-flight send; once it returns, no later send can
    # pass the same lock, including the method's generic failure fallback.
    release.set()
    sender.join(timeout=3)
    stopper.join(timeout=3)
    assert not sender.is_alive() and not stopper.is_alive()
    assert stop_done.is_set()
    sent_before = len(sent)
    bridge._handle_update(update("/atividade"))
    assert len(sent) == sent_before
    assert len([row for row in sent if row[0] == "sendMessage"]) == 1


def test_refresh_started_before_stop_cannot_edit_old_card_after_reset(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = capture_timers(monkeypatch)
    record = bridge._activity_callbacks[token[4:]]
    record["last_edit"] = time.monotonic() - 10
    bridge.backend.router._event_bus.publish(
        "tool.call.requested", run_id=run.id, session_id=run.session_id, tool_name="read_file",
    )
    assert len(timers) == 1

    context_entered = threading.Event()
    release_context = threading.Event()
    original_context = bridge._activity_context

    def blocked_context(*args, **kwargs):
        context_entered.set()
        assert release_context.wait(3)
        return original_context(*args, **kwargs)

    monkeypatch.setattr(bridge, "_activity_context", blocked_context)
    worker = threading.Thread(target=timers[0].function, args=timers[0].args, kwargs=timers[0].kwargs)
    worker.start()
    assert context_entered.wait(2)

    bridge.stop()
    bridge.reset()
    sent_before_release = len(sent)
    release_context.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert len([row for row in sent if row[0] == "editMessageText"]) == 0
    assert len(sent) == sent_before_release


def test_telegram_edit_failure_does_not_mutate_run_or_kernel_state(transport, monkeypatch):
    bridge, sent = transport
    run = new_run(bridge.backend)
    _, token = activity_card(bridge, sent)
    timers = capture_timers(monkeypatch)
    before_run = bridge.backend.runs.get_run(run.id)
    before_events = bridge.backend.bus.list_events(run_id=run.id)

    def fail_edit(method, **kwargs):
        if method == "editMessageText":
            raise RuntimeError("network unavailable")
        return {"message_id": 71}

    monkeypatch.setattr(bridge, "_api", fail_edit)
    bridge._handle_callback(callback(token))
    assert len(timers) == 1
    timers[0].function(*timers[0].args, **timers[0].kwargs)
    assert bridge.backend.runs.get_run(run.id) == before_run
    assert bridge.backend.bus.list_events(run_id=run.id) == before_events
