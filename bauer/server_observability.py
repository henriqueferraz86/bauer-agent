"""Primitivas de observabilidade seguras para o servidor HTTP."""

from __future__ import annotations

import re
import sqlite3
import ipaddress
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_RUNTIME_DATABASES = ("runtime_state.sqlite3", "budget_ledger.sqlite3")


class Metrics:
    """Contadores Prometheus por processo para o servidor HTTP."""

    def __init__(self):
        self.requests_total: int = 0
        self.requests_errors: int = 0
        self.chat_requests_total: int = 0
        self.stream_requests_total: int = 0
        self.tool_calls_total: int = 0
        self.rate_limited_total: int = 0
        self.client_disconnects_total: int = 0
        self._start_time: float = time.time()

    def to_prometheus(self, model: str = "", provider: str = "", runtime: dict | None = None) -> str:
        """Serializa métricas no formato Prometheus text exposition."""
        uptime = time.time() - self._start_time
        runtime = runtime or {}
        lines = [
            "# HELP bauer_uptime_seconds Tempo em segundos desde o inicio do servidor",
            "# TYPE bauer_uptime_seconds gauge", f'bauer_uptime_seconds {uptime:.2f}', "",
            "# HELP bauer_requests_total Total de requisicoes HTTP recebidas",
            "# TYPE bauer_requests_total counter", f'bauer_requests_total {self.requests_total}', "",
            "# HELP bauer_requests_errors_total Total de erros HTTP (5xx)",
            "# TYPE bauer_requests_errors_total counter", f'bauer_requests_errors_total {self.requests_errors}', "",
            "# HELP bauer_chat_requests_total Total de chamadas ao endpoint /chat",
            "# TYPE bauer_chat_requests_total counter", f'bauer_chat_requests_total {self.chat_requests_total}', "",
            "# HELP bauer_stream_requests_total Total de chamadas ao endpoint /stream",
            "# TYPE bauer_stream_requests_total counter", f'bauer_stream_requests_total {self.stream_requests_total}', "",
            "# HELP bauer_client_disconnects_total Requisicoes abandonadas pelo cliente (SSE)",
            "# TYPE bauer_client_disconnects_total counter", f'bauer_client_disconnects_total {self.client_disconnects_total}', "",
            "# HELP bauer_tool_calls_total Total de tool calls executadas",
            "# TYPE bauer_tool_calls_total counter", f'bauer_tool_calls_total {self.tool_calls_total}', "",
            "# HELP bauer_rate_limited_total Total de requisicoes bloqueadas por rate limit",
            "# TYPE bauer_rate_limited_total counter", f'bauer_rate_limited_total {self.rate_limited_total}', "",
            "# HELP bauer_runs_total Total de runs registradas", "# TYPE bauer_runs_total counter",
            f'bauer_runs_total {int(runtime.get("runs_total", 0))}', "",
            "# HELP bauer_runs_active Runs em execucao ou aguardando aprovacao", "# TYPE bauer_runs_active gauge",
            f'bauer_runs_active {int(runtime.get("runs_active", 0))}', "",
            "# HELP bauer_runs_failed_total Total de runs com falha", "# TYPE bauer_runs_failed_total counter",
            f'bauer_runs_failed_total {int(runtime.get("runs_failed_total", 0))}', "",
            "# HELP bauer_approvals_pending Aprovacoes pendentes", "# TYPE bauer_approvals_pending gauge",
            f'bauer_approvals_pending {int(runtime.get("approvals_pending", 0))}', "",
            "# HELP bauer_policy_denied_total Total de decisoes de policy negadas", "# TYPE bauer_policy_denied_total counter",
            f'bauer_policy_denied_total {int(runtime.get("policy_denied_total", 0))}', "",
            "# HELP bauer_skill_executions_total Total de execucoes de skill", "# TYPE bauer_skill_executions_total counter",
            f'bauer_skill_executions_total {int(runtime.get("skill_executions_total", 0))}', "",
            "# HELP bauer_agent_runtime_adapter_calls_total Total de chamadas a runtime adapters",
            "# TYPE bauer_agent_runtime_adapter_calls_total counter",
            f'bauer_agent_runtime_adapter_calls_total {int(runtime.get("agent_runtime_adapter_calls_total", 0))}',
        ]
        if model:
            lines += ["", "# HELP bauer_info Informacoes do servidor (gauge constante = 1)",
                      "# TYPE bauer_info gauge", f'bauer_info{{model="{model}",provider="{provider}"}} 1']
        return "\n".join(lines) + "\n"


