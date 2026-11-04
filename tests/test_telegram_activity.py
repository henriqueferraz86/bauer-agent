"""Hermetic tests for bauer/telegram_activity.py (sprint 45, plano 061 Etapa 3).

Segue o padrão de tests/test_chat_activity.py: RunManager+EventBus REAIS sobre
tmp_path geram os dados; só a camada HTTP do Telegram (`_api`) e
`AgentBackend._resolver_kernel` são fake.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bauer import telegram_activity as ta
from bauer.channel_base import ChannelMessage
from bauer.core.events import EventBus
from bauer.core.runtime.run_manager import RunManager
from bauer.telegram_bridge import TelegramBridge
from bauer.usage_pricing import format_cost


# ── fixtures/fábricas ────────────────────────────────────────────────────────


@pytest.fixture
def runtime(tmp_path):
    bus = EventBus(root=tmp_path / "runtime")
    runs = RunManager(store=bus.store, event_bus=bus)
    return runs, bus


def new_run(runs, *, sid="tg:42", agent="serve.chat", task="Revisar o PR", status="running"):
    return runs.create_run(session_id=sid, agent_id=agent, status=status, input={"message": task})


def kernel_of(runtime):
    runs, bus = runtime
    return SimpleNamespace(runs=runs, bus=bus)


def make_backend(kernel, *, session_id="tg:42", context=None, workspace="/workspace"):
    backend = SimpleNamespace()
    backend.is_ready = True
    backend._router = SimpleNamespace(workspace=workspace)
    backend.capture_project_context = Mock(return_value=context)
    backend.session_key = Mock(return_value=session_id)
    backend._resolver_kernel = Mock(return_value=kernel)
    backend.initialize = Mock()
    return backend


def private_msg(*, chat_id="42", user_id="42", text="/atividade"):
    return ChannelMessage(
        channel="telegram", user_id=user_id, chat_id=chat_id, text=text,
        raw={"message": {"chat": {"id": int(chat_id), "type": "private"}, "from": {"id": int(user_id)}}},
    )


def group_msg(*, chat_id="42", user_id="42", text="/atividade"):
    return ChannelMessage(
        channel="telegram", user_id=user_id, chat_id=chat_id, text=text,
        raw={"message": {"chat": {"id": int(chat_id), "type": "group"}, "from": {"id": int(user_id)}}},
    )


class FakeBridge:
    """Fake bridge: campos reais de TelegramBridge para callbacks/lock; só
    `_api`/`send_text` são fake (camada HTTP do Telegram, nunca rede real)."""

    def __init__(self, backend):
        self.backend = backend
        self.send_text = Mock()
        self.api_calls: list[tuple[str, dict]] = []
        self._activity_callbacks: dict[str, dict] = {}
        self._activity_callback_lock = threading.Lock()
        self._next_message_id = 1000
        self._api_raise_on: list[bool] = []  # fila de "deve levantar?" por chamada de _api
        self._cancel_run_or_loop = Mock(return_value="Cancelamento solicitado para run-x.")

    def _api(self, method: str, **kwargs):
        self.api_calls.append((method, kwargs))
        if self._api_raise_on:
            should_raise = self._api_raise_on.pop(0)
            if should_raise:
                raise RuntimeError("simulated Telegram failure")
        if method == "sendMessage":
            mid = self._next_message_id
            self._next_message_id += 1
            return {"message_id": mid}
        return {}

    def edit_calls(self) -> list[dict]:
        return [kwargs for method, kwargs in self.api_calls if method == "editMessageText"]


def no_real_thread(monkeypatch):
    """Impede send_activity_card de abrir uma thread observadora REAL —
    testes que não exercitam o observador não devem depender de timing real.
    """
    captured: list[dict] = []

    class _FakeThread:
        def __init__(self, *, target, kwargs, daemon=None, name=None):
            captured.append(kwargs)
            self.target = target
            self.kwargs = kwargs

        def start(self) -> None:
            pass

    monkeypatch.setattr(ta.threading, "Thread", _FakeThread)
    return captured


# ── render_activity_card (pura) ─────────────────────────────────────────────


def test_render_card_shows_unknown_cost_never_zero(runtime):
    runs, bus = runtime
    run = new_run(runs)
    envelope = ta.build_chat_activity(runs, bus, "tg:42")
    proj = envelope["runs"][0]
    text = ta.render_activity_card(proj, run)
    assert "Custo: desconhecido" in text
    assert "US$ 0,00" not in text and "$0.00" not in text


def test_render_card_shows_known_cost(runtime):
    runs, bus = runtime
    run = new_run(runs)
    runs.update_run(run.id, cost_estimate=0.42, usage_known=True)
    run = runs.get_run(run.id)
    envelope = ta.build_chat_activity(runs, bus, "tg:42")
    proj = envelope["runs"][0]
    text = ta.render_activity_card(proj, run)
    assert f"Custo registrado: {format_cost(0.42)}" in text
    assert "desconhecido" not in text


def test_render_card_truncates_task_and_includes_project_name(runtime):
    runs, bus = runtime
    run = new_run(runs, task="Implementar o pedido do usuário")
    envelope = ta.build_chat_activity(runs, bus, "tg:42")
    proj = envelope["runs"][0]
    text = ta.render_activity_card(proj, run, project_name="investimentosOS")
    assert text.startswith("Projeto: investimentosOS")
    assert "Tarefa: Implementar o pedido do usuário" in text
    assert "Estado:" in text


def test_render_card_without_envelope_is_friendly_not_crash():
    text = ta.render_activity_card(None, None)
    assert "não encontrada" in text.lower()


def test_render_card_two_delegations_to_same_agent_are_separate_lines(runtime):
    runs, bus = runtime
    run = new_run(runs, agent="coordinator")
    for number in (1, 2):
        data = {"delegation_id": f"delegation-{number}", "agent_name": "Revisor",
                "task": f"Revisar tarefa {number}"}
        bus.publish("task.delegated", run_id=run.id, session_id="tg:42", agent_id="reviewer",
                    data=data, status="delegated")
        bus.publish("agent.started", run_id=run.id, session_id="tg:42", agent_id="reviewer", data=data)
    envelope = ta.build_chat_activity(runs, bus, "tg:42")
    proj = envelope["runs"][0]
    text = ta.render_activity_card(proj, run)
    assert text.count("Revisor —") == 2


# ── send_activity_card ───────────────────────────────────────────────────────


def test_send_activity_card_refuses_outside_private_chat(runtime):
    runs, bus = runtime
    new_run(runs)
    kernel = kernel_of(runtime)
    backend = make_backend(kernel)
    bridge = FakeBridge(backend)
    ta.send_activity_card(bridge, group_msg())
    bridge.send_text.assert_called_once()
    assert "privada" in bridge.send_text.call_args.args[1]
    backend._resolver_kernel.assert_not_called()


def test_send_activity_card_no_active_run_is_friendly_without_keyboard(runtime):
    runs, bus = runtime
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    ta.send_activity_card(bridge, private_msg())
    bridge.send_text.assert_called_once_with("42", "Nenhuma execução ativa ou recente nesta conversa.")
    assert bridge.api_calls == []


def test_send_activity_card_kernel_disabled_is_friendly_not_crash(runtime):
    runs, bus = runtime
    new_run(runs)
    backend = make_backend(None, session_id="tg:42")
    backend._resolver_kernel = Mock(return_value=None)
    bridge = FakeBridge(backend)
    ta.send_activity_card(bridge, private_msg())
    bridge.send_text.assert_called_once()
    assert "indisponível" in bridge.send_text.call_args.args[1].lower()


def test_send_activity_card_sends_card_with_keyboard_for_non_terminal_run(runtime, monkeypatch):
    threads = no_real_thread(monkeypatch)
    runs, bus = runtime
    run = new_run(runs, status="running")
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    ta.send_activity_card(bridge, private_msg())
    assert len(bridge.api_calls) == 1
    method, kwargs = bridge.api_calls[0]
    assert method == "sendMessage"
    assert kwargs["chat_id"] == "42"
    assert "Tarefa:" in kwargs["text"]
    rows = kwargs["reply_markup"]["inline_keyboard"][0]
    assert any(b["text"] == "Atualizar" for b in rows)
    assert any(b["text"] == "Cancelar tarefa" for b in rows)
    assert len(threads) == 1  # observador agendado
    assert threads[0]["run_id"] == run.id


def test_send_activity_card_terminal_run_has_no_keyboard_and_no_watcher(runtime, monkeypatch):
    threads = no_real_thread(monkeypatch)
    runs, bus = runtime
    new_run(runs, status="completed")
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    ta.send_activity_card(bridge, private_msg())
    method, kwargs = bridge.api_calls[0]
    assert "reply_markup" not in kwargs
    assert threads == []


def test_send_activity_card_sessions_never_cross(runtime, monkeypatch):
    no_real_thread(monkeypatch)
    runs, bus = runtime
    new_run(runs, sid="tg:1", task="tarefa do chat 1", status="running")
    new_run(runs, sid="tg:2", task="tarefa do chat 2 PRIVADA", status="running")
    kernel = kernel_of(runtime)

    backend1 = make_backend(kernel, session_id="tg:1")
    bridge1 = FakeBridge(backend1)
    ta.send_activity_card(bridge1, private_msg(chat_id="1", user_id="1"))
    _, kwargs1 = bridge1.api_calls[0]
    assert "tarefa do chat 1" in kwargs1["text"]
    assert "PRIVADA" not in kwargs1["text"]

    backend2 = make_backend(kernel, session_id="tg:2")
    bridge2 = FakeBridge(backend2)
    ta.send_activity_card(bridge2, private_msg(chat_id="2", user_id="2"))
    _, kwargs2 = bridge2.api_calls[0]
    assert "tarefa do chat 2 PRIVADA" in kwargs2["text"]
    assert "chat 1" not in kwargs2["text"]


# ── observador (_watch_activity) ────────────────────────────────────────────


def test_watch_activity_no_change_skips_edit_then_edits_on_real_change(runtime, monkeypatch):
    runs, bus = runtime
    run = new_run(runs, status="running")
    kernel = kernel_of(runtime)
    envelope = ta.build_chat_activity(runs, bus, "tg:42", run_id=run.id)
    initial_text = ta.render_activity_card(envelope["runs"][0], run)

    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)

    calls = {"n": 0}

    def fake_sleep(_interval):
        calls["n"] += 1
        if calls["n"] == 1:
            return  # primeiro tick: nada muda
        if calls["n"] == 2:
            runs.update_run(run.id, status="completed")  # segundo tick: vira terminal

    monkeypatch.setattr(ta.time, "sleep", fake_sleep)

    ta._watch_activity(
        bridge=bridge, chat_id="42", message_id=999, session_id="tg:42", run_id=run.id,
        workspace="/workspace", project_name=None, last_rendered=initial_text,
        deadline_monotonic=time.monotonic() + 3600,
    )

    edits = bridge.edit_calls()
    assert len(edits) == 1  # só a edição final — o 1º tick sem mudança não editou
    assert edits[0]["reply_markup"] == {"inline_keyboard": []}


def test_watch_activity_intermediate_failure_never_blocks_final_edit(runtime, monkeypatch):
    """Requisito 7 do SPEC: falha numa edição intermediária NUNCA impede a
    edição final do resumo terminal — nunca herda o padrão "desisti para
    sempre" do `_StreamingDraft`."""
    runs, bus = runtime
    run = new_run(runs, status="running")
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    # 1ª chamada de editMessageText (tick intermediário) levanta; a 2ª
    # (edição final, incondicional) tem que ser TENTADA mesmo assim.
    bridge._api_raise_on = [True]

    calls = {"n": 0}

    def fake_sleep(_interval):
        calls["n"] += 1
        if calls["n"] == 2:
            runs.update_run(run.id, status="completed")

    monkeypatch.setattr(ta.time, "sleep", fake_sleep)

    ta._watch_activity(
        bridge=bridge, chat_id="42", message_id=999, session_id="tg:42", run_id=run.id,
        workspace="/workspace", project_name=None, last_rendered="",  # força mudança no 1º tick
        deadline_monotonic=time.monotonic() + 3600,
    )

    edits = bridge.edit_calls()
    # A 1ª tentativa (intermediária) falhou e não aparece como edição bem-
    # sucedida registrada por _api — mas _api FOI chamada (e levantou);
    # confirmamos que o laço prosseguiu e a edição final ocorreu de fato.
    assert len(edits) == 2  # intermediária (que o fake registra antes de levantar) + final
    assert edits[-1]["reply_markup"] == {"inline_keyboard": []}
    assert calls["n"] == 2


