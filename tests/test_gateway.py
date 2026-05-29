"""tests/test_gateway.py — Testes unitários do gateway WebSocket.

Cobre:
  - Frame builders: _res_ok, _res_err, _event, _hello_ok
  - _ConnState: next_seq, get_history, reset_history, active_runs
  - _parse_sse_lines: parsing de SSE
  - _handle_method: wake, config.get, agents.list, models.list,
                    sessions.list/preview/reset/patch, chat.history,
                    chat.abort, agent.wait, status, method desconhecido
  - _run_chat: bridge SSE com HTTP mockado (delta, final, abort, error)
  - _client_handler: handshake completo (connect.challenge → connect → hello-ok)
  - public API: run_gateway levanta RuntimeError sem websockets,
               start_gateway_thread sobe daemon thread
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bauer.gateway import (
    ADAPTER_TYPE,
    AGENT_ID,
    AGENT_NAME,
    DEFAULT_SESSION_SLOT,
    HEARTBEAT_INTERVAL_S,
    PROTOCOL_VERSION,
    _ConnState,
    _event,
    _handle_method,
    _hello_ok,
    _parse_sse_lines,
    _res_err,
    _res_ok,
    _run_chat,
    _client_handler,
    run_gateway,
    start_gateway_thread,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_async(coro):
    """Roda uma coroutine em um event loop temporário."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------


class TestResOk:
    def test_structure(self):
        frame = _res_ok("abc", {"x": 1})
        assert frame == {"type": "res", "id": "abc", "ok": True, "payload": {"x": 1}}

    def test_none_payload(self):
        frame = _res_ok("id1", None)
        assert frame["ok"] is True
        assert frame["payload"] is None

    def test_list_payload(self):
        frame = _res_ok("r2", [1, 2, 3])
        assert frame["payload"] == [1, 2, 3]


class TestResErr:
    def test_structure(self):
        frame = _res_err("req1", "not_found", "not found")
        assert frame["type"] == "res"
        assert frame["id"] == "req1"
        assert frame["ok"] is False
        assert frame["error"]["code"] == "not_found"
        assert frame["error"]["message"] == "not found"


class TestEvent:
    def test_structure(self):
        frame = _event("heartbeat", 5, {"ts": 12345})
        assert frame["type"] == "event"
        assert frame["event"] == "heartbeat"
        assert frame["seq"] == 5
        assert frame["payload"]["ts"] == 12345

    def test_chat_event(self):
        frame = _event("chat", 0, {"state": "delta", "message": {"content": "hi"}})
        assert frame["event"] == "chat"
        assert frame["payload"]["state"] == "delta"


class TestHelloOk:
    def test_type_and_protocol(self):
        frame = _hello_ok("req-99")
        assert frame["type"] == "res"
        assert frame["id"] == "req-99"
        assert frame["ok"] is True
        payload = frame["payload"]
        assert payload["type"] == "hello-ok"
        assert payload["protocol"] == PROTOCOL_VERSION
        assert payload["adapterType"] == ADAPTER_TYPE

    def test_features_present(self):
        payload = _hello_ok("x")["payload"]
        methods = payload["features"]["methods"]
        assert "chat.send" in methods
        assert "agents.list" in methods
        assert "sessions.reset" in methods

    def test_snapshot_health(self):
        payload = _hello_ok("x")["payload"]
        agents = payload["snapshot"]["health"]["agents"]
        assert any(a["agentId"] == AGENT_ID for a in agents)

    def test_policy_tick(self):
        payload = _hello_ok("x")["payload"]
        assert payload["policy"]["tickIntervalMs"] == HEARTBEAT_INTERVAL_S * 1000


# ---------------------------------------------------------------------------
# _ConnState
# ---------------------------------------------------------------------------


class TestConnState:
    def test_initial_state(self):
        s = _ConnState()
        assert s.connected is False
        assert s.seq == 0
        assert s.histories == {}
        assert s.active_runs == {}

    def test_next_seq_increments(self):
        s = _ConnState()
        assert s.next_seq() == 0
        assert s.next_seq() == 1
        assert s.next_seq() == 2
        assert s.seq == 3

    def test_get_history_creates_empty(self):
        s = _ConnState()
        h = s.get_history("key1")
        assert h == []
        assert "key1" in s.histories

    def test_get_history_returns_same_list(self):
        s = _ConnState()
        h1 = s.get_history("k")
        h1.append({"role": "user", "content": "hello"})
        h2 = s.get_history("k")
        assert len(h2) == 1

    def test_reset_history(self):
        s = _ConnState()
        h = s.get_history("session1")
        h.append({"role": "user", "content": "msg"})
        s.reset_history("session1")
        assert s.get_history("session1") == []

    def test_reset_nonexistent_key_is_empty(self):
        s = _ConnState()
        s.reset_history("nonexistent")
        assert s.get_history("nonexistent") == []


