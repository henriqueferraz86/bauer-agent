"""Live channel workers and their authenticated cancellation ownership.

This registry records worker liveness; the Kernel alone records run outcomes.
It deliberately cannot resume a Python worker after process restart.

Also owns the background-task delivery registry (sprint 43, plano 063): a
channel-agnostic "mail slot" that a background watcher (``run_command(
background=true)``, `bauer/tools/execution.py`) uses to push a completion
message back into the session that created it, without `ExecToolsMixin`
needing to know anything about Telegram/web/CLI.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .execution_control import ExecutionControl

logger = logging.getLogger("bauer.channel_execution")


@dataclass
class SessionWorker:
    done: threading.Event = field(default_factory=threading.Event)
    stop_queue: threading.Event = field(default_factory=threading.Event)
    response: str = ""
    error: BaseException | None = None
    current: ChannelExecution | None = None
    discarded_pending: int = 0
    timed_out: bool = False


@dataclass
class ChannelExecution:
    owner: str
    channel: str
    chat_id: str
    project_id: str | None
    session_key: str
    workspace: Path
    batch: SessionWorker
    job_id: str = field(default_factory=lambda: f"job-{uuid4().hex[:16]}")
    started_at: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    control: ExecutionControl = field(init=False)
    requested_reason: str | None = None
    phase: str = "admitting"

    def __post_init__(self) -> None:
        self.control = ExecutionControl(session_id=self.session_key, workspace=self.workspace)

    @property
    def id(self) -> str:
        return self.control.run_id or self.job_id


class ChannelExecutionRegistry:
    """Own live worker references, bounded history and no terminal mutations."""

    def __init__(self, history_limit: int = 100) -> None:
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, ChannelExecution] = OrderedDict()
        self._history_limit = history_limit

    def add(self, execution: ChannelExecution) -> None:
        with self._lock:
            self._entries[execution.job_id] = execution
            finished = [key for key, value in self._entries.items() if value.done.is_set()]
            for key in finished[:-self._history_limit] if self._history_limit else finished:
                self._entries.pop(key, None)

    def owned(self, *, owner: str, channel: str, chat_id: str,
              project_id: str | None, run_id: str | None = None,
              active_only: bool = True) -> list[ChannelExecution]:
        with self._lock:
            return [job for job in self._entries.values()
                    if job.owner == owner and job.channel == channel
                    and job.chat_id == chat_id and job.project_id == project_id
                    and (not active_only or not job.done.is_set())
                    and (run_id is None or run_id in {job.id, job.job_id})]

    def active_session(self, session_key: str) -> bool:
        with self._lock:
            return any(job.session_key == session_key and not job.done.is_set()
                       for job in self._entries.values())

    def active_for_session(self, session_key: str) -> ChannelExecution | None:
        with self._lock:
            return next((job for job in reversed(self._entries.values())
                         if job.session_key == session_key and not job.done.is_set()), None)


# ── Background-task delivery (sprint 43, plano 063) ─────────────────────────
#
# A single, channel-agnostic mail slot: anything holding a ``session_id`` and
# a block of text can push it back into the conversation that owns that
# session, without knowing which bridge/backend implements it. Registered by
# each bridge at construction time (e.g. `TelegramBridge.__init__` registers
# the `"tg:"` prefix); resolved by prefix at delivery time.
#
# Deliberately in-memory and single-process: Bauer runs one gateway process
# per installation (plano 063 explicitly rules out a distributed/durable
# queue), so this does not survive a gateway restart. If the gateway process
# restarts while a background task is still running, the watcher thread that
# would have delivered its result dies with it — the background process
# itself (not a child of the gateway) keeps running orphaned. Documented
# limitation, not solved here.

DeliverFn = Callable[[str, str], bool]

_delivery_channels: dict[str, DeliverFn] = {}
_delivery_lock = threading.Lock()

# Retry backoff for transient delivery failures (network timeout, Telegram
# 429/5xx) — same short schedule used as reference in the Hermes design
# (`gateway/wake.py:25`), cited in plano 063.
RETRY_DELAYS_S: tuple[float, ...] = (2.0, 5.0, 10.0)

# Substrings (case-insensitive) recognizable in a delivery error that mean
# "retrying will never work" — chat/token is permanently bad, not a blip.
_PERMANENT_ERROR_MARKERS = (
    "chat not found",
    "bot was blocked",
    "bot was kicked",
    "user is deactivated",
    "unauthorized",
    "forbidden",
    "peer_id_invalid",
)


def register_delivery_channel(prefix: str, deliver_fn: DeliverFn) -> None:
    """Register how to deliver text to any ``session_id`` starting with `prefix`.

    Re-registering the same prefix replaces the previous handler (a bridge
    re-registers on every construction — fine, Bauer runs one bridge
    instance per channel per gateway process).
    """
    with _delivery_lock:
        _delivery_channels[prefix] = deliver_fn


def unregister_delivery_channel(prefix: str) -> None:
    """Remove a previously registered prefix, if present. Mostly for tests."""
    with _delivery_lock:
        _delivery_channels.pop(prefix, None)


def deliver_to_session(session_id: str, text: str) -> bool:
    """Single delivery point — resolves the channel by the session_id prefix.

    Returns ``False`` (not an error, a no-op) when no channel is registered
    for this session_id — e.g. a CLI/web session that has no "push" channel
    at all. May raise whatever the registered `deliver_fn` raises.
    """
    with _delivery_lock:
        channels = dict(_delivery_channels)
    for prefix, fn in channels.items():
        if session_id.startswith(prefix):
            return fn(session_id, text)
    return False


def _is_permanent_delivery_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _PERMANENT_ERROR_MARKERS)


def deliver_with_retry(
    session_id: str, text: str, *,
    delays: tuple[float, ...] = RETRY_DELAYS_S,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Call `deliver_to_session`, retrying transient failures with a short backoff.

    - `deliver_to_session` returning `False` (no exception) means no channel
      is registered for this session — retrying cannot fix that, give up.
    - An exception classified as permanent (bad chat/token, blocked bot)
      gives up immediately.
    - Any other exception is treated as transient and retried up to
      `len(delays)` more times.
    """
    last_exc: BaseException | None = None
    for attempt, delay in enumerate((0.0, *delays)):
        if delay:
            sleep(delay)
        try:
            delivered = deliver_to_session(session_id, text)
        except Exception as exc:  # noqa: BLE001 — classified below, not swallowed
            last_exc = exc
            if _is_permanent_delivery_error(exc):
                logger.error(
                    "Entrega para %s falhou permanentemente: %s", session_id, exc,
                )
                return False
            logger.warning(
                "Entrega para %s falhou (tentativa %d/%d, transitório): %s",
                session_id, attempt + 1, len(delays) + 1, exc,
            )
            continue
        if delivered:
            return True
        logger.info("Nenhum canal de entrega registrado para %s — no-op.", session_id)
        return False
    logger.error(
        "Entrega para %s esgotou tentativas de retry: %s", session_id, last_exc,
    )
    return False