def test_watch_activity_timeout_stops_even_when_not_terminal(runtime, monkeypatch):
    runs, bus = runtime
    run = new_run(runs, status="running")  # nunca chega a terminal
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)

    monkeypatch.setattr(ta.time, "sleep", lambda _interval: None)

    ta._watch_activity(
        bridge=bridge, chat_id="42", message_id=999, session_id="tg:42", run_id=run.id,
        workspace="/workspace", project_name=None, last_rendered="",
        deadline_monotonic=time.monotonic() - 1,  # teto já vencido
    )

    edits = bridge.edit_calls()
    assert len(edits) == 1
    assert edits[0]["reply_markup"] == {"inline_keyboard": []}
    assert "2h" in edits[0]["text"] or "Acompanhamento encerrado" in edits[0]["text"]


def test_watch_activity_run_disappearing_stops_cleanly(runtime, monkeypatch):
    runs, bus = runtime
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    monkeypatch.setattr(ta.time, "sleep", lambda _interval: None)

    ta._watch_activity(
        bridge=bridge, chat_id="42", message_id=999, session_id="tg:42",
        run_id="run-does-not-exist", workspace="/workspace", project_name=None,
        last_rendered="", deadline_monotonic=time.monotonic() + 3600,
    )
    assert bridge.edit_calls() == []  # some sem crashar, sem editar nada


