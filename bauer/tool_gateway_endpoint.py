"""Restricted Tool Gateway endpoint and single-use request ledger."""
from __future__ import annotations

import hmac
import ipaddress
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path, PureWindowsPath
from typing import Any, Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict

from .tool_gateway_authorization import ToolGatewayAuthorizer, ToolGatewayDenied
from .tool_gateway_protocol import call_digest
from .tool_router import ToolError, ToolRouter


class _Call(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: str
    args: dict[str, Any]


class ToolCallLedger:
    """Transactional, permanently non-reusable token IDs; stores no arguments."""

    def __init__(self, path: Path, *, create: bool = True):
        if not isinstance(path, Path):
            raise ValueError("ledger path must be a Path")
        self.path = path
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.is_file():
            raise ValueError("tool gateway ledger unavailable")
        uri = self.path.resolve().as_uri() + ("?mode=rwc" if create else "?mode=rw")
        with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db, db:
            if create:
                db.execute("CREATE TABLE IF NOT EXISTS tool_gateway_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                db.execute("INSERT OR IGNORE INTO tool_gateway_meta VALUES ('ledger_id', ?)", (uuid.uuid4().hex,))
            try:
                identity = db.execute("SELECT value FROM tool_gateway_meta WHERE key='ledger_id'").fetchone()
            except sqlite3.Error as exc:
                raise ValueError("tool gateway ledger identity unavailable") from exc
            if not identity or not isinstance(identity[0], str) or len(identity[0]) != 32:
                raise ValueError("invalid tool gateway ledger identity")
            self.ledger_id = identity[0]
            if create:
                db.execute("CREATE TABLE IF NOT EXISTS tool_gateway_calls ("
                           "token_id TEXT PRIMARY KEY, digest TEXT NOT NULL, run_id TEXT NOT NULL, "
                           "remote_run_id TEXT NOT NULL, tool_call_id TEXT NOT NULL, state TEXT NOT NULL, "
                           "UNIQUE(run_id, remote_run_id, tool_call_id))")
                db.execute("CREATE TABLE IF NOT EXISTS tool_gateway_run_barrier ("
                           "run_id TEXT PRIMARY KEY, state TEXT NOT NULL, reason TEXT)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tool_gateway_calls)")}
            if columns != {"token_id", "digest", "run_id", "remote_run_id", "tool_call_id", "state"}:
                raise ValueError("incompatible tool gateway ledger schema")
            barrier_columns = {row[1] for row in db.execute("PRAGMA table_info(tool_gateway_run_barrier)")}
            if barrier_columns != {"run_id", "state", "reason"}:
                raise ValueError("incompatible tool gateway barrier schema")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # mode=rw refuses to recreate a deleted ledger. Re-check identity on
        # every operation so replacement or truncation cannot silently clear a
        # pending effect, stop intent, or idempotency record.
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            with db:
                # Validate the ledger identity under the same writer lock as
                # the operation; another process cannot replace its marker
                # between this check and a claim, settlement or stop.
                db.execute("BEGIN IMMEDIATE")
                try:
                    row = db.execute("SELECT value FROM tool_gateway_meta WHERE key='ledger_id'").fetchone()
                except sqlite3.Error as exc:
                    raise ValueError("tool gateway ledger identity unavailable") from exc
                if not row or not hmac.compare_digest(str(row[0]), self.ledger_id):
                    raise ValueError("tool gateway ledger identity changed")
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_meta'").fetchone():
                    marker = db.execute(
                        "SELECT value FROM runtime_meta WHERE key='tool_gateway_guard_v1'"
                    ).fetchone()
                    if marker and not hmac.compare_digest(str(marker[0]), self.ledger_id):
                        raise ValueError("tool gateway ledger identity changed")
                yield db
        finally:
            db.close()

    def claim(self, token_id: str, digest: str, *, run_id: str, remote_run_id: str, tool_call_id: str,
              require_active_guard: bool = False) -> bool:
        with self._connect() as db:
            # On an activated runtime, check custody under the same writer
            # lock as the reservation. Standalone ledgers remain useful for
            # isolated protocol tests but are not accepted by the router.
            runtime_meta = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runtime_meta'"
            ).fetchone()
            marker = (db.execute(
                "SELECT value FROM runtime_meta WHERE key='tool_gateway_guard_v1'"
            ).fetchone() if runtime_meta else None)
            if require_active_guard and marker is None:
                return False
            if marker:
                if not hmac.compare_digest(str(marker[0]), self.ledger_id):
                    raise ValueError("tool gateway ledger identity changed")
                row = db.execute(
                    "SELECT payload FROM runtime_records WHERE collection='runs' AND record_id=? "
                    "ORDER BY sequence DESC LIMIT 1", (run_id,),
                ).fetchone()
                if row is None:
                    return False
                run = json.loads(row[0])
                if run.get("status") != "running" or run.get("runtime_adapter") != "agentos_http":
                    return False
            if db.execute("SELECT 1 FROM tool_gateway_run_barrier WHERE run_id=?", (run_id,)).fetchone():
                return False
            try:
                db.execute("INSERT INTO tool_gateway_calls VALUES (?, ?, ?, ?, ?, 'CLAIMED')",
                           (token_id, digest, run_id, remote_run_id, tool_call_id))
                db.execute("INSERT INTO tool_gateway_run_barrier VALUES (?, 'IN_FLIGHT', NULL)", (run_id,))
                return True
            except sqlite3.IntegrityError:
                return False

    def settle(self, token_id: str, *, completed: bool) -> None:
        self._settle(token_id, "COMPLETED" if completed else "UNKNOWN")

    def reject(self, token_id: str) -> None:
        """Consume a refused read without keeping an uncertain-effect barrier."""
        self._settle(token_id, "REJECTED")

    def _settle(self, token_id: str, state: str) -> None:
        with self._connect() as db:
            row = db.execute("SELECT run_id FROM tool_gateway_calls WHERE token_id=? AND state='CLAIMED'",
                             (token_id,)).fetchone()
            if row is None:
                return
            db.execute("UPDATE tool_gateway_calls SET state=? WHERE token_id=? AND state='CLAIMED'",
                       (state, token_id))
            if state != "UNKNOWN":
                db.execute("DELETE FROM tool_gateway_run_barrier WHERE run_id=? AND state='IN_FLIGHT'", (row[0],))
                db.execute("UPDATE tool_gateway_run_barrier SET state='STOPPING' "
                           "WHERE run_id=? AND state='STOP_REQUESTED'", (row[0],))
            else:
                db.execute("UPDATE tool_gateway_run_barrier SET state=CASE WHEN state='STOP_REQUESTED' "
                           "THEN 'UNKNOWN_STOP_REQUESTED' ELSE 'UNKNOWN' END WHERE run_id=?", (row[0],))

    def request_cancel(self, *, run_id: str) -> bool:
        return self.request_stop(run_id=run_id, reason="cancel")

    def stop_reason(self, run_id: str) -> str | None:
        """Return a persisted stop/uncertain-effect reason for a live caller."""
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be non-empty")
        with self._connect() as db:
            row = db.execute("SELECT state, reason FROM tool_gateway_run_barrier WHERE run_id=?",
                             (run_id,)).fetchone()
        if row is None:
            return None
        state, reason = row
        if state in {"STOP_REQUESTED", "STOPPING", "UNKNOWN_STOP_REQUESTED"}:
            return str(reason or "cancel")
        if state == "UNKNOWN":
            return "unknown"
        return None

    def request_stop(self, *, run_id: str, reason: str) -> bool:
        """Reserve a terminal transition unless a tool is in flight or uncertain.

        The caller must authenticate/authorize run ownership before calling.
        Every cancellation and terminal-state path must share this barrier.
        A stop requested during an effect remains sticky and blocks future calls;
        False means the stop cannot yet be reported as complete.
        """
        if not isinstance(run_id, str) or not run_id.strip() or reason not in {"cancel", "kill_switch", "timeout", "budget_exhausted", "terminal"}:
            raise ValueError("run_id must be non-empty")
        with self._connect() as db:
            return self.request_stop_in_transaction(db, run_id=run_id, reason=reason)

    @staticmethod
    def request_stop_in_transaction(db: sqlite3.Connection, *, run_id: str, reason: str) -> bool:
        """Reserve a stop under the caller's existing SQLite writer lock."""
        current = db.execute("SELECT state FROM tool_gateway_run_barrier WHERE run_id=?", (run_id,)).fetchone()
        if current:
            if current[0] == "IN_FLIGHT":
                db.execute("UPDATE tool_gateway_run_barrier SET state='STOP_REQUESTED', reason=? WHERE run_id=?",
                           (reason, run_id))
                return False
            if current[0] == "UNKNOWN":
                db.execute("UPDATE tool_gateway_run_barrier SET state='UNKNOWN_STOP_REQUESTED', reason=? WHERE run_id=?",
                           (reason, run_id))
                return False
            return current[0] == "STOPPING"
        db.execute("INSERT INTO tool_gateway_run_barrier VALUES (?, 'STOPPING', ?)", (run_id, reason))
        return True