# ---------------------------------------------------------------------------
# _parse_sse_lines
# ---------------------------------------------------------------------------


class TestParseSSELines:
    def test_simple_data(self):
        raw = "data: hello\n\n"
        events = list(_parse_sse_lines(raw))
        assert events == [("message", "hello")]

    def test_multiple_events(self):
        raw = "data: first\n\ndata: second\n\n"
        events = list(_parse_sse_lines(raw))
        assert events == [("message", "first"), ("message", "second")]

    def test_event_field(self):
        raw = "event: chat\ndata: payload\n\n"
        events = list(_parse_sse_lines(raw))
        assert events == [("chat", "payload")]

    def test_done_sentinel(self):
        raw = "data: [DONE]\n\n"
        events = list(_parse_sse_lines(raw))
        assert events == [("message", "[DONE]")]

    def test_empty_raw(self):
        events = list(_parse_sse_lines(""))
        assert events == []

    def test_multiline_data(self):
        raw = "data: line1\ndata: line2\n\n"
        events = list(_parse_sse_lines(raw))
        assert len(events) == 1
        assert "line1" in events[0][1]

    def test_no_trailing_newline(self):
        raw = "data: noeol"
        events = list(_parse_sse_lines(raw))
        assert events == [("message", "noeol")]


# ---------------------------------------------------------------------------
# _handle_method (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestHandleMethod:
    """Testa cada método do dispatcher."""

    async def _call(self, method, params=None, req_id="r1", bauer_url="http://localhost:7770", api_key=""):
        state = _ConnState()
        state.connected = True
        frames = []

        async def send_frame(f):
            frames.append(f)

        result = await _handle_method(method, params or {}, req_id, state, send_frame, bauer_url, api_key)
        return result, frames, state

    async def test_wake(self):
        result, _, _ = await self._call("wake")
        assert result["ok"] is True
        assert result["payload"]["ok"] is True

    async def test_config_get(self):
        result, _, _ = await self._call("config.get")
        assert result["ok"] is True
        payload = result["payload"]
        assert "model" in payload
        assert payload["model"]["provider"] == "bauer"

    async def test_agents_list(self):
        result, _, _ = await self._call("agents.list")
        assert result["ok"] is True
        agents = result["payload"]["agents"]
        assert len(agents) == 1
        assert agents[0]["id"] == AGENT_ID
        assert result["payload"]["defaultId"] == AGENT_ID

    async def test_sessions_list_empty(self):
        result, _, _ = await self._call("sessions.list")
        assert result["ok"] is True
        assert result["payload"]["sessions"] == []

    async def test_sessions_list_with_history(self):
        state = _ConnState()
        state.connected = True
        key = "agent:bauer:main"
        state.get_history(key).append({"role": "user", "content": "hi"})

        frames = []
        async def send_frame(f): frames.append(f)

        result = await _handle_method("sessions.list", {}, "r1", state, send_frame, "http://x", "")
        sessions = result["payload"]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["key"] == key
        assert sessions[0]["messageCount"] == 1

    async def test_sessions_preview_empty(self):
        result, _, _ = await self._call("sessions.preview", {"key": "nokey"})
        assert result["ok"] is True
        assert result["payload"]["preview"] == ""

    async def test_sessions_preview_with_content(self):
        state = _ConnState()
        state.connected = True
        key = "agent:bauer:main"
        state.get_history(key).append({"role": "assistant", "content": "A" * 200})

        frames = []
        async def send_frame(f): frames.append(f)

        result = await _handle_method("sessions.preview", {"key": key}, "r1", state, send_frame, "http://x", "")
        preview = result["payload"]["preview"]
        assert len(preview) == 120   # truncado

    async def test_sessions_reset(self):
        state = _ConnState()
        key = "session-abc"
        state.get_history(key).append({"role": "user", "content": "msg"})

        frames = []
        async def send_frame(f): frames.append(f)

        result = await _handle_method("sessions.reset", {"key": key}, "r1", state, send_frame, "http://x", "")
        assert result["ok"] is True
        assert state.get_history(key) == []

    async def test_sessions_patch(self):
        result, _, _ = await self._call("sessions.patch", {"model": "gpt-4"})
        assert result["ok"] is True

    async def test_chat_history_empty(self):
        result, _, _ = await self._call("chat.history", {"sessionKey": "k"})
        assert result["ok"] is True
        assert result["payload"]["messages"] == []

    async def test_chat_history_default_key(self):
        result, _, _ = await self._call("chat.history", {})
        assert result["payload"]["sessionKey"] == f"agent:{AGENT_ID}:{DEFAULT_SESSION_SLOT}"

    async def test_chat_abort_no_runs(self):
        result, _, _ = await self._call("chat.abort", {"runId": "nonexistent"})
        assert result["ok"] is True
        assert result["payload"]["aborted"] == 0

    async def test_chat_abort_by_run_id(self):
        state = _ConnState()
        state.connected = True
        run_id = "run-abc"

        async def _dummy():
            await asyncio.sleep(999)

        task = asyncio.create_task(_dummy())
        state.active_runs[run_id] = task

        frames = []
        async def send_frame(f): frames.append(f)

        result = await _handle_method("chat.abort", {"runId": run_id}, "r1", state, send_frame, "http://x", "")
        assert result["payload"]["aborted"] == 1
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_chat_abort_by_session_key(self):
        state = _ConnState()
        state.connected = True
        session_key = "agent:bauer:main"
        run_id = "r123"

        async def _dummy():
            await asyncio.sleep(999)

        task = asyncio.create_task(_dummy())
        state.active_runs[run_id] = task

        frames = []
        async def send_frame(f): frames.append(f)

        result = await _handle_method("chat.abort", {"sessionKey": session_key}, "r1", state, send_frame, "http://x", "")
        assert result["payload"]["aborted"] == 1
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_agent_wait_no_run(self):
        result, _, _ = await self._call("agent.wait", {"runId": "norun", "timeoutMs": 100})
        assert result["ok"] is True
        assert result["payload"]["finished"] is True

    async def test_status_empty(self):
        result, _, _ = await self._call("status")
        assert result["ok"] is True
        assert "sessions" in result["payload"]

    async def test_unknown_method(self):
        result, _, _ = await self._call("foo.bar")
        assert result["ok"] is False
        assert result["error"]["code"] == "not_implemented"

    async def test_models_list_fallback(self):
        """Se o backend estiver offline, retorna modelo padrão."""
        result, _, _ = await self._call("models.list", bauer_url="http://localhost:0")
        assert result["ok"] is True
        models = result["payload"]["models"]
        assert any(m["id"] == "bauer" for m in models)

    async def test_chat_send_returns_none(self):
        """chat.send retorna None (ACK é enviado dentro de _run_chat)."""
        state = _ConnState()
        state.connected = True

        frames = []
        async def send_frame(f): frames.append(f)

        result = await _handle_method(
            "chat.send",
            {"message": "oi", "sessionKey": "k"},
            "r1", state, send_frame, "http://x", "",
        )
        assert result is None
        # Cancela tarefas pendentes
        for t in state.active_runs.values():
            t.cancel()