class RateLimiter:
    """Rate limiter thread-safe de janela deslizante, limitado a 10 mil chaves."""

    _MAX_KEYS = 10_000

    def __init__(self, max_requests: int = 60, window_s: float = 60.0):
        self.max_requests = max_requests
        self.window_s = window_s
        self._windows: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_s
        for key in [key for key, window in self._windows.items() if not window or window[-1] < cutoff]:
            del self._windows[key]

    def is_allowed(self, key: str) -> bool:
        if self.max_requests <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            window = self._windows.get(key)
            if window is None:
                if len(self._windows) >= self._MAX_KEYS:
                    self._prune_locked(now)
                if len(self._windows) >= self._MAX_KEYS:
                    return False
                window = self._windows[key] = deque()
            cutoff = now - self.window_s
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self.max_requests:
                return False
            window.append(now)
            return True

    def retry_after(self, key: str) -> float:
        with self._lock:
            window = self._windows.get(key)
            if not window:
                return 0.0
            oldest = window[0]
        remaining = (oldest + self.window_s) - time.monotonic()
        return max(0.0, min(self.window_s, remaining))

    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._windows)


@dataclass(frozen=True)
class ObservabilityDeps:
    """Stores explicitamente consumidos pelas rotas operacionais de leitura."""

    store: Any
    event_bus: Any
    run_manager: Any
    trace_store: Any
    audit_log: Any
    approval_manager: Any
    runtime_root: Path


def _window_delta(window: str):
    from datetime import timedelta
    from fastapi import HTTPException

    match = re.fullmatch(r"(\d+)([mhdw])", window.strip().lower())
    if not match:
        raise HTTPException(status_code=400, detail="Use janela como 24h, 7d ou 2w.")
    quantity, unit = int(match.group(1)), match.group(2)
    return {"m": timedelta(minutes=quantity), "h": timedelta(hours=quantity),
            "d": timedelta(days=quantity), "w": timedelta(weeks=quantity)}[unit]


