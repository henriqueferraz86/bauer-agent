"""Testes do JsonlStateStore — incluindo integridade sob concorrência (#08)."""

from __future__ import annotations

import threading
from pathlib import Path

from bauer.core.runtime.state_store import JsonlStateStore


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
    """#08: N threads appendando no MESMO arquivo, via instâncias distintas de
    JsonlStateStore apontando pro mesmo root. Sem o lock por-arquivo, writes se
    intercalavam (linha corrompida → JSONDecodeError → registro PERDIDO). Com o
    lock, TODOS os N*M registros são legíveis e válidos."""
    n_threads = 8
    per_thread = 50
    barrier = threading.Barrier(n_threads)

    def _worker(tid: int) -> None:
        # instância PRÓPRIA por thread (mesmo root) — testa o lock por-arquivo,
        # não um lock por-instância.
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
    import json
    for ln in lines:
        assert isinstance(json.loads(ln), dict)  # toda linha é JSON válido