# ---------------------------------------------------------------------------
# _run_chat (SSE bridge mockado)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunChat:
    """Testa _run_chat com urllib.request mockado."""

    def _make_sse_response(self, chunks: list[str]) -> list[bytes]:
        """Gera linhas SSE como retornaria o urllib response."""
        lines = []
        for chunk in chunks:
            lines.append(f"data: {chunk}\n".encode())
        return lines

    async def test_empty_message_returns_error(self):
        state = _ConnState()
        frames = []
        async def send_frame(f): frames.append(f)

        await _run_chat(state, send_frame, "r1", {"message": ""}, "http://x", "")
        assert frames[0]["ok"] is False
        assert frames[0]["error"]["code"] == "bad_request"

    async def test_delta_and_final_events(self):
        """Simula streaming com dois deltas e [DONE]."""
        state = _ConnState()
        frames = []
        async def send_frame(f): frames.append(f)

        chunks_raw = [
            json.dumps({"choices": [{"delta": {"content": "Ola"}}]}),
            json.dumps({"choices": [{"delta": {"content": " mundo"}}]}),
            "[DONE]",
        ]
        sse_lines = [f"data: {c}\n".encode() for c in chunks_raw]

        mock_resp = MagicMock()
        mock_resp.__iter__ = MagicMock(return_value=iter(sse_lines))
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            await _run_chat(
                state, send_frame, "r1",
                {"message": "hi", "sessionKey": "k"},
                "http://bauer", "",
            )

        event_types = [f.get("event") for f in frames if f.get("type") == "event"]
        payloads_by_event = {
            f["event"]: f["payload"]
            for f in frames if f.get("type") == "event"
        }

        # ACK deve ser o primeiro frame (res ok)
        assert frames[0]["type"] == "res"
        assert frames[0]["ok"] is True
        assert frames[0]["payload"]["status"] == "started"

        # Deve ter eventos chat
        assert "chat" in event_types

        # Último evento chat deve ser final
        chat_frames = [f for f in frames if f.get("event") == "chat"]
        assert chat_frames[-1]["payload"]["state"] == "final"
        assert chat_frames[-1]["payload"]["message"]["content"] == "Ola mundo"

        # Histórico deve ter sido atualizado
        history = state.get_history("k")
        assert any(m["role"] == "user" for m in history)
        assert any(m["role"] == "assistant" for m in history)

    async def test_presence_event_after_done(self):
        """Emite evento presence após [DONE]."""
        state = _ConnState()
        frames = []
        async def send_frame(f): frames.append(f)

        sse_lines = [
            b"data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}).encode() + b"\n",
            b"data: [DONE]\n",
        ]
        mock_resp = MagicMock()
        mock_resp.__iter__ = MagicMock(return_value=iter(sse_lines))
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            await _run_chat(
                state, send_frame, "r1",
                {"message": "oi"},
                "http://bauer", "",
            )

        event_names = [f.get("event") for f in frames]
        assert "presence" in event_names

    async def test_error_event_on_exception(self):
        """Emite evento de erro se o backend lançar exceção."""
        state = _ConnState()
        frames = []
        async def send_frame(f): frames.append(f)

        with patch("urllib.request.urlopen", side_effect=OSError("conexao recusada")):
            await _run_chat(
                state, send_frame, "r1",
                {"message": "oi"},
                "http://bauer", "",
            )

        # ACK primeiro
        assert frames[0]["ok"] is True
        # Evento de erro
        error_frames = [f for f in frames if f.get("event") == "chat" and f["payload"].get("state") == "error"]
        assert error_frames, "Esperava evento chat state=error"

    async def test_run_id_from_params(self):
        """runId personalizado é preservado nos eventos."""
        state = _ConnState()
        frames = []
        async def send_frame(f): frames.append(f)

        sse_lines = [b"data: [DONE]\n"]
        mock_resp = MagicMock()
        mock_resp.__iter__ = MagicMock(return_value=iter(sse_lines))
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            await _run_chat(
                state, send_frame, "r1",
                {"message": "oi", "idempotencyKey": "my-run-id"},
                "http://bauer", "",
            )

        run_ids = {
            f["payload"].get("runId")
            for f in frames
            if f.get("event") == "chat"
        }
        assert "my-run-id" in run_ids