def register_observability_routes(app, deps: ObservabilityDeps, verify_key) -> None:
    """Registra sessões, eventos, runs, auditoria e aprovações sem capturar create_app."""
    from dataclasses import asdict
    from datetime import datetime
    from fastapi import Depends, HTTPException, Query
    from .core.events import EventBus
    from .core.observability import AuditLog

    @app.get("/sessions")
    def list_sessions(_: None = Depends(verify_key)):
        return {"sessions": deps.store.list_sessions()}

    @app.get("/events")
    def list_events(limit: int = Query(100, ge=1, le=1000), _: None = Depends(verify_key)):
        return {"events": [EventBus.to_dict(event) for event in deps.event_bus.list_events(limit=limit)]}

    @app.get("/runs")
    def list_runs(_: None = Depends(verify_key)):
        return {"runs": [asdict(run) for run in deps.run_manager.list_runs()]}

    @app.get("/runs/{run_id}")
    def get_run(run_id: str, _: None = Depends(verify_key)):
        run = deps.run_manager.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(run)

    @app.get("/runs/{run_id}/events")
    def list_run_events(run_id: str, _: None = Depends(verify_key)):
        return {"events": [EventBus.to_dict(event) for event in deps.event_bus.list_events(run_id=run_id)]}

    @app.get("/runs/{run_id}/trace")
    def get_run_trace(run_id: str, _: None = Depends(verify_key)):
        if deps.run_manager.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return deps.trace_store.get_trace(run_id)

    @app.get("/audit")
    def list_audit(run_id: str = Query("", description="filtra por run_id"),
                   limit: int = Query(100, ge=1, le=1000), _: None = Depends(verify_key)):
        return {"audit": [AuditLog.to_dict(record) for record in deps.audit_log.list_records(
            run_id=run_id or None, limit=limit)]}

    @app.get("/audit/report")
    def audit_report_endpoint(last: str = Query("24h", description="janela: 24h, 7d, 2w"),
                              _: None = Depends(verify_key)):
        from .core.audit import build_report
        return asdict(build_report(deps.runtime_root, since=datetime.now() - _window_delta(last), window_label=last))

    @app.get("/audit/runs/{run_id}")
    def audit_run_endpoint(run_id: str, _: None = Depends(verify_key)):
        from .core.audit import audit_run
        audited = audit_run(deps.runtime_root, run_id, include_events=True, include_tools=True)
        if audited is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(audited)

    @app.get("/audit/runs/{run_id}/score")
    def audit_score_endpoint(run_id: str, _: None = Depends(verify_key)):
        from .core.audit import score_run_by_id
        score = score_run_by_id(deps.runtime_root, run_id)
        if score is None:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' nao encontrada.")
        return asdict(score)

    @app.get("/audit/skills/insights")
    def audit_skill_insights_endpoint(last: str = Query("7d", description="janela: 24h, 7d, 2w"),
                                      _: None = Depends(verify_key)):
        from .core.audit import build_skill_insights
        return asdict(build_skill_insights(deps.runtime_root, since=datetime.now() - _window_delta(last),
                                           window_label=last, suggest_new=True))

    @app.get("/approvals")
    def list_approvals(status: str = Query("", description="pending | approved | denied"),
                       _: None = Depends(verify_key)):
        return {"approvals": [asdict(record) for record in deps.approval_manager.list(status=status or None)]}

    @app.post("/approvals/{approval_id}/approve")
    def approve_request(approval_id: str, _: None = Depends(verify_key)):
        try:
            from .core.kernel import BauerKernel
            result = BauerKernel(runs=deps.run_manager, bus=deps.event_bus, approvals=deps.approval_manager).approve(approval_id)
            return asdict(result) if hasattr(result, "__dataclass_fields__") else result
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' nao encontrado.")

    @app.post("/approvals/{approval_id}/deny")
    def deny_request(approval_id: str, _: None = Depends(verify_key)):
        try:
            from .core.kernel import BauerKernel
            return BauerKernel(runs=deps.run_manager, bus=deps.event_bus, approvals=deps.approval_manager).deny(approval_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Approval '{approval_id}' nao encontrado.")

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str, _: None = Depends(verify_key)):
        if not deps.store.delete(session_id):
            raise HTTPException(status_code=404, detail=f"Sessao '{session_id}' nao encontrada.")
        return {"deleted": session_id}


def request_id(incoming: str | None) -> str:
    """Aceita somente IDs de correlação de formato limitado."""
    candidate = (incoming or "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def apply_security_headers(response) -> None:
    """Headers seguros que não dependem de saber onde TLS foi terminado."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")


def runtime_ready(root: Path) -> tuple[bool, str | None]:
    """Checa stores locais sem chamar providers ou revelar dados persistidos."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".readyz-probe"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        for name in _RUNTIME_DATABASES:
            path = root / name
            if not path.exists():
                continue
            with sqlite3.connect(str(path), timeout=2.0) as conn:
                result = conn.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                return False, "runtime database integrity check failed"
    except (OSError, sqlite3.DatabaseError):
        return False, "runtime storage unavailable"
    return True, None


def parse_trusted_proxies(entries: list[str] | None) -> tuple[list, bool]:
    """Converte IPs/CIDRs de proxy em redes; `*` é opt-in explícito."""
    networks: list = []
    wildcard = False
    for raw in entries or []:
        item = str(raw).strip()
        if item == "*":
            wildcard = True
        elif item:
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
    return networks, wildcard


def peer_is_trusted(peer: str, networks: list, wildcard: bool) -> bool:
    if wildcard:
        return True
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip_from(peer: str, forwarded: str, networks: list, wildcard: bool) -> str:
    """Honra XFF somente quando a conexão vem de proxy explicitamente confiável."""
    if not peer_is_trusted(peer, networks, wildcard):
        return peer or "unknown"
    for candidate in reversed([item.strip() for item in (forwarded or "").split(",") if item.strip()]):
        if not peer_is_trusted(candidate, networks, False):
            return candidate
    return peer or "unknown"