# ── botões (handle_activity_callback) ───────────────────────────────────────


def _cq(*, token: str, action: str, chat_id="42", owner="42", message_id=999):
    return {
        "id": "cqid", "data": f"act:{action}:{token}",
        "from": {"id": int(owner)},
        "message": {"message_id": message_id, "chat": {"id": int(chat_id)}},
    }


def test_refresh_button_valid_token_reedits(runtime, monkeypatch):
    runs, bus = runtime
    run = new_run(runs, status="running")
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    bridge._activity_callbacks["tok1"] = {
        "owner": "42", "chat_id": "42", "message_id": 999, "run_id": run.id,
        "session_id": "tg:42", "workspace": "/workspace", "project_name": None,
        "expires_at": time.monotonic() + 600,
    }
    handled = ta.handle_activity_callback(bridge, _cq(token="tok1", action="refresh"))
    assert handled is True
    edits = bridge.edit_calls()
    assert len(edits) == 1
    assert edits[0]["message_id"] == 999
    # token de "Atualizar" não é consumido — permanece válido para novo clique
    assert "tok1" in bridge._activity_callbacks


def test_cancel_button_consumes_token_and_edits_once(runtime):
    runs, bus = runtime
    run = new_run(runs, status="running")
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    bridge._activity_callbacks["tok2"] = {
        "owner": "42", "chat_id": "42", "message_id": 999, "run_id": run.id,
        "session_id": "tg:42", "workspace": "/workspace", "project_name": None,
        "expires_at": time.monotonic() + 600,
    }
    handled = ta.handle_activity_callback(bridge, _cq(token="tok2", action="cancel"))
    assert handled is True
    bridge._cancel_run_or_loop.assert_called_once()
    msg_arg, run_id_arg = bridge._cancel_run_or_loop.call_args.args
    assert run_id_arg == run.id
    assert msg_arg.chat_id == "42" and msg_arg.user_id == "42"
    edits = bridge.edit_calls()
    assert len(edits) == 1
    assert edits[0]["reply_markup"] == {"inline_keyboard": []}
    assert "tok2" not in bridge._activity_callbacks  # one-shot