# ── Idempotência local (claim em memória) ────────────────────────────────────

_delivered_keys: set[str] = set()
_delivered_keys_lock = threading.Lock()


def claim_delivery(key: str) -> bool:
    """Atomically claim a delivery key; True only for the first caller.

    Marked claimed BEFORE delivery is attempted (not after), so a defensive
    double-invocation of the watcher can't race into sending the message
    twice. In-memory only — does not survive a gateway restart (consistent
    with plano 063 explicitly ruling out a distributed/durable claim).
    """
    with _delivered_keys_lock:
        if key in _delivered_keys:
            return False
        _delivered_keys.add(key)
        return True


# ── Formatting + the background-completion delivery entry point ────────────

_COMMAND_TRUNCATE_CHARS = 200
_OUTPUT_TRUNCATE_CHARS = 2000


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n[... truncado, {len(text) - limit} chars omitidos]"


def format_background_result(
    pid: str, command: str, returncode: int | None,
    stdout_buf: list[str], stderr_buf: list[str],
) -> str:
    """Build the explicit-header text sent when a background task completes.

    A new message is always sent (no "edit the live message in place" —
    there is no way to track that a Telegram message is still live/editable
    today, see ARCHITECTURE.md for sprint 43), so the header must stand on
    its own for a user who gets this with no immediate turn context.
    """
    cmd = command if len(command) <= _COMMAND_TRUNCATE_CHARS else (
        command[:_COMMAND_TRUNCATE_CHARS].rstrip() + "…"
    )
    lines = [
        f"Resultado da tarefa em background (PID {pid})",
        f"$ {cmd}",
        "-------------",
        f"exit: {returncode}",
    ]
    stdout = "".join(stdout_buf)
    stderr = "".join(stderr_buf)
    if stdout:
        lines.append("--- stdout ---")
        lines.append(_truncate(stdout, _OUTPUT_TRUNCATE_CHARS))
    if stderr:
        lines.append("--- stderr ---")
        lines.append(_truncate(stderr, _OUTPUT_TRUNCATE_CHARS))
    return "\n".join(lines)


def deliver_background_completion(
    *, pid: str, command: str, returncode: int | None,
    stdout_buf: list[str], stderr_buf: list[str],
    session_id: str, started_at: float,
) -> bool:
    """Entry point the `run_command(background=true)` watcher calls on exit.

    Channel-agnostic and not `run_command`-specific in spirit — any caller
    with a `session_id` and some text could reuse `deliver_with_retry`
    directly; this wrapper only adds the idempotency claim and the
    background-task text template that `run_command` needs.
    """
    key = f"{pid}:{started_at}"
    if not claim_delivery(key):
        logger.debug("Entrega para PID %s (%s) já reivindicada — ignorando.", pid, key)
        return False
    text = format_background_result(pid, command, returncode, stdout_buf, stderr_buf)
    return deliver_with_retry(session_id, text)


def governance_stop_reason(kernel: Any, metrics: Any, execution: ChannelExecution,
                           *, max_runtime_s: float = 0) -> str | None:
    """Read kill-switch and live spending without changing the budget profile.

    Persisted spend includes previous executor attempts; this attempt's recorder
    is added only until the Kernel settles it on return.
    """
    if kernel is None:
        return None
    if kernel.control is not None and kernel.control.kill_switch_enabled():
        return "kill_switch"
    run = kernel.runs.get_run(execution.control.run_id) if execution.control.run_id else None
    if run is not None and run.status in {"cancelled", "failed"}:
        return "cancelled"
    if max_runtime_s > 0 and time.monotonic() - execution.started_at >= max_runtime_s:
        return "budget_exhausted"
    if kernel.budget is None:
        return None
    status = kernel.budget.status()
    profile = status["profile"]
    if profile["mode"] == "locked":
        return "budget_exhausted"
    cost = metrics.result()["cost_estimate"]
    for scope in ("daily", "weekly", "monthly"):
        period = status[scope]
        limit = period["limit_usd"]
        if limit is not None and period["used_usd"] + cost >= limit:
            return "budget_exhausted"
    run_limit = profile.get("max_cost_usd_per_run")
    if run_limit is not None and cost + float(getattr(run, "cost_estimate", 0) or 0) >= run_limit:
        return "budget_exhausted"
    if run is not None:
        agent = status["by_agent"].get(run.agent_id)
        if agent and agent["used_usd"] + cost >= agent["limit_usd"]:
            return "budget_exhausted"
    return None
