"""gateway.py — WebSocket Gateway compatível com Claw3D / escritório virtual.

Implementa o protocolo Hermes WebSocket:
  - Handshake:  connect.challenge → connect → hello-ok
  - chat.send:  streaming delta/final via /v1/chat/completions
  - chat.abort: cancela run ativo
  - chat.history: histórico da sessão
  - agents.list, sessions.list, sessions.reset, sessions.patch
  - models.list, status, wake, config.get
  - Heartbeat a cada 25s (policy.tickIntervalMs = 30000)

Bridges WebSocket (porta 18789) ↔ HTTP Bauer (porta 7770).

Uso:
    from bauer.gateway import run_gateway
    await run_gateway(bauer_url="http://localhost:7770", port=18789)

    # ou via CLI:
    bauer gateway [--port 18789] [--bauer-url http://localhost:7770]
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("bauer.gateway")

# ── Constantes de protocolo ───────────────────────────────────────────────────

PROTOCOL_VERSION = 3
ADAPTER_TYPE = "bauer"
AGENT_ID = "bauer"
AGENT_NAME = "Bauer Agent"
DEFAULT_SESSION_SLOT = "main"
HEARTBEAT_INTERVAL_S = 25   # < 30 s (policy.tickIntervalMs)
MAX_CHAT_ROUNDS = 8          # agentic loop limit


# ── Estado por conexão ────────────────────────────────────────────────────────

@dataclass
class _ConnState:
    """Estado isolado por cliente WebSocket."""
    connected: bool = False
    seq: int = 0
    # session_key → lista de messages {role, content}
    histories: dict[str, list[dict]] = field(default_factory=dict)
    # run_id → asyncio.Task
    active_runs: dict[str, asyncio.Task] = field(default_factory=dict)

    def next_seq(self) -> int:
        v = self.seq
        self.seq += 1
        return v

    def get_history(self, session_key: str) -> list[dict]:
        return self.histories.setdefault(session_key, [])

    def reset_history(self, session_key: str) -> None:
        self.histories[session_key] = []


# ── Construtores de frame ─────────────────────────────────────────────────────

def _res_ok(req_id: str, payload: Any) -> dict:
    return {"type": "res", "id": req_id, "ok": True, "payload": payload}


def _res_err(req_id: str, code: str, message: str) -> dict:
    return {"type": "res", "id": req_id, "ok": False,
            "error": {"code": code, "message": message}}


def _event(event_name: str, seq: int, payload: Any) -> dict:
    return {"type": "event", "event": event_name, "seq": seq, "payload": payload}


def _hello_ok(req_id: str) -> dict:
    return _res_ok(req_id, {
        "type": "hello-ok",
        "protocol": PROTOCOL_VERSION,
        "adapterType": ADAPTER_TYPE,
        "features": {
            "methods": [
                "agents.list", "sessions.list", "sessions.preview",
                "sessions.patch", "sessions.reset",
                "chat.send", "chat.abort", "chat.history",
                "agent.wait", "status", "config.get",
                "models.list", "wake",
            ],
            "events": ["chat", "presence", "heartbeat"],
        },
        "snapshot": {
            "health": {
                "agents": [{"agentId": AGENT_ID, "name": AGENT_NAME, "isDefault": True}],
                "defaultAgentId": AGENT_ID,
            },
            "sessionDefaults": {"mainKey": f"agent:{AGENT_ID}:{DEFAULT_SESSION_SLOT}"},
        },
        "auth": {"role": "operator", "scopes": ["operator.admin"]},
        "policy": {"tickIntervalMs": HEARTBEAT_INTERVAL_S * 1000},
    })


# ── SSE parser ────────────────────────────────────────────────────────────────

def _parse_sse_lines(raw: str):
    """Gera (event_name, data) de um bloco SSE raw."""
    event = "message"
    data_lines: list[str] = []
    for line in raw.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "" and data_lines:
            yield event, "\n".join(data_lines)
            event = "message"
            data_lines = []
    if data_lines:
        yield event, "\n".join(data_lines)


# ── Chat handler (SSE bridge) ─────────────────────────────────────────────────

async def _run_chat(
    state: _ConnState,
    send_frame,           # coroutine callable
    req_id: str,
    params: dict,
    bauer_url: str,
    api_key: str,
) -> None:
    """Faz POST /v1/chat/completions e emite delta/final events via WebSocket."""
    session_key = params.get("sessionKey") or f"agent:{AGENT_ID}:{DEFAULT_SESSION_SLOT}"
    user_msg = (params.get("message") or "").strip()
    run_id = params.get("idempotencyKey") or secrets.token_hex(8)

    if not user_msg:
        await send_frame(_res_err(req_id, "bad_request", "message is empty"))
        return

    history = state.get_history(session_key)
    messages = list(history) + [{"role": "user", "content": user_msg}]

    # ACK imediato
    await send_frame(_res_ok(req_id, {"status": "started", "runId": run_id}))

    accumulated = ""

    async def _stream():
        nonlocal accumulated
        import urllib.request

        body = json.dumps({
            "model": "bauer",
            "messages": messages,
            "stream": True,
            "session_id": session_key,
        }).encode()

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers["X-Hermes-Session-Id"] = session_key

        req = urllib.request.Request(
            f"{bauer_url}/v1/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            loop = asyncio.get_event_loop()

            def _do_request():
                chunks: list[str] = []
                with urllib.request.urlopen(req, timeout=120) as resp:
                    buf = ""
                    for raw_bytes in resp:
                        buf += raw_bytes.decode("utf-8", errors="replace")
                        # Processa linhas completas
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.rstrip("\r")
                            if line.startswith("data:"):
                                data = line[5:].strip()
                                chunks.append(data)
                return chunks

            # Roda em thread (urllib é síncrono)
            raw_chunks = await loop.run_in_executor(None, _do_request)

            for data in raw_chunks:
                if data == "[DONE]":
                    # Evento final
                    seq = state.next_seq()
                    await send_frame(_event("chat", seq, {
                        "runId": run_id,
                        "sessionKey": session_key,
                        "state": "final",
                        "stopReason": "end_turn",
                        "message": {"role": "assistant", "content": accumulated},
                    }))
                    # Persiste no histórico
                    history.append({"role": "user", "content": user_msg})
                    history.append({"role": "assistant", "content": accumulated})
                    # Presence event
                    seq2 = state.next_seq()
                    await send_frame(_event("presence", seq2, {
                        "sessions": {
                            "recent": [{"key": session_key, "updatedAt": int(time.time() * 1000)}],
                            "byAgent": [{"agentId": AGENT_ID, "recent": [
                                {"key": session_key, "updatedAt": int(time.time() * 1000)}
                            ]}],
                        }
                    }))
                    return

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                delta_content = (
                    chunk.get("choices", [{}])[0]
                    .get("delta", {})
                    .get("content", "")
                )
                if delta_content:
                    accumulated += delta_content
                    seq = state.next_seq()
                    await send_frame(_event("chat", seq, {
                        "runId": run_id,
                        "sessionKey": session_key,
                        "state": "delta",
                        "message": {"role": "assistant", "content": accumulated},
                    }))

        except asyncio.CancelledError:
            # chat.abort foi chamado
            seq = state.next_seq()
            await send_frame(_event("chat", seq, {
                "runId": run_id,
                "sessionKey": session_key,
                "state": "aborted",
            }))
        except Exception as exc:
            logger.error("Gateway chat error: %s", exc)
            seq = state.next_seq()
            await send_frame(_event("chat", seq, {
                "runId": run_id,
                "sessionKey": session_key,
                "state": "error",
                "errorMessage": str(exc),
            }))

    task = asyncio.create_task(_stream())
    state.active_runs[run_id] = task

    try:
        await task
    finally:
        state.active_runs.pop(run_id, None)


# ── Handlers de método ────────────────────────────────────────────────────────

async def _handle_method(
    method: str,
    params: dict,
    req_id: str,
    state: _ConnState,
    send_frame,
    bauer_url: str,
    api_key: str,
) -> dict | None:
    """Despacha um método e retorna frame de resposta (ou None se já enviou)."""

    # --- wake / ping --------------------------------------------------------
    if method == "wake":
        return _res_ok(req_id, {"ok": True})

    # --- config.get ---------------------------------------------------------
    if method == "config.get":
        return _res_ok(req_id, {
            "model": {"provider": "bauer", "name": "bauer"},
            "features": {},
        })

    # --- agents.list --------------------------------------------------------
    if method == "agents.list":
        return _res_ok(req_id, {
            "defaultId": AGENT_ID,
            "mainKey": f"agent:{AGENT_ID}:{DEFAULT_SESSION_SLOT}",
            "agents": [
                {
                    "id": AGENT_ID,
                    "name": AGENT_NAME,
                    "workspace": "default",
                    "role": "operator",
                    "isDefault": True,
                }
            ],
        })

    # --- models.list --------------------------------------------------------
    if method == "models.list":
        # Tenta buscar modelos do Bauer; fallback para modelo padrão
        try:
            import urllib.request as _ur
            req = _ur.Request(f"{bauer_url}/v1/models", method="GET")
            if api_key:
                req.add_header("Authorization", f"Bearer {api_key}")
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None, lambda: _ur.urlopen(req, timeout=5).read()
            )
            data = json.loads(raw)
            models = [
                {"id": m["id"], "name": m.get("id", m["id"])}
                for m in data.get("data", [])
            ]
        except Exception:
            models = [{"id": "bauer", "name": AGENT_NAME}]
        return _res_ok(req_id, {"models": models})

    # --- sessions.list ------------------------------------------------------
    if method == "sessions.list":
        sessions = []
        for key, msgs in state.histories.items():
            if msgs:
                sessions.append({
                    "key": key,
                    "agentId": AGENT_ID,
                    "updatedAt": int(time.time() * 1000),
                    "model": "bauer",
                    "messageCount": len(msgs),
                })
        return _res_ok(req_id, {"sessions": sessions})

    # --- sessions.preview ---------------------------------------------------
    if method == "sessions.preview":
        key = params.get("key", "")
        msgs = state.histories.get(key, [])
        preview = msgs[-1]["content"][:120] if msgs else ""
        return _res_ok(req_id, {"key": key, "preview": preview})

    # --- sessions.reset -----------------------------------------------------
    if method == "sessions.reset":
        key = params.get("key", "")
        state.reset_history(key)
        return _res_ok(req_id, {"ok": True, "key": key})

    # --- sessions.patch -----------------------------------------------------
    if method == "sessions.patch":
        # Aceita patches de modelo/configuração (sem-op por ora)
        return _res_ok(req_id, {"ok": True})

    # --- chat.history -------------------------------------------------------
    if method == "chat.history":
        key = params.get("sessionKey", f"agent:{AGENT_ID}:{DEFAULT_SESSION_SLOT}")
        msgs = state.get_history(key)
        return _res_ok(req_id, {"sessionKey": key, "messages": msgs})

    # --- chat.send ----------------------------------------------------------
    if method == "chat.send":
        # Fire-and-forget: _run_chat envia ACK e depois emite events
        asyncio.create_task(
            _run_chat(state, send_frame, req_id, params, bauer_url, api_key)
        )
        return None   # ACK será enviado dentro de _run_chat

    # --- chat.abort ---------------------------------------------------------
    if method == "chat.abort":
        run_id = params.get("runId")
        session_key = params.get("sessionKey")
        aborted = 0
        if run_id and run_id in state.active_runs:
            state.active_runs[run_id].cancel()
            aborted = 1
        elif session_key:
            for rid, task in list(state.active_runs.items()):
                task.cancel()
                aborted += 1
        return _res_ok(req_id, {"ok": True, "aborted": aborted})

    # --- agent.wait ---------------------------------------------------------
    if method == "agent.wait":
        run_id = params.get("runId", "")
        timeout_ms = params.get("timeoutMs", 30000)
        task = state.active_runs.get(run_id)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_ms / 1000)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        return _res_ok(req_id, {"ok": True, "runId": run_id, "finished": run_id not in state.active_runs})

    # --- status -------------------------------------------------------------
    if method == "status":
        sessions = []
        for key, msgs in state.histories.items():
            if msgs:
                sessions.append({"key": key, "updatedAt": int(time.time() * 1000)})
        return _res_ok(req_id, {
            "sessions": {
                "recent": sessions,
                "byAgent": [{"agentId": AGENT_ID, "recent": sessions}],
            }
        })

    # --- método desconhecido -----------------------------------------------
    logger.warning("Gateway: método não implementado: %s", method)
    return _res_err(req_id, "not_implemented", f"Method '{method}' not implemented.")


# ── Handler principal por conexão ─────────────────────────────────────────────

async def _client_handler(websocket, bauer_url: str, api_key: str) -> None:
    """Loop principal de um cliente Claw3D conectado."""
    state = _ConnState()
    client_addr = getattr(websocket, "remote_address", "?")
    logger.info("Gateway: cliente conectado — %s", client_addr)

    async def send_frame(frame: dict) -> None:
        try:
            await websocket.send(json.dumps(frame, ensure_ascii=False))
        except Exception as exc:
            logger.debug("Gateway: send falhou (%s): %s", client_addr, exc)

    # 1. Desafio de conexão
    await send_frame(_event(
        "connect.challenge",
        state.next_seq(),
        {"nonce": secrets.token_hex(8)},
    ))

    # 2. Heartbeat em background
    async def _heartbeat():
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            await send_frame(_event(
                "heartbeat",
                state.next_seq(),
                {"ts": int(time.time() * 1000)},
            ))

    hb_task = asyncio.create_task(_heartbeat())

    try:
        async for raw in websocket:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Gateway: frame inválido de %s", client_addr)
                continue

            if frame.get("type") != "req":
                continue

            req_id = frame.get("id", "?")
            method = frame.get("method", "")
            params = frame.get("params") or {}

            # Handshake connect
            if method == "connect":
                state.connected = True
                await send_frame(_hello_ok(req_id))
                logger.info("Gateway: cliente autenticado — %s", client_addr)
                continue

            if not state.connected:
                await send_frame(_res_err(req_id, "not_connected", "Send connect first."))
                continue

            # Despacha método
            response = await _handle_method(
                method, params, req_id, state, send_frame, bauer_url, api_key
            )
            if response is not None:
                await send_frame(response)

    except Exception as exc:
        # Conexão fechada pelo cliente ou erro de protocolo
        logger.debug("Gateway: conexão encerrada (%s): %s", client_addr, exc)
    finally:
        hb_task.cancel()
        for task in state.active_runs.values():
            task.cancel()
        logger.info("Gateway: cliente desconectado — %s", client_addr)


# ── API pública ───────────────────────────────────────────────────────────────

async def run_gateway(
    bauer_url: str = "http://localhost:7770",
    host: str = "127.0.0.1",
    port: int = 18789,
    api_key: str = "",
) -> None:
    """Inicia o gateway WebSocket e bloqueia até ser interrompido.

    Args:
        bauer_url:  URL base do Bauer HTTP server (deve estar rodando).
        host:       Interface de escuta.
        port:       Porta WebSocket.
        api_key:    API key do Bauer serve (vazio = sem auth).
    """
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError(
            "O pacote 'websockets' é necessário para o gateway. "
            "Instale com: pip install websockets"
        ) from exc

    handler = lambda ws: _client_handler(ws, bauer_url, api_key)

    logger.info("Bauer Gateway iniciando em ws://%s:%d", host, port)
    logger.info("Backend Bauer: %s", bauer_url)

    async with websockets.serve(handler, host, port):
        await asyncio.Future()   # roda para sempre até cancelamento


def run_gateway_sync(
    bauer_url: str = "http://localhost:7770",
    host: str = "127.0.0.1",
    port: int = 18789,
    api_key: str = "",
) -> None:
    """Versão síncrona de run_gateway para uso em threads ou scripts."""
    asyncio.run(run_gateway(bauer_url=bauer_url, host=host, port=port, api_key=api_key))


def start_gateway_thread(
    bauer_url: str = "http://localhost:7770",
    host: str = "127.0.0.1",
    port: int = 18789,
    api_key: str = "",
):
    """Inicia o gateway em uma daemon thread. Retorna o thread."""
    import threading

    def _run():
        asyncio.run(run_gateway(bauer_url=bauer_url, host=host, port=port, api_key=api_key))

    t = threading.Thread(target=_run, daemon=True, name="bauer-gateway")
    t.start()
    return t