_ALLOWED = {"read_file": {"path", "offset", "limit"}}
_MAX_BODY = 16 * 1024


class _BoundedRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request: Request):
            lengths = request.headers.getlist("content-length")
            if len(lengths) > 1:
                raise HTTPException(status_code=400, detail="invalid content length")
            if lengths:
                try:
                    declared = int(lengths[0])
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="invalid content length") from exc
                if declared < 0:
                    raise HTTPException(status_code=400, detail="invalid content length")
                if declared > _MAX_BODY:
                    raise HTTPException(status_code=413, detail="tool request too large")
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_BODY:
                    raise HTTPException(status_code=413, detail="tool request too large")
                chunks.append(chunk)
            request._body = b"".join(chunks)
            return await original(request)

        return bounded


def _validate_call(call: _Call) -> None:
    allowed = _ALLOWED.get(call.action)
    if allowed is None or set(call.args) - allowed:
        raise HTTPException(status_code=403, detail="tool unavailable")
    for key, value in call.args.items():
        if key == "path":
            if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
                raise HTTPException(status_code=400, detail="invalid tool arguments")
        elif isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100000:
            raise HTTPException(status_code=400, detail="invalid tool arguments")


def _validate_read_target(router: ToolRouter, path: Any) -> None:
    from .privacy import is_sensitive_name

    if not isinstance(path, str) or not path:
        raise ToolError("relative file path required")
    relative = PureWindowsPath(path)
    if (relative.anchor or not relative.parts
            or any(part.startswith(".") or ":" in part or part.endswith((" ", "."))
                   or is_sensitive_name(part) for part in relative.parts)
            or relative.suffix.casefold() in {".pem", ".key", ".p12", ".pfx", ".kdbx"}):
        raise ToolError("read target unavailable")
    candidate = router.workspace.joinpath(*relative.parts)
    if router._sandbox(path) != candidate or not candidate.is_file():
        raise ToolError("read target unavailable")