@pytest.mark.parametrize("mutate", [
    lambda rec: {**rec, "expires_at": time.monotonic() - 1},
    lambda rec: {**rec, "chat_id": "99"},
    lambda rec: {**rec, "owner": "99"},
    lambda rec: {**rec, "message_id": 1},
])
def test_button_ignores_invalid_token_variants(runtime, mutate):
    runs, bus = runtime
    run = new_run(runs, status="running")
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    base = {
        "owner": "42", "chat_id": "42", "message_id": 999, "run_id": run.id,
        "session_id": "tg:42", "workspace": "/workspace", "project_name": None,
        "expires_at": time.monotonic() + 600,
    }
    bridge._activity_callbacks["tok3"] = mutate(base)
    handled = ta.handle_activity_callback(bridge, _cq(token="tok3", action="refresh"))
    assert handled is True
    assert bridge.edit_calls() == []
    bridge._cancel_run_or_loop.assert_not_called()


def test_button_ignores_unknown_token(runtime):
    runs, bus = runtime
    kernel = kernel_of(runtime)
    backend = make_backend(kernel, session_id="tg:42")
    bridge = FakeBridge(backend)
    handled = ta.handle_activity_callback(bridge, _cq(token="ghost", action="refresh"))
    assert handled is True
    assert bridge.edit_calls() == []


# ── paridade do botão "Cancelar tarefa" com /cancelar <run_id> (real bridge) ─


def test_cancel_button_matches_cancelar_command_behavior(tmp_path):
    """Mesmo helper (_cancel_run_or_loop), reusado byte-a-byte pelo comando e
    pelo botão — comportamento observável idêntico ao de /cancelar."""
    backend = SimpleNamespace(cancel_channel_execution=Mock(return_value="Cancelamento solicitado"))
    bridge = TelegramBridge(token="fake", backend=backend, allowed_users=[42], state_dir=tmp_path)
    try:
        response_cmd = bridge._cancel_run_or_loop(
            ChannelMessage(channel="telegram", user_id="42", chat_id="42", text="/cancelar run-1"),
            "run-1",
        )
        response_button = bridge._cancel_run_or_loop(
            ChannelMessage(channel="telegram", user_id="42", chat_id="42", text=""),
            "run-1",
        )
        assert response_cmd == response_button == "Cancelamento solicitado"
        assert backend.cancel_channel_execution.call_count == 2
    finally:
        bridge.stop()
