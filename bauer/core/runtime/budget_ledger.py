"""Process-safe cost reservations for runtime autonomy budgets.

The JSONL ``run_costs`` file remains the human-readable audit trail.  This
ledger is the authoritative, transactional counter used for admission.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


class BudgetLedgerExceeded(RuntimeError):
    """An atomic reservation would exceed one of the configured limits."""


class BudgetLedger:
    """A small SQLite ledger, isolated from session and Kanban databases."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "budget_ledger.sqlite3"
        with self._connect() as conn:
            self._init(conn)
            self._migrate_jsonl_once(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=5.0)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            yield conn
        finally:
            conn.close()

    @staticmethod
    @contextmanager
    def _write(conn: sqlite3.Connection) -> Iterator[None]:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _init(conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS budget_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS budget_runs (
                run_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                company_id TEXT,
                created_at REAL NOT NULL,
                reserved_usd REAL NOT NULL DEFAULT 0,
                actual_usd REAL NOT NULL DEFAULT 0,
                state TEXT NOT NULL DEFAULT 'reserved',
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS budget_runs_created ON budget_runs(created_at);
            CREATE INDEX IF NOT EXISTS budget_runs_agent ON budget_runs(agent_id);
            CREATE INDEX IF NOT EXISTS budget_runs_company ON budget_runs(company_id);
        """)

    def _migrate_jsonl_once(self, conn: sqlite3.Connection) -> None:
        """Import historic costs once, atomically, without modifying the audit log."""
        with self._write(conn):
            if conn.execute("SELECT 1 FROM budget_meta WHERE key='jsonl_v1'").fetchone():
                return
            path = self.root / "run_costs.jsonl"
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                        run_id = str(item.get("run_id") or "")
                        if not run_id:
                            continue
                        amount = max(0.0, float(item.get("cost_usd") or 0))
                        timestamp = _timestamp(item.get("timestamp"))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    conn.execute(
                        """INSERT INTO budget_runs(run_id, agent_id, company_id, created_at,
                           reserved_usd, actual_usd, state, updated_at)
                           VALUES (?, ?, ?, ?, 0, ?, 'settled', ?)
                           ON CONFLICT(run_id) DO UPDATE SET
                             actual_usd=excluded.actual_usd, state='settled',
                             updated_at=excluded.updated_at""",
                        (run_id, str(item.get("agent_id") or "default"),
                         str(item.get("company_id") or "") or None,
                         timestamp, amount, timestamp),
                    )
            conn.execute("INSERT INTO budget_meta(key, value) VALUES('jsonl_v1', 'done')")

    def reserve(self, *, run_id: str, agent_id: str, company_id: str | None,
                estimated_cost_usd: float, limits: dict[str, float | None]) -> None:
        """Reserve estimate once per run, checking all scopes under one writer lock."""
        estimate = max(0.0, float(estimated_cost_usd))
        now = _timestamp(None)
        with self._connect() as conn, self._write(conn):
            existing = conn.execute(
                "SELECT * FROM budget_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                return  # idempotent admission/retry for this run
            for scope, limit in limits.items():
                if limit is None:
                    continue
                used = self._used(conn, scope, agent_id, company_id, now)
                if used + estimate > float(limit):
                    raise BudgetLedgerExceeded(
                        f"budget exceeded for {scope}: used=${used:.4f} limit=${float(limit):.4f}"
                    )
            conn.execute(
                """INSERT INTO budget_runs(run_id, agent_id, company_id, created_at,
                   reserved_usd, actual_usd, state, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, 'reserved', ?)""",
                (run_id, agent_id, company_id, now, estimate, now),
            )

    def settle(self, *, run_id: str, actual_cost_usd: float, agent_id: str = "default",
               company_id: str | None = None) -> bool:
        """Replace an active reservation by actual cost; return whether it changed."""
        now = _timestamp(None)
        amount = max(0.0, float(actual_cost_usd))
        with self._connect() as conn, self._write(conn):
            current = conn.execute(
                "SELECT state, actual_usd FROM budget_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if (current is not None and current["state"] == "settled"
                    and float(current["actual_usd"] or 0) == amount):
                return False
            conn.execute(
                """INSERT INTO budget_runs(run_id, agent_id, company_id, created_at,
                   reserved_usd, actual_usd, state, updated_at)
                   VALUES (?, ?, ?, ?, 0, ?, 'settled', ?)
                   ON CONFLICT(run_id) DO UPDATE SET reserved_usd=0,
                     actual_usd=excluded.actual_usd, state='settled', updated_at=excluded.updated_at""",
                (run_id, agent_id, company_id, now, amount, now),
            )
            return True

    def release(self, run_id: str) -> None:
        """Release an unspent reservation on terminal failure/cancellation."""
        with self._connect() as conn, self._write(conn):
            conn.execute(
                "UPDATE budget_runs SET reserved_usd=0, state='released', updated_at=? "
                "WHERE run_id=? AND state='reserved'", (_timestamp(None), run_id),
            )

    def used(self, scope: str, *, agent_id: str | None = None,
             company_id: str | None = None) -> float:
        with self._connect() as conn:
            return self._used(conn, scope, agent_id, company_id, _timestamp(None))

    @staticmethod
    def _used(conn: sqlite3.Connection, scope: str, agent_id: str | None,
              company_id: str | None, now: float) -> float:
        # Per-run limits apply to the candidate reservation only. ``reserve``
        # is idempotent by run_id, so an existing run has already been checked;
        # a new run starts at zero rather than inheriting global spend.
        if scope == "run":
            return 0.0
        where = ["state != 'released'"]
        values: list[Any] = []
        if scope == "daily":
            where.append("created_at >= ?")
            values.append(now - 86400)
        elif scope == "weekly":
            where.append("created_at >= ?")
            values.append(now - 7 * 86400)
        elif scope == "monthly":
            where.append("created_at >= ?")
            values.append(now - 30 * 86400)
        elif scope == "agent":
            where.append("agent_id = ?")
            values.append(agent_id or "")
        elif scope == "company":
            where.append("company_id = ?")
            values.append(company_id or "")
        row = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN state='reserved' THEN reserved_usd ELSE actual_usd END), 0) AS used "
            "FROM budget_runs WHERE " + " AND ".join(where), values,
        ).fetchone()
        return float(row["used"] or 0.0)


def _timestamp(value: Any) -> float:
    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC).timestamp()