def build_tool_gateway_router(*, authorizer: ToolGatewayAuthorizer, ledger: ToolCallLedger,
                              service_key: str, policy_root: Path,
                              policy_rules_path: Path | None = None,
                              require_secure_transport: bool = False) -> APIRouter:
    """Build an opt-in router; caller must enforce private/TLS transport."""
    if not isinstance(service_key, str) or len(service_key.encode("utf-8")) < 32:
        raise ValueError("service key must contain at least 32 bytes")
    if not isinstance(policy_root, Path) or (policy_rules_path is not None and not isinstance(policy_rules_path, Path)):
        raise ValueError("policy paths must be trusted Paths")
    if policy_root.resolve() != authorizer.policy_root:
        raise ValueError("policy root must match the authoritative Kernel run store")
    if ledger.path.resolve() != (authorizer.policy_root / "runtime_state.sqlite3").resolve():
        raise ValueError("ledger must use the canonical Kernel runtime path")
    authorizer.activate_effect_guard(ledger.path)
    router = APIRouter(route_class=_BoundedRoute)

    @router.post("/bauer/tool-gateway/call")
    def call(body: _Call, request: Request) -> dict[str, str]:
        if require_secure_transport and request.url.scheme != "https":
            try:
                local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
            except ValueError:
                local = False
            if not local:
                raise HTTPException(status_code=403, detail="secure transport required")
        authorization = request.headers.getlist("authorization")
        service = request.headers.getlist("x-bauer-service-key")
        if (len(authorization) != 1 or len(service) != 1
                or not hmac.compare_digest(service[0].encode("utf-8"), service_key.encode("utf-8"))):
            raise HTTPException(status_code=401, detail="tool request denied")
        try:
            context = authorizer.authorize(authorization[0])
        except ToolGatewayDenied as exc:
            raise HTTPException(status_code=403, detail="tool request denied") from exc
        _validate_call(body)
        try:
            digest = call_digest(body.action, body.args)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid tool arguments") from exc
        if not hmac.compare_digest(digest, context.call_digest):
            raise HTTPException(status_code=403, detail="tool request denied")
        if not ledger.claim(context.token_id, digest, run_id=context.gateway_run_id,
                            remote_run_id=context.remote_run_id, tool_call_id=context.tool_call_id,
                            require_active_guard=True):
            raise HTTPException(status_code=409, detail="tool request already submitted")
        try:
            # Recheck immediately before policy; final atomic execution and
            # cancellation ordering are prerequisites for activating this route.
            context = authorizer.authorize(authorization[0])
            from .core.events import EventBus
            from .privacy import redact_text

            tool_router = ToolRouter(workspace=context.workspace, policy_enabled=True,
                                     policy_root=policy_root, policy_rules_path=policy_rules_path,
                                     session_id=context.gateway_session_id, run_id=context.gateway_run_id,
                                     tool_allowlist=list(_ALLOWED), web_enabled=False,
                                     restricted_builtin_only=True,
                                     event_bus=EventBus(root=policy_root),
                                     audit_logs_dir=policy_root / "tool-gateway-audit")
            _validate_read_target(tool_router, body.args.get("path"))
            result = tool_router.execute_native_call(body.action, body.args)
            output = redact_text(result).encode("utf-8")[:65536].decode("utf-8", errors="ignore")
        except (ToolGatewayDenied, ToolError) as exc:
            ledger.reject(context.token_id)
            raise HTTPException(status_code=403, detail="tool request denied") from exc
        except BaseException:
            ledger.settle(context.token_id, completed=False)
            raise
        ledger.settle(context.token_id, completed=True)
        return {"result": output}

    return router