# ---------------------------------------------------------------------------
# _client_handler (handshake)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestClientHandler:
    """Testa o handshake completo e o dispatcher por método."""

    def _make_ws(self, frames_in: list[dict]):
        """Mock de websocket: itera frames de entrada, captura sends."""
        ws = MagicMock()
        ws.remote_address = ("127.0.0.1", 12345)
        sent = []

        async def send(raw: str):
            sent.append(json.loads(raw))

        ws.send = send

        async def _aiter():
            for f in frames_in:
                yield json.dumps(f)

        ws.__aiter__ = lambda self: _aiter()
        return ws, sent

    async def test_handshake_sends_challenge(self):
        """Handler envia connect.challenge imediatamente."""
        ws, sent = self._make_ws([])

        # Sem frames → handler termina sem erro
        await _client_handler(ws, "http://localhost:7770", "")

        # Primeiro frame enviado deve ser connect.challenge
        assert sent[0]["type"] == "event"
        assert sent[0]["event"] == "connect.challenge"
        assert "nonce" in sent[0]["payload"]

    async def test_handshake_connect_returns_hello_ok(self):
        """Após método connect, handler envia hello-ok."""
        ws, sent = self._make_ws([
            {"type": "req", "id": "r1", "method": "connect", "params": {}},
        ])

        await _client_handler(ws, "http://localhost:7770", "")

        hello_frames = [f for f in sent if f.get("payload", {}).get("type") == "hello-ok"]
        assert hello_frames, "Esperava frame hello-ok"
        assert hello_frames[0]["ok"] is True
        assert hello_frames[0]["payload"]["adapterType"] == ADAPTER_TYPE

    async def test_method_before_connect_returns_error(self):
        """Método enviado antes de connect retorna not_connected."""
        ws, sent = self._make_ws([
            {"type": "req", "id": "r2", "method": "wake", "params": {}},
        ])

        await _client_handler(ws, "http://localhost:7770", "")

        err_frames = [
            f for f in sent
            if f.get("type") == "res" and not f.get("ok")
        ]
        assert err_frames
        assert err_frames[0]["error"]["code"] == "not_connected"

    async def test_wake_after_connect(self):
        """wake retorna ok após handshake."""
        ws, sent = self._make_ws([
            {"type": "req", "id": "r1", "method": "connect", "params": {}},
            {"type": "req", "id": "r2", "method": "wake", "params": {}},
        ])

        await _client_handler(ws, "http://localhost:7770", "")

        wake_resp = next((f for f in sent if f.get("id") == "r2"), None)
        assert wake_resp is not None
        assert wake_resp["ok"] is True

    async def test_invalid_json_is_ignored(self):
        """Frames inválidos são ignorados sem crash."""
        ws = MagicMock()
        ws.remote_address = ("127.0.0.1", 1)
        sent = []
        ws.send = AsyncMock(side_effect=lambda r: sent.append(json.loads(r)))

        async def _aiter():
            yield "NOT JSON {{{"

        ws.__aiter__ = lambda self: _aiter()

        await _client_handler(ws, "http://localhost:7770", "")
        # Apenas o challenge inicial
        assert len(sent) == 1
        assert sent[0]["event"] == "connect.challenge"

    async def test_non_req_frames_ignored(self):
        """Frames que não são type=req são ignorados."""
        ws, sent = self._make_ws([
            {"type": "event", "event": "something", "payload": {}},
            {"type": "req", "id": "r1", "method": "connect", "params": {}},
        ])

        await _client_handler(ws, "http://localhost:7770", "")

        hello_frames = [f for f in sent if f.get("payload", {}).get("type") == "hello-ok"]
        assert hello_frames, "Esperava hello-ok após connect"

    async def test_sessions_list_via_handler(self):
        """sessions.list retorna lista vazia na primeira chamada."""
        ws, sent = self._make_ws([
            {"type": "req", "id": "r1", "method": "connect", "params": {}},
            {"type": "req", "id": "r2", "method": "sessions.list", "params": {}},
        ])

        await _client_handler(ws, "http://localhost:7770", "")

        sessions_resp = next((f for f in sent if f.get("id") == "r2"), None)
        assert sessions_resp is not None
        assert sessions_resp["ok"] is True
        assert "sessions" in sessions_resp["payload"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TestRunGateway:
    def test_raises_without_websockets(self):
        """run_gateway levanta RuntimeError se websockets não estiver instalado."""
        with patch.dict("sys.modules", {"websockets": None}):
            with pytest.raises(RuntimeError, match="websockets"):
                asyncio.run(run_gateway(host="127.0.0.1", port=19999))

    def test_start_gateway_thread_returns_thread(self):
        """start_gateway_thread sobe daemon thread e retorna threading.Thread."""
        # Substitui run_gateway por uma coroutine que termina imediatamente
        async def _noop(**kwargs):
            return

        with patch("bauer.gateway.run_gateway", new=_noop):
            t = start_gateway_thread(host="127.0.0.1", port=19998)

        # Aguarda o thread terminar (run_gateway retorna imediatamente)
        t.join(timeout=2.0)

        assert isinstance(t, threading.Thread)
        assert t.daemon is True
        assert t.name == "bauer-gateway"


# ---------------------------------------------------------------------------
# Seq counter integrity
# ---------------------------------------------------------------------------


class TestSeqCounterIntegrity:
    def test_seq_monotonic_across_calls(self):
        """next_seq sempre retorna valores crescentes."""
        s = _ConnState()
        vals = [s.next_seq() for _ in range(20)]
        assert vals == list(range(20))

    def test_multiple_states_independent(self):
        """Estados separados têm contadores independentes."""
        s1 = _ConnState()
        s2 = _ConnState()
        s1.next_seq(); s1.next_seq()
        assert s2.next_seq() == 0
        assert s1.next_seq() == 2

    def test_event_uses_correct_seq(self):
        s = _ConnState()
        frame = _event("chat", s.next_seq(), {"x": 1})
        assert frame["seq"] == 0
        frame2 = _event("chat", s.next_seq(), {"x": 2})
        assert frame2["seq"] == 1
