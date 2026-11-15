"""Narrow AgentOS HTTP surface for an experimental channel integration.

This is defense in depth, not user isolation or remote tool governance.
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import parse_qsl
from starlette.requests import Request
from starlette.responses import JSONResponse

_RUN_PATH = re.compile(r"^/(agents|teams)/([^/]+)/runs(?:/([^/]+)(/cancel)?)?$")
_FIELDS = {"message", "session_id", "user_id", "stream"}
_MAX_BODY = 256 * 1024


class ChannelBoundaryMiddleware:
    """Deny routes and form parameters outside a small channel contract."""

    def __init__(self, app, agent_ids: set[str], team_ids: set[str], *, jwt_isolation: bool = False,
                 entities=None, approval_store=None, active_runs=None, external_tools: bool = False):
        self.app = app
        self.allowed_ids = {"agents": frozenset(agent_ids), "teams": frozenset(team_ids)}
        self.jwt_isolation = jwt_isolation
        self.entities = entities or {}
        self.approval_store = approval_store
        self.active_runs = active_runs
        self.external_tools = external_tools

    async def __call__(self, scope, receive, send):
        async def deny(status: int = 403):
            await JSONResponse({"detail": "operação indisponível para o canal"}, status_code=status)(scope, receive, send)

        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self.app(scope, receive, send)
            return

        method, path = scope["method"], scope["path"]
        if method == "GET" and path in {"/health", "/info"} and not scope.get("query_string"):
            await self.app(scope, receive, send)
            return
        if (self.jwt_isolation and method == "GET" and path == "/bauer/channel-capabilities"
                and not scope.get("query_string")):
            await self.app(scope, receive, send)
            return
        if (self.jwt_isolation and self.external_tools and method == "POST"
                and path in {"/bauer/tool-requirement", "/bauer/tool-continue"}
                and not scope.get("query_string")):
            await self.app(scope, receive, send)
            return
        match = _RUN_PATH.fullmatch(path)
        if not match or match[2] not in self.allowed_ids[match[1]]:
            await deny()
            return
        kind, entity_id, run_id, cancel = match.groups()
        if run_id is not None:
            # The shared-key profile does not authenticate an end user.  Never
            # expose lifecycle endpoints there.  The JWT profile is tested
            # with non-admin callers and Agno's user_isolation enabled.
            allowed = self.jwt_isolation and (
                (method == "GET" and cancel is None) or (method == "POST" and cancel == "/cancel")
            )
            if not allowed:
                await deny()
                return
            try:
                query = parse_qsl(scope.get("query_string", b"").decode("ascii"),
                                  keep_blank_values=True, strict_parsing=True)
            except (UnicodeDecodeError, ValueError):
                await deny(400)
                return
            if len(query) != 1 or query[0][0] != "session_id" or not query[0][1].strip():
                await deny(400)
                return
            if cancel:
                # Never signal a cancellation while an approval continuation
                # may be executing. Identity and run ownership are verified
                # before persisting intent; the store serializes it with claim.
                from .agentos_approval_store import ApprovalConflict

                identity = scope.get("state", {}).get("bauer_jwt_identity")
                subject = identity.get("subject") if isinstance(identity, dict) else None
                scopes = identity.get("scopes") if isinstance(identity, dict) else None
                entity = self.entities.get(kind, {}).get(entity_id)
                if (not isinstance(subject, str) or not isinstance(scopes, list)
                        or f"{kind}:{entity_id}:run" not in scopes or entity is None
                        or self.approval_store is None):
                    await deny()
                    return
                session_id = query[0][1]
                try:
                    active = self.active_runs is not None and self.active_runs.owns(
                        run_id=run_id, owner=subject, entity_type=kind,
                        entity_id=entity_id, session_id=session_id,
                    )
                    if active and not self.active_runs.owned_here(
                        run_id=run_id, owner=subject, entity_type=kind,
                        entity_id=entity_id, session_id=session_id,
                    ):
                        # Agno's default cancellation manager is per process.
                        # Do not claim acceptance in a different worker or
                        # after restart; the remote outcome is still unknown.
                        await deny(409)
                        return
                    if not active:
                        run = await entity.aget_run_output(run_id, session_id=session_id, user_id=subject)
                        run_entity = getattr(run, "agent_id" if kind == "agents" else "team_id", None)
                        if (run is None or run_entity != entity_id or getattr(run, "run_id", None) != run_id
                                or getattr(run, "session_id", None) != session_id
                                or getattr(run, "user_id", None) != subject):
                            await deny(404)
                            return
                        status = getattr(getattr(run, "status", None), "value", getattr(run, "status", None))
                        if str(status).upper() in {"COMPLETED", "CANCELLED", "ERROR", "FAILED"}:
                            await deny(409)
                            return
                    self.approval_store.request_cancel(owner_id=subject, entity_type=kind,
                                                       entity_id=entity_id, run_id=run_id,
                                                       session_id=session_id)
                    if active:
                        # Ownership was bound to the server-generated SSE start
                        # before exposing the ID. This is acceptance only; the
                        # caller must confirm the remote terminal state.
                        await entity.acancel_run(run_id=run_id)
                        await JSONResponse({"status": "cancel_requested"})(scope, receive, send)
                        return
                except ApprovalConflict:
                    await deny(409)
                    return
                except Exception:  # noqa: BLE001 - error and ownership failure both fail closed
                    await deny(404)
                    return
            await self.app(scope, receive, send)
            return
        if method != "POST" or scope.get("query_string"):
            await deny()
            return
        content_types = [v for k, v in scope["headers"] if k.lower() == b"content-type"]
        if len(content_types) != 1 or content_types[0].split(b";", 1)[0].lower().strip() not in {
            b"multipart/form-data", b"application/x-www-form-urlencoded"
        }:
            await deny(400)
            return
        chunks = []
        size = 0
        while True:
            event = await receive()
            if event["type"] != "http.request":
                await deny(400)
                return
            chunk = event.get("body", b"")
            size += len(chunk)
            if size > _MAX_BODY:
                await deny(413)
                return
            chunks.append(chunk)
            if not event.get("more_body", False):
                break
        body = b"".join(chunks)

        def replay():
            delivered = False

            async def read():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()

            return read

        try:
            form = await Request(scope, replay()).form(max_files=0, max_fields=4, max_part_size=_MAX_BODY)
            entries = form.multi_items()
            keys = [key for key, _ in entries]
            if len(keys) != len(set(keys)) or set(keys) - _FIELDS or any(not isinstance(v, str) for _, v in entries):
                await deny(400)
                return
            if not form.get("message"):
                await deny(400)
                return
        except Exception:  # malformed multipart is refused before reaching Agno
            await deny(400)
            return
        if self.jwt_isolation:
            identity = scope.get("state", {}).get("bauer_jwt_identity")
            subject = identity.get("subject") if isinstance(identity, dict) else None
            session_id = form.get("session_id")
            if (not isinstance(subject, str) or not subject or not isinstance(session_id, str)
                    or not session_id.strip() or len(session_id) > 256):
                await deny(400)
                return
            from .core.runtime.adapters.agno_adapter import _ACTIVE_AGNO_SESSION_ID

            # The client cannot choose an identity: `subject` came from the
            # verified JWT. The Agno handler performs its own session ownership
            # check before invoking a model/tool. Hashing the pair also avoids
            # keeping raw Telegram identity in per-session router cache keys.
            encoded = json.dumps([subject, session_id], ensure_ascii=True, separators=(",", ":")).encode("ascii")
            token = _ACTIVE_AGNO_SESSION_ID.set(hashlib.sha256(encoded).hexdigest())
            try:
                if self.active_runs is not None and str(form.get("stream", "")).lower() == "true":
                    from .agentos_active_runs import StartedEventTracker

                    tracker = StartedEventTracker(self.active_runs, owner=subject, entity_type=kind,
                                                  entity_id=entity_id, session_id=session_id)
                    is_sse = False

                    async def tracked_send(event):
                        nonlocal is_sse
                        if event["type"] == "http.response.start":
                            is_sse = (event.get("status") == 200 and any(
                                key.lower() == b"content-type" and b"text/event-stream" in value.lower()
                                for key, value in event.get("headers", [])
                            ))
                        elif event["type"] == "http.response.body" and is_sse:
                            more = bool(event.get("more_body", False))
                            frames = tracker.feed(event.get("body", b""), final=not more)
                            if not frames and more:
                                return
                            event = {**event, "body": b"".join(frames)}
                        await send(event)

                    try:
                        await self.app(scope, replay(), tracked_send)
                    finally:
                        tracker.close()
                else:
                    await self.app(scope, replay(), send)
            finally:
                _ACTIVE_AGNO_SESSION_ID.reset(token)
            return
        await self.app(scope, replay(), send)
