"""Testes do estado persistente do runtime, incluindo concorrência."""

from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path

from bauer.core.runtime.state_store import JsonlStateStore, SqliteStateStore


def _sqlite_process_writer(root: str, process_id: int, count: int) -> None:
    """Target de processo precisa estar no nível do módulo para Windows/spawn."""
    store = SqliteStateStore(root)
    for index in range(count):
        store.append("events", {"id": f"p{process_id}-{index}", "process": process_id})


def test_append_list_latest_roundtrip(tmp_path: Path):
    store = JsonlStateStore(root=tmp_path)
    store.append("runs", {"id": "r1", "status": "queued", "updated_at": "1"})
    store.append("runs", {"id": "r1", "status": "running", "updated_at": "2"})
    store.append("runs", {"id": "r2", "status": "queued", "updated_at": "1"})

    assert len(store.list("runs")) == 3
    assert store.latest("runs", "r1") == {"id": "r1", "status": "running", "updated_at": "2"}
    latest = store.list_latest("runs")
    assert {r["id"] for r in latest} == {"r1", "r2"}
    assert next(r for r in latest if r["id"] == "r1")["status"] == "running"


def test_missing_collection_is_empty(tmp_path: Path):
    store = JsonlStateStore(root=tmp_path)
    assert store.list("nada") == []
    assert store.latest("nada", "x") is None


def test_concurrent_appends_do_not_corrupt(tmp_path: Path):
    """Múltiplas instâncias preservam todos os registros no mesmo banco."""
    n_threads = 8
    per_thread = 50
    barrier = threading.Barrier(n_threads)

    def _worker(tid: int) -> None:
        # Instância própria por thread, todas no mesmo diretório persistente.
        store = JsonlStateStore(root=tmp_path)
        barrier.wait()  # largada simultânea maximiza a contenção
        for i in range(per_thread):
            store.append("events", {"id": f"t{tid}-{i}", "tid": tid, "i": i})

    threads = [threading.Thread(target=_worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = JsonlStateStore(root=tmp_path).list("events")
    # Nenhum registro perdido nem corrompido.
    assert len(records) == n_threads * per_thread
    ids = {r["id"] for r in records}
    expected = {f"t{tid}-{i}" for tid in range(n_threads) for i in range(per_thread)}
    assert ids == expected


def test_no_partial_line_on_disk(tmp_path: Path):
    """Cada append escreve exatamente uma linha JSON completa terminada em \\n."""
    store = JsonlStateStore(root=tmp_path)
    for i in range(20):
        store.append("c", {"id": str(i)})
    raw = (tmp_path / "c.jsonl").read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 20
    for ln in lines:
        assert isinstance(json.loads(ln), dict)  # toda linha é JSON válido


def test_sqlite_migrates_jsonl_and_keeps_audit(tmp_path: Path):
    (tmp_path / "runs.jsonl").write_text(
        json.dumps({"id": "legacy", "status": "queued", "updated_at": "1"}) + "\n",
        encoding="utf-8",
    )

    store = SqliteStateStore(tmp_path)
    assert store.latest("runs", "legacy")["status"] == "queued"  # type: ignore[index]

    store.upsert("runs", {"id": "legacy", "status": "running", "updated_at": "2"})
    assert store.latest("runs", "legacy")["status"] == "running"  # type: ignore[index]
    assert len((tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_sqlite_serializes_writes_from_separate_processes(tmp_path: Path):
    process_count = 2
    per_process = 30
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_sqlite_process_writer, args=(str(tmp_path), process_id, per_process))
        for process_id in range(process_count)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    records = SqliteStateStore(tmp_path).list("events")
    assert len(records) == process_count * per_process
    assert {record["id"] for record in records} == {
        f"p{process_id}-{index}"
        for process_id in range(process_count)
        for index in range(per_process)
    }
