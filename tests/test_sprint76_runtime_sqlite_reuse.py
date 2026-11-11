"""Sprint 76 (plano 068): o runtime reutiliza conexões SQLite.

Antes, ``SqliteStateStore`` e ``BudgetLedger`` abriam e fechavam uma conexão
por operação. Em WAL, cada fechamento da última conexão fazia checkpoint e
apagava ``-wal``/``-shm``; a próxima abertura reconstruía os dois sob lock
exclusivo. No Windows essa espera chegou ao ``busy_timeout`` inteiro (run
42935274304: thread presa no primeiro pragma de uma conexão nova, aprovação
nunca gravada).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bauer.core.runtime import sqlite_connections
from bauer.core.runtime.budget_ledger import BudgetLedger
from bauer.core.runtime.state_store import SqliteStateStore
from tests._deadlines import ANTI_HANG


@pytest.fixture
def connects(monkeypatch):
    """Conta ``sqlite3.connect`` por nome de arquivo."""
    opened: list[str] = []
    real = sqlite3.connect

    def traced(*args, **kwargs):
        target = args[0] if args else kwargs["database"]
        opened.append(Path(str(target)).name)
        return real(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", traced)
    return opened


def _runtime_files(opened: list[str]) -> int:
    return sum(name in {"runtime_state.sqlite3", "budget_ledger.sqlite3"} for name in opened)


def test_governed_turn_opens_an_order_of_magnitude_fewer_connections(tmp_path, monkeypatch, connects):
    from bauer.channel_governance import TurnMetrics, build_channel_kernel
    from bauer.core.kernel import run_governed

    operations = []
    for owner in (SqliteStateStore, BudgetLedger):
        real_connect = owner._connect

        def counted(self, _real=real_connect):
            operations.append(1)
            return _real(self)

        monkeypatch.setattr(owner, "_connect", counted)

    config = SimpleNamespace(kernel=SimpleNamespace(
        enabled=True, evaluator_enabled=False, max_replans=0, heartbeat_interval_s=0))
    kernel = build_channel_kernel(config, workspace=tmp_path,
                                  sessions_dir=tmp_path / "memory" / "sessions")

    def executor(payload):
        metrics = TurnMetrics()
        with metrics.context("tg:42", payload["run_id"]):
            for index in range(5):
                kernel.bus.publish("tool.call.completed", run_id=payload["run_id"],
                                   tool_name="read_file", data={"index": index})
            return metrics.result(output="done")

    run_governed(kernel, executor, agent_id="channel.telegram", session_id="tg:42",
                 task="primeiro turno")
    connects.clear()
    operations.clear()

    result = run_governed(kernel, executor, agent_id="channel.telegram",
                          session_id="tg:42", task="turno medido")

    assert result.status == "completed"
    # Antes da sprint 76: uma conexão por operação (59 num turno com 5 eventos).
    assert len(operations) >= 50
    assert _runtime_files(connects) * 10 <= len(operations), (connects, len(operations))


def test_wal_is_not_deleted_and_rebuilt_between_operations(tmp_path):
    store = SqliteStateStore(tmp_path / "runtime")
    wal = Path(f"{store.path}-wal")
    for index in range(20):
        store.append("events", {"id": f"e{index}"})
        assert wal.exists(), f"-wal apagado depois da operação {index}"
    store.close()


def _open_handles(paths: set[str]) -> list[str]:
    fd_dir = Path("/proc/self/fd")
    found = []
    for entry in fd_dir.iterdir():
        try:
            target = os.readlink(entry)
        except OSError:
            continue
        if target in paths:
            found.append(target)
    return found


@pytest.mark.parametrize("factory", [SqliteStateStore, BudgetLedger])
def test_close_releases_every_file_handle(tmp_path, factory):
    root = tmp_path / "runtime"
    store = factory(root)
    if isinstance(store, SqliteStateStore):
        store.append("events", {"id": "e1"})
    else:
        store.reserve(run_id="r1", agent_id="a", company_id=None,
                      estimated_cost_usd=0.1, limits={})
    worker = threading.Thread(target=lambda: store.path.exists() and (
        store.append("events", {"id": "e2"}) if isinstance(store, SqliteStateStore)
        else store.used("daily")))
    worker.start()
    worker.join(ANTI_HANG)
    db = store.path.resolve()
    names = {str(db), f"{db}-wal", f"{db}-shm"}

    store.close()

    # O último fechamento faz checkpoint e remove -wal/-shm: prova de que
    # nenhuma conexão do processo ficou aberta (âncora e a da thread).
    assert not Path(f"{db}-wal").exists()
    assert not Path(f"{db}-shm").exists()
    if Path("/proc/self/fd").is_dir():
        assert _open_handles(names) == []
    # No Windows, renomear arquivo aberto falha.
    moved = db.with_name("moved.sqlite3")
    os.replace(db, moved)
    os.replace(moved, db)
    # O store continua utilizável: reabre sob demanda.
    if isinstance(store, SqliteStateStore):
        assert store.latest("events", "e1") == {"id": "e1"}
    else:
        assert store.used("daily") == pytest.approx(0.1)
    store.close()


def test_each_thread_uses_its_own_connection(tmp_path):
    store = SqliteStateStore(tmp_path / "runtime")
    owners: dict[int, set[int]] = {}
    guard = threading.Lock()
    real_insert = SqliteStateStore._insert

    def insert(conn, collection, record):
        with guard:
            owners.setdefault(id(conn), set()).add(threading.get_ident())
        real_insert(conn, collection, record)

    store._insert = insert  # type: ignore[method-assign]
    errors: list[BaseException] = []

    def write(worker: int) -> None:
        try:
            for index in range(25):
                store.append("events", {"id": f"{worker}-{index}"})
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(ANTI_HANG)

    assert errors == []
    assert len(store.list("events")) == 150
    assert len(owners) == 6
    assert all(len(threads) == 1 for threads in owners.values())
    store.close()


def test_reader_never_sees_another_threads_open_transaction(tmp_path):
    store = SqliteStateStore(tmp_path / "runtime")
    store.upsert("runs", {"id": "r1", "status": "running"})
    inside = threading.Event()
    release = threading.Event()

    def writer() -> None:
        with store.transaction() as tx:
            tx.upsert("runs", {"id": "r1", "status": "completed"})
            inside.set()
            release.wait(ANTI_HANG)

    thread = threading.Thread(target=writer)
    thread.start()
    assert inside.wait(ANTI_HANG)
    try:
        assert store.latest("runs", "r1")["status"] == "running"
    finally:
        release.set()
        thread.join(ANTI_HANG)
    assert store.latest("runs", "r1")["status"] == "completed"
    store.close()


def test_read_inside_own_transaction_keeps_committed_view(tmp_path):
    store = SqliteStateStore(tmp_path / "runtime")
    store.upsert("runs", {"id": "r1", "status": "running"})
    with store.transaction() as tx:
        tx.upsert("runs", {"id": "r1", "status": "completed"})
        # Uso reentrante na mesma thread abre conexão própria (como antes) e
        # não enxerga a transação ainda aberta.
        assert store.latest("runs", "r1")["status"] == "running"
    assert store.latest("runs", "r1")["status"] == "completed"
    store.close()


def test_connections_of_finished_threads_are_released(tmp_path):
    store = SqliteStateStore(tmp_path / "runtime")
    for index in range(20):
        thread = threading.Thread(target=store.append, args=("events", {"id": str(index)}))
        thread.start()
        thread.join(ANTI_HANG)
    store.append("events", {"id": "main"})
    # âncora + thread principal + no máximo a última thread encerrada
    assert store._pool.open_connections() <= 3
    store.close()


def test_closing_mid_operation_defers_to_the_owner(tmp_path):
    store = SqliteStateStore(tmp_path / "runtime")
    inside = threading.Event()
    release = threading.Event()
    seen: list[str] = []

    def reader() -> None:
        with store._connect() as conn:
            inside.set()
            release.wait(ANTI_HANG)
            seen.append(conn.execute("SELECT 'ok'").fetchone()[0])

    thread = threading.Thread(target=reader)
    thread.start()
    assert inside.wait(ANTI_HANG)
    store.close()
    release.set()
    thread.join(ANTI_HANG)
    assert seen == ["ok"]
    assert store._pool.open_connections() == 0
    store.close()


@pytest.mark.skipif(sys.platform == "win32",
                    reason="no Windows, arquivo aberto não pode ser apagado")
def test_runtime_dir_removed_on_disk_is_reopened_fresh(tmp_path):
    import shutil

    root = tmp_path / "runtime"
    store = SqliteStateStore(root)
    store.append("events", {"id": "old"})
    # Conexões abertas apontam para o inode apagado; o pool percebe a troca
    # de arquivo e reabre, em vez de ler e gravar num banco que sumiu.
    shutil.rmtree(root)

    fresh = SqliteStateStore(root)
    assert fresh.list("events") == []
    fresh.append("events", {"id": "new"})
    assert [r["id"] for r in SqliteStateStore(root).list("events")] == ["new"]
    fresh.close()


def test_restore_snapshot_closes_pooled_connections_first(tmp_path):
    from bauer.core.runtime.snapshot import create_snapshot, restore_snapshot

    root = tmp_path / "runtime"
    store = SqliteStateStore(root)
    store.append("events", {"id": "before"})
    snapshot = create_snapshot(root, tmp_path / "snapshot")
    store.append("events", {"id": "after"})

    restore_snapshot(root, snapshot)

    assert [record["id"] for record in store.list("events")] == ["before"]
    store.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="fork é POSIX")
def test_forked_child_opens_its_own_connections(tmp_path):
    import multiprocessing

    store = SqliteStateStore(tmp_path / "runtime")
    store.append("events", {"id": "parent"})
    context = multiprocessing.get_context("fork")
    child = context.Process(target=_child_append, args=(store,))
    child.start()
    child.join(ANTI_HANG)
    assert child.exitcode == 0
    store.append("events", {"id": "parent-2"})
    assert [r["id"] for r in store.list("events")] == ["parent", "child", "parent-2"]
    store.close()


def _child_append(store: SqliteStateStore) -> None:
    pool = store._pool
    store.append("events", {"id": "child"})
    # O filho abriu conexões próprias; as herdadas ficam intocadas.
    assert pool.open_connections() == 2
    assert sqlite_connections._INHERITED
    os._exit(0)


def test_process_exit_closes_connections(tmp_path):
    import subprocess

    root = tmp_path / "runtime"
    code = (
        "import sys\n"
        "from bauer.core.runtime.state_store import SqliteStateStore\n"
        "store = SqliteStateStore(sys.argv[1])\n"
        "store.append('events', {'id': 'e1'})\n"
        "import bauer.core.runtime.sqlite_connections as pools\n"
        "keep = pools._POOLS\n"
    )
    subprocess.run([sys.executable, "-c", code, str(root)], check=True, timeout=ANTI_HANG)
    db = root / "runtime_state.sqlite3"
    assert db.exists()
    assert not Path(f"{db}-wal").exists()
