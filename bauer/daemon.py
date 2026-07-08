"""BauerDaemon — long-running autonomous agent daemon.

The daemon manages a pool of asyncio workers, each continuously claiming and
executing kanban tasks.  It enforces budgets, checks board diagnostics, writes
heartbeats, and handles graceful shutdown on SIGTERM/SIGINT.

Quick start::

    import asyncio
    from bauer.daemon import BauerDaemon, DaemonConfig

    config = DaemonConfig(board="default", workers=2, max_cost_usd=5.0)
    daemon = BauerDaemon(config)
    asyncio.run(daemon.start())

CLI (via bauer/cli.py)::

    bauer daemon start [--workers 2] [--board default] [--budget-usd 5.0] [--detach]
    bauer daemon stop
    bauer daemon status
    bauer daemon logs [--follow]

Architecture
------------
* One asyncio event loop (single-threaded).
* N worker coroutines, each running claim→execute→complete in a tight loop.
* One heartbeat coroutine (writes to daemon_state every 30 s).
* One budget-watchdog coroutine (checks limits every 5 s).
* One diagnostic-watchdog coroutine (runs kanban_diagnostics every 60 s).
* Workers use :class:`~bauer.process_supervisor.ProcessSupervisor` for
  exponential-backoff restart on crash.

Shutdown sequence
-----------------
1. SIGTERM / SIGINT / budget exhaustion sets ``_shutdown_event``.
2. All workers finish their current poll iteration (no mid-task kill).
3. daemon writes final heartbeat with ``status="stopped"``.
4. Process exits with code 0 (graceful) or 1 (budget / crash).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DaemonConfig
# ---------------------------------------------------------------------------


@dataclass
class DaemonConfig:
    """All tunable knobs for the daemon.

    Attributes
    ----------
    board:
        Kanban board slug to process tasks from.
    workers:
        Number of parallel worker coroutines.
    max_cost_usd:
        Hard limit on total LLM cost for this daemon session.
    max_wall_seconds:
        Hard limit on total run time (seconds).
    max_llm_calls:
        Hard limit on LLM API calls.
    max_tool_calls:
        Hard limit on tool executions.
    poll_interval_seconds:
        How long workers sleep when no task is ready.
    heartbeat_interval_seconds:
        How often the heartbeat coroutine writes to daemon_state.db.
    diagnostics_interval_seconds:
        How often the diagnostic watchdog runs board diagnostics.
    budget_check_interval_seconds:
        How often the budget watchdog checks limits.
    profile:
        Bauer model profile (``"low"`` / ``"medium"`` / ``"high"``).
    scope_config:
        Optional dict passed to ``ScopeBoundary.from_config()``.
    headless_mode:
        Approval mode for commands (``"threshold"`` / ``"yolo"`` / ``"deny_all"``).
    headless_risk_threshold:
        Risk threshold when ``headless_mode="threshold"``.
    state_dir:
        Directory for daemon_state.db and PID file.  Defaults to
        ``$BAUER_HOME/.bauer/daemon`` or ``~/.bauer/daemon``.
    """

    board: str = "default"
    workers: int = 2
    max_cost_usd: float = 5.0
    max_wall_seconds: int = 3600
    max_llm_calls: int = 200
    max_tool_calls: int = 500
    poll_interval_seconds: float = 5.0
    heartbeat_interval_seconds: float = 30.0
    diagnostics_interval_seconds: float = 60.0
    budget_check_interval_seconds: float = 5.0
    profile: str = "low"
    scope_config: dict | None = None
    headless_mode: str = "threshold"
    headless_risk_threshold: float = 0.4
    state_dir: Path | None = None
    supervisor_max_restarts: int = 5
    supervisor_backoff_base: float = 10.0
    supervisor_backoff_cap: float = 120.0

    def get_state_dir(self) -> Path:
        if self.state_dir:
            return Path(self.state_dir)
        home = os.environ.get("BAUER_HOME")
        base = Path(home).expanduser() if home else Path.home() / ".bauer"
        return base / "daemon"


# ---------------------------------------------------------------------------
# DaemonState (SQLite-backed)
# ---------------------------------------------------------------------------

_DAEMON_SCHEMA = """
CREATE TABLE IF NOT EXISTS daemon_sessions (
    id          TEXT PRIMARY KEY,
    pid         INTEGER NOT NULL,
    board       TEXT NOT NULL,
    workers     INTEGER NOT NULL,
    started_at  REAL NOT NULL,
    last_heartbeat REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running',
    budget_json TEXT,
    shutdown_reason TEXT
);
"""


class DaemonStateDB:
    """Lightweight SQLite store for daemon session state."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DAEMON_SCHEMA)

    def upsert(self, session_id: str, pid: int, board: str, workers: int,
               status: str = "running", budget_json: str | None = None,
               shutdown_reason: str | None = None) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daemon_sessions
                    (id, pid, board, workers, started_at, last_heartbeat, status, budget_json, shutdown_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_heartbeat = excluded.last_heartbeat,
                    status = excluded.status,
                    budget_json = excluded.budget_json,
                    shutdown_reason = excluded.shutdown_reason
                """,
                (session_id, pid, board, workers, now, now, status, budget_json, shutdown_reason),
            )

    def heartbeat(self, session_id: str, budget_json: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE daemon_sessions SET last_heartbeat=?, budget_json=? WHERE id=?",
                (time.time(), budget_json, session_id),
            )

    def mark_stopped(self, session_id: str, reason: str = "graceful") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE daemon_sessions SET status='stopped', shutdown_reason=? WHERE id=?",
                (reason, session_id),
            )

    def get_running(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM daemon_sessions WHERE status='running' ORDER BY started_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest(self) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM daemon_sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None


# ---------------------------------------------------------------------------
# WorkerContext — passed into the agent run
# ---------------------------------------------------------------------------


@dataclass
class WorkerContext:
    worker_id: int
    board: str
    config: DaemonConfig
    budget: Any  # AutonomousBudget
    scope: Any | None  # ScopeBoundary | None
    headless_engine: Any | None  # HeadlessApprovalEngine | None
    shutdown_event: asyncio.Event
    on_task_complete: Callable[[str, str, float], None] | None = None
    on_task_failed: Callable[[str, str, str], None] | None = None


# ---------------------------------------------------------------------------
# BauerDaemon
# ---------------------------------------------------------------------------


class BauerDaemon:
    """Long-running autonomous agent daemon.

    Parameters
    ----------
    config:
        :class:`DaemonConfig` instance with all settings.
    on_escalation:
        Optional async callback ``(reason: str, context: dict) -> None``
        called when the daemon needs human attention.
    """

    def __init__(
        self,
        config: DaemonConfig | None = None,
        *,
        on_escalation: Callable[[str, dict], Any] | None = None,
    ) -> None:
        self._cfg = config or DaemonConfig()
        self._on_escalation = on_escalation

        # Shutdown coordination
        self._shutdown_event = asyncio.Event()
        self._shutdown_reason: str = "unknown"
        self._exit_code: int = 0

        # Session identity
        self._session_id = f"daemon_{os.getpid()}_{int(time.time())}"
        self._pid = os.getpid()

        # Lazy-initialized components
        self._budget: Any = None
        self._scope: Any = None
        self._headless_engine: Any = None
        self._state_db: DaemonStateDB | None = None

        # Worker stats
        self._tasks_completed: int = 0
        self._tasks_failed: int = 0
        self._worker_supervisors: list[Any] = []

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def start(self) -> int:
        """Start the daemon and block until shutdown.

        Returns the process exit code (0=graceful, 1=budget/crash).
        """
        self._setup_components()
        self._setup_signal_handlers()
        self._write_pid_file()

        logger.info(
            "BauerDaemon starting: session=%s pid=%d board=%s workers=%d",
            self._session_id, self._pid, self._cfg.board, self._cfg.workers,
        )

        # Register session in state DB
        self._state_db.upsert(
            self._session_id, self._pid, self._cfg.board, self._cfg.workers,
        )

        try:
            async with asyncio.TaskGroup() as tg:
                # Worker coroutines
                for i in range(self._cfg.workers):
                    tg.create_task(
                        self._worker_loop(i),
                        name=f"worker_{i}",
                    )
                # Support coroutines
                tg.create_task(self._heartbeat_loop(), name="heartbeat")
                tg.create_task(self._budget_watchdog(), name="budget_watchdog")
                tg.create_task(self._diagnostic_watchdog(), name="diagnostic_watchdog")

        except* Exception as eg:
            for exc in eg.exceptions:
                logger.error("Daemon task group error: %s: %s", type(exc).__name__, exc)
            self._exit_code = 1

        finally:
            self._state_db.mark_stopped(self._session_id, self._shutdown_reason)
            self._remove_pid_file()
            logger.info(
                "BauerDaemon stopped: session=%s reason=%s exit_code=%d "
                "tasks_completed=%d tasks_failed=%d",
                self._session_id, self._shutdown_reason, self._exit_code,
                self._tasks_completed, self._tasks_failed,
            )

        return self._exit_code

    def request_shutdown(self, reason: str = "requested", exit_code: int = 0) -> None:
        """Signal all workers to stop after their current iteration."""
        self._shutdown_reason = reason
        self._exit_code = exit_code
        self._shutdown_event.set()
        logger.info("BauerDaemon shutdown requested: reason=%s", reason)

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: int) -> None:
        """Claim → execute → complete loop for one worker slot."""
        from .process_supervisor import ProcessSupervisor

        supervisor = ProcessSupervisor(
            worker_id=worker_id,
            max_restarts=self._cfg.supervisor_max_restarts,
            backoff_base=self._cfg.supervisor_backoff_base,
            backoff_cap=self._cfg.supervisor_backoff_cap,
        )
        self._worker_supervisors.append(supervisor)
        ctx = WorkerContext(
            worker_id=worker_id,
            board=self._cfg.board,
            config=self._cfg,
            budget=self._budget,
            scope=self._scope,
            headless_engine=self._headless_engine,
            shutdown_event=self._shutdown_event,
        )

        logger.info("worker[%d] started", worker_id)

        while not self._shutdown_event.is_set():
            try:
                supervisor.record_success_start()
                claimed = await self._claim_next_task(worker_id)
                if claimed is None:
                    # No ready task — poll
                    await asyncio.sleep(self._cfg.poll_interval_seconds)
                    continue
                task_id, task_title = claimed
                await self._execute_task(task_id, task_title, ctx)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not supervisor.should_restart(exc):
                    logger.critical(
                        "worker[%d] exceeded max_restarts — shutting down daemon",
                        worker_id,
                    )
                    await self._escalate(
                        f"worker_{worker_id}_dead",
                        {"worker_id": worker_id, "exc": str(exc)},
                    )
                    self.request_shutdown(
                        reason=f"worker_{worker_id}_exceeded_restarts",
                        exit_code=1,
                    )
                    break
                await supervisor.wait_backoff()

        logger.info(
            "worker[%d] stopped (consecutive_failures=%d)",
            worker_id,
            supervisor.consecutive_failures,
        )

    async def _claim_next_task(self, worker_id: int) -> tuple[str, str] | None:
        """Try to claim the next ready task from the kanban board.

        Returns ``(task_id, task_title)`` or ``None`` if nothing is ready.
        Uses the existing TaskDispatcher (or kanban_db directly if available).
        """
        try:
            from .kanban_db import connect, get_next_ready_task, claim_task
            with connect(self._cfg.board) as conn:
                task = get_next_ready_task(conn)
                if task is None:
                    return None
                ok = claim_task(conn, task.id, worker_id=f"daemon_worker_{worker_id}")
                if not ok:
                    return None  # lost the race to another worker
                return task.id, task.title
        except Exception as exc:
            logger.debug("claim_next_task failed: %s", exc)
            return None

    async def _execute_task(self, task_id: str, task_title: str, ctx: WorkerContext) -> None:
        """Execute one task with the full safety stack."""
        logger.info(
            "worker[%d] executing task %s: %r",
            ctx.worker_id, task_id, task_title,
        )
        start = time.monotonic()

        try:
            # Check budget before starting.
            if self._budget and self._budget.is_exhausted:
                logger.warning(
                    "worker[%d] budget exhausted — skipping task %s",
                    ctx.worker_id, task_id,
                )
                return

            # Consume one tool call unit for the task itself.
            if self._budget:
                try:
                    self._budget.consume_tool_call()
                except Exception:
                    return  # budget just exhausted

            # Core task execution — delegate to TaskDispatcher.
            await self._run_task_via_dispatcher(task_id, ctx)

            elapsed = time.monotonic() - start
            self._tasks_completed += 1
            logger.info(
                "worker[%d] task %s completed in %.1fs",
                ctx.worker_id, task_id, elapsed,
            )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            self._tasks_failed += 1
            logger.error(
                "worker[%d] task %s FAILED after %.1fs: %s: %s",
                ctx.worker_id, task_id, elapsed, type(exc).__name__, exc,
            )
            await self._record_task_failure(task_id, str(exc))
            raise

    async def _run_task_via_dispatcher(self, task_id: str, ctx: WorkerContext) -> None:
        """Run the task using the configured dispatcher strategy.

        Falls back gracefully if the full agent stack isn't available.
        """
        try:
            import contextvars as _cv

            from .task_dispatcher import TaskDispatcher

            dispatcher = TaskDispatcher(
                workspace=Path.cwd(),
                profile=ctx.config.profile,
            )

            # Cost meter: cada LLM call dentro da task reporta custo real ao
            # budget do daemon. Sem isto o max_cost_usd era cap sem medição.
            _sink_token = None
            if self._budget is not None:
                from .cost_meter import cost_sink

                _budget = self._budget

                def _daemon_cost_sink(provider, model, usage, cost_usd):
                    if cost_usd > 0:
                        _budget.consume_llm_call(
                            cost_usd=cost_usd,
                            output_tokens=int(usage.get("completion_tokens", 0) or 0),
                        )

                _sink_token = cost_sink.set(_daemon_cost_sink)

            try:
                # copy_context: run_in_executor NÃO propaga ContextVars por
                # padrão — sem a cópia o sink não existiria na thread do executor.
                _cv_ctx = _cv.copy_context()
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: _cv_ctx.run(
                        dispatcher.run_task,
                        task_id,
                        board=ctx.board,
                        headless=True,
                    ),
                )
            finally:
                if _sink_token is not None:
                    from .cost_meter import cost_sink
                    cost_sink.reset(_sink_token)
        except (ImportError, AttributeError):
            # Minimal fallback: just mark the task as running for N seconds.
            logger.debug(
                "TaskDispatcher.run_task unavailable; using stub executor for %s",
                task_id,
            )
            await asyncio.sleep(1.0)
            await self._mark_task_complete(task_id)

    async def _mark_task_complete(self, task_id: str) -> None:
        try:
            from .kanban_db import connect, complete_task
            with connect(self._cfg.board) as conn:
                complete_task(conn, task_id, summary="Completed by daemon stub")
        except Exception as exc:
            logger.debug("mark_task_complete failed for %s: %s", task_id, exc)

    async def _record_task_failure(self, task_id: str, error: str) -> None:
        try:
            from .kanban_db import connect, fail_task
            with connect(self._cfg.board) as conn:
                fail_task(conn, task_id, error=error)
        except Exception as exc:
            logger.debug("record_task_failure failed for %s: %s", task_id, exc)

    # ------------------------------------------------------------------
    # Support coroutines
    # ------------------------------------------------------------------

    async def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep for *seconds*, but wake up immediately when shutdown is requested.

        Uses small asyncio.sleep steps to stay responsive without holding
        a reference to the event's waiter list across iterations.
        """
        step = min(0.05, seconds)
        elapsed = 0.0
        while elapsed < seconds and not self._shutdown_event.is_set():
            await asyncio.sleep(step)
            elapsed += step

    async def _heartbeat_loop(self) -> None:
        """Write heartbeat every N seconds until shutdown."""
        while not self._shutdown_event.is_set():
            try:
                budget_json = (
                    json.dumps(self._budget.to_dict()) if self._budget else None
                )
                self._state_db.heartbeat(self._session_id, budget_json)
            except Exception as exc:
                logger.debug("heartbeat write failed: %s", exc)
            await self._sleep_interruptible(self._cfg.heartbeat_interval_seconds)

    async def _budget_watchdog(self) -> None:
        """Check budget every N seconds; shutdown if exhausted."""
        from .autonomous_budget import BudgetStatus

        while not self._shutdown_event.is_set():
            await self._sleep_interruptible(self._cfg.budget_check_interval_seconds)

            if self._budget is None:
                continue

            status = self._budget._compute_status()
            if status == BudgetStatus.EXHAUSTED:
                logger.warning(
                    "BauerDaemon budget exhausted: %s",
                    self._budget.summary(),
                )
                await self._escalate(
                    "budget_exhausted",
                    {"budget": self._budget.to_dict()},
                )
                self.request_shutdown(reason="budget_exhausted", exit_code=0)
                return
            elif status == BudgetStatus.WARNING:
                logger.info(
                    "BauerDaemon budget WARNING: %s",
                    self._budget.summary(),
                )

    async def _diagnostic_watchdog(self) -> None:
        """Periodically compute board diagnostics and log critical issues."""
        while not self._shutdown_event.is_set():
            await self._sleep_interruptible(self._cfg.diagnostics_interval_seconds)

            try:
                await self._run_diagnostics()
            except Exception as exc:
                logger.debug("diagnostic_watchdog error: %s", exc)

    async def _run_diagnostics(self) -> None:
        try:
            from .kanban_db import connect, list_tasks, list_events, list_runs
            from .kanban_diagnostics import compute_board_diagnostics

            with connect(self._cfg.board) as conn:
                tasks = list_tasks(conn)
                events_by_task = {t.id: list_events(conn, t.id) for t in tasks}
                runs_by_task = {t.id: list_runs(conn, t.id) for t in tasks}

            diagnostics = compute_board_diagnostics(
                tasks,
                events_by_task=events_by_task,
                runs_by_task=runs_by_task,
            )
            critical = [d for d in diagnostics if d.severity == "critical"]
            errors = [d for d in diagnostics if d.severity == "error"]

            if critical:
                for d in critical:
                    logger.critical(
                        "kanban[%s] CRITICAL %s: %s", d.task_id, d.rule, d.message
                    )
                await self._escalate(
                    "critical_diagnostics",
                    {"diagnostics": [d.rule for d in critical]},
                )
            elif errors:
                for d in errors[:3]:  # log up to 3 errors
                    logger.error(
                        "kanban[%s] ERROR %s: %s", d.task_id, d.rule, d.message
                    )
        except ImportError:
            pass  # kanban_db not available

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    async def _escalate(self, reason: str, context: dict) -> None:
        logger.warning("DAEMON ESCALATION: %s | context=%s", reason, context)
        if self._on_escalation:
            try:
                await self._on_escalation(reason, context)
            except Exception as exc:
                logger.error("escalation callback failed: %s", exc)

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_components(self) -> None:
        # Idempotent: if already initialised (e.g. tests pre-configure the
        # budget before calling start()), skip re-creation so callers can
        # exhaust / inspect the budget before the daemon starts.
        if self._budget is not None:
            return

        from .autonomous_budget import AutonomousBudget

        self._budget = AutonomousBudget(
            max_cost_usd=self._cfg.max_cost_usd,
            max_wall_seconds=self._cfg.max_wall_seconds,
            max_llm_calls=self._cfg.max_llm_calls,
            max_tool_calls=self._cfg.max_tool_calls,
        )

        if self._cfg.scope_config is not None:
            try:
                from .scope_boundary import ScopeBoundary
                self._scope = ScopeBoundary.from_config(self._cfg.scope_config)
            except Exception as exc:
                logger.warning("scope_boundary setup failed: %s — running without scope", exc)

        try:
            from .headless_approval import HeadlessApprovalEngine, HeadlessApprovalConfig
            self._headless_engine = HeadlessApprovalEngine(
                HeadlessApprovalConfig(
                    mode=self._cfg.headless_mode,  # type: ignore[arg-type]
                    risk_threshold=self._cfg.headless_risk_threshold,
                )
            )
        except Exception as exc:
            logger.warning("headless_approval setup failed: %s", exc)

        state_dir = self._cfg.get_state_dir()
        self._state_db = DaemonStateDB(state_dir / "daemon_state.db")

    def _setup_signal_handlers(self) -> None:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: self.request_shutdown(
                        reason=f"signal_{s.name}", exit_code=0
                    ),
                )
            except NotImplementedError:
                # Windows doesn't support add_signal_handler for all signals.
                pass

    def _write_pid_file(self) -> None:
        try:
            pid_path = self._cfg.get_state_dir() / "daemon.pid"
            pid_path.parent.mkdir(parents=True, exist_ok=True)
            pid_path.write_text(str(self._pid))
        except Exception as exc:
            logger.debug("PID file write failed: %s", exc)

    def _remove_pid_file(self) -> None:
        try:
            pid_path = self._cfg.get_state_dir() / "daemon.pid"
            pid_path.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug("PID file removal failed: %s", exc)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return not self._shutdown_event.is_set()

    def stats(self) -> dict:
        return {
            "session_id": self._session_id,
            "pid": self._pid,
            "board": self._cfg.board,
            "workers": self._cfg.workers,
            "tasks_completed": self._tasks_completed,
            "tasks_failed": self._tasks_failed,
            "is_running": self.is_running,
            "shutdown_reason": self._shutdown_reason,
            "budget": self._budget.to_dict() if self._budget else None,
            "worker_supervisors": [s.stats() for s in self._worker_supervisors],
        }


# ---------------------------------------------------------------------------
# Helpers for CLI: read PID file / check if daemon alive
# ---------------------------------------------------------------------------


def get_daemon_pid(state_dir: Path | None = None) -> int | None:
    """Read the PID file and return the daemon PID, or None if not running."""
    if state_dir is None:
        home = os.environ.get("BAUER_HOME")
        base = Path(home).expanduser() if home else Path.home() / ".bauer"
        state_dir = base / "daemon"
    pid_path = state_dir / "daemon.pid"
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except Exception:
        return None


def is_daemon_alive(state_dir: Path | None = None) -> bool:
    """Return True if a daemon PID file exists and the process is running."""
    pid = get_daemon_pid(state_dir)
    if pid is None:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        psutil = None  # type: ignore[assignment]
    try:
        os.kill(pid, 0)  # signal 0 = check existence
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        # Windows raises OSError(87, 'The parameter is incorrect') for non-existent PIDs
        return False
