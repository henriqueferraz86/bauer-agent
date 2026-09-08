"""Persistência de estado do runtime, com SQLite transacional e JSONL legado."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

class JsonlStateStore:
    """Fachada legada para o store SQLite, mantendo ``*.jsonl`` como auditoria.

    O nome é preservado para extensões que ainda o importam. Desde a migração
    do runtime, as operações delegam ao SQLite: instanciar este tipo nunca mais
    cria uma segunda fonte de verdade só porque um consumidor antigo ainda usa
    o nome histórico.
    """

    def __init__(self, root: str | Path = "memory/runtime"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._store = SqliteStateStore(self.root)

    def append(self, collection: str, record: Any) -> dict[str, Any]:
        return self._store.append(collection, record)

    def upsert(self, collection: str, record: Any) -> dict[str, Any]:
        return self._store.upsert(collection, record)

    def latest(self, collection: str, record_id: str) -> dict[str, Any] | None:
        return self._store.latest(collection, record_id)

    def list(self, collection: str) -> list[dict[str, Any]]:
        return self._store.list(collection)

    def list_latest(self, collection: str) -> list[dict[str, Any]]:
        return self._store.list_latest(collection)

    def upsert_unless_status(
        self, collection: str, record: Any, *, forbidden_statuses: set[str]
    ) -> bool:
        return self._store.upsert_unless_status(
            collection, record, forbidden_statuses=forbidden_statuses
        )

    def _path(self, collection: str) -> Path:
        safe = collection.strip().replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.jsonl"

    @staticmethod
    def _to_dict(record: Any) -> dict[str, Any]:
        if is_dataclass(record):
            return asdict(record)
        if isinstance(record, dict):
            return dict(record)
        raise TypeError(f"Unsupported record type: {type(record)!r}")


class SqliteStateStore:
    """Fonte de verdade transacional para estado compartilhado do runtime.

    O banco serializa writers de processos distintos via ``BEGIN IMMEDIATE`` e
    usa WAL para não bloquear leitores. Os ``*.jsonl`` existentes são
    importados uma única vez e continuam recebendo uma cópia auditável das
    escritas; nenhuma leitura operacional depende deles depois da migração.
    """

    def __init__(self, root: str | Path = "memory/runtime"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "runtime_state.sqlite3"
        with self._connect() as conn:
            self._init(conn)
            self._migrate_jsonl_once(conn)

    @contextmanager
    def _connect(self):
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
    def _write(conn: sqlite3.Connection):
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
            CREATE TABLE IF NOT EXISTS runtime_meta (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runtime_records (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                collection TEXT NOT NULL,
                record_id TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS runtime_records_collection_sequence
                ON runtime_records(collection, sequence);
            CREATE INDEX IF NOT EXISTS runtime_records_latest
                ON runtime_records(collection, record_id, sequence DESC);
        """)

    def _migrate_jsonl_once(self, conn: sqlite3.Connection) -> None:
        """Importa o histórico legível sem alterar os arquivos de auditoria."""
        with self._write(conn):
            if conn.execute(
                "SELECT 1 FROM runtime_meta WHERE key='jsonl_v1'"
            ).fetchone():
                return
            for path in sorted(self.root.glob("*.jsonl")):
                collection = path.stem
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    self._insert(conn, collection, record)
            conn.execute(
                "INSERT INTO runtime_meta(key, value) VALUES('jsonl_v1', 'done')"
            )

    @staticmethod
    def _insert(conn: sqlite3.Connection, collection: str, record: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO runtime_records(collection, record_id, payload) VALUES (?, ?, ?)",
            (
                collection,
                str(record.get("id") or ""),
                json.dumps(record, ensure_ascii=False, sort_keys=True),
            ),
        )

    def append(self, collection: str, record: Any) -> dict[str, Any]:
        data = JsonlStateStore._to_dict(record)
        safe_collection = self._safe_collection(collection)
        with self._connect() as conn, self._write(conn):
            self._insert(conn, safe_collection, data)
            self._append_audit(safe_collection, data)
        return data

    def upsert(self, collection: str, record: Any) -> dict[str, Any]:
        return self.append(collection, record)

    def latest(self, collection: str, record_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT payload FROM runtime_records
                   WHERE collection=? AND record_id=? ORDER BY sequence DESC LIMIT 1""",
                (self._safe_collection(collection), str(record_id)),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list(self, collection: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM runtime_records WHERE collection=? ORDER BY sequence",
                (self._safe_collection(collection),),
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def list_latest(self, collection: str) -> list[dict[str, Any]]:
        latest_by_id: dict[str, dict[str, Any]] = {}
        for record in self.list(collection):
            record_id = str(record.get("id", ""))
            if record_id:
                latest_by_id[record_id] = record
        return sorted(
            latest_by_id.values(),
            key=lambda record: str(record.get("updated_at") or record.get("started_at") or ""),
        )

    def upsert_unless_status(
        self, collection: str, record: Any, *, forbidden_statuses: set[str]
    ) -> bool:
        """Insere ``record`` apenas se o estado atual não for terminal.

        É a primitiva de compare-and-set usada por conclusões concorrentes de
        runs: o primeiro processo que grava terminal vence.
        """
        data = JsonlStateStore._to_dict(record)
        safe_collection = self._safe_collection(collection)
        record_id = str(data.get("id") or "")
        with self._connect() as conn, self._write(conn):
            row = conn.execute(
                """SELECT payload FROM runtime_records
                   WHERE collection=? AND record_id=? ORDER BY sequence DESC LIMIT 1""",
                (safe_collection, record_id),
            ).fetchone()
            if row and str(json.loads(row["payload"]).get("status") or "") in forbidden_statuses:
                return False
            self._insert(conn, safe_collection, data)
            self._append_audit(safe_collection, data)
        return True

    def export_jsonl(self, collection: str, destination: str | Path | None = None) -> Path:
        """Exporta um snapshot legível do banco sem torná-lo fonte de verdade."""
        path = Path(destination) if destination is not None else self._path(collection)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(record, ensure_ascii=False, sort_keys=True) for record in self.list(collection)]
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return path

    def _append_audit(self, collection: str, data: dict[str, Any]) -> None:
        """Escrita serializada pelo writer lock do SQLite; falha não desfaz estado."""
        try:
            path = self._path(collection)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
                fh.flush()
        except OSError:
            return  # o SQLite já confirmou o estado; auditoria é best-effort

    def _path(self, collection: str) -> Path:
        return self.root / f"{self._safe_collection(collection)}.jsonl"

    @staticmethod
    def _safe_collection(collection: str) -> str:
        return collection.strip().replace("/", "_").replace("\\", "_")


RuntimeStateStore = JsonlStateStore | SqliteStateStore
