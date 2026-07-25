"""Testes do EventBus: pub/sub, persistência, FileWatcher, WebhookHandler, singleton."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.event_bus import (
    Event,
    EventBus,
    FileWatcher,
    Subscription,
    WebhookHandler,
    get_event_bus,
    reset_event_bus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bus(tmp_path: Path, async_dispatch: bool = False) -> EventBus:
    return EventBus(db_path=tmp_path / "test_bus.db", async_dispatch=async_dispatch)


# ---------------------------------------------------------------------------
# TestEventDataclass
# ---------------------------------------------------------------------------

class TestEventDataclass:
    def test_id_auto_generated(self):
        evt = Event(topic="t", payload={})
        assert len(evt.id) > 0

    def test_id_is_unique(self):
        e1 = Event(topic="t", payload={})
        e2 = Event(topic="t", payload={})
        assert e1.id != e2.id

    def test_custom_id_preserved(self):
        evt = Event(topic="t", payload={}, id="abc123")
        assert evt.id == "abc123"

    def test_source_default(self):
        evt = Event(topic="t", payload={})
        assert evt.source == "internal"

    def test_ts_set(self):
        t0 = time.time()
        evt = Event(topic="t", payload={})
        assert evt.ts >= t0


# ---------------------------------------------------------------------------
# TestEventBusInit
# ---------------------------------------------------------------------------

class TestEventBusInit:
    def test_creates_db(self, tmp_path):
        db = tmp_path / "bus.db"
        bus = EventBus(db_path=db)
        assert db.exists()

    def test_creates_events_table(self, tmp_path):
        db = tmp_path / "bus.db"
        EventBus(db_path=db)
        con = sqlite3.connect(str(db))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        con.close()
        assert "events" in tables

    def test_no_persist_skips_db(self, tmp_path):
        db = tmp_path / "bus.db"
        EventBus(db_path=db, persist=False)
        assert not db.exists()

    def test_starts_with_no_subscribers(self, tmp_path):
        bus = _bus(tmp_path)
        assert bus.topics() == []


# ---------------------------------------------------------------------------
# TestSubscribeAndPublish
# ---------------------------------------------------------------------------

class TestSubscribeAndPublish:
    def test_subscribe_returns_sub_id(self, tmp_path):
        bus = _bus(tmp_path)
        sub_id = bus.subscribe("topic.a", lambda e: None)
        assert sub_id and len(sub_id) > 0

    def test_handler_called_on_publish(self, tmp_path):
        bus = _bus(tmp_path)
        received = []
        bus.subscribe("topic.a", lambda e: received.append(e))
        bus.publish("topic.a", {"x": 1})
        assert len(received) == 1
        assert received[0].topic == "topic.a"

    def test_handler_receives_payload(self, tmp_path):
        bus = _bus(tmp_path)
        received = []
        bus.subscribe("data", lambda e: received.append(e.payload))
        bus.publish("data", {"key": "value"})
        assert received[0]["key"] == "value"

    def test_multiple_subscribers_same_topic(self, tmp_path):
        bus = _bus(tmp_path)
        calls = []
        bus.subscribe("t", lambda e: calls.append(1))
        bus.subscribe("t", lambda e: calls.append(2))
        bus.publish("t", {})
        assert len(calls) == 2

    def test_no_handler_for_other_topic(self, tmp_path):
        bus = _bus(tmp_path)
        received = []
        bus.subscribe("a", lambda e: received.append(e))
        bus.publish("b", {})
        assert received == []

    def test_publish_returns_event(self, tmp_path):
        bus = _bus(tmp_path)
        evt = bus.publish("t", {"x": 1})
        assert isinstance(evt, Event)
        assert evt.topic == "t"

    def test_publish_without_subscribers_ok(self, tmp_path):
        bus = _bus(tmp_path)
        evt = bus.publish("orphan", {"x": 1})
        assert evt.topic == "orphan"

    def test_source_passed_to_event(self, tmp_path):
        bus = _bus(tmp_path)
        received = []
        bus.subscribe("t", lambda e: received.append(e.source))
        bus.publish("t", {}, source="webhook")
        assert received[0] == "webhook"

    def test_handler_exception_does_not_crash(self, tmp_path):
        bus = _bus(tmp_path)
        bus.subscribe("t", lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        # deve não levantar
        bus.publish("t", {})


# ---------------------------------------------------------------------------
# TestUnsubscribe
# ---------------------------------------------------------------------------

class TestUnsubscribe:
    def test_unsubscribe_removes_handler(self, tmp_path):
        bus = _bus(tmp_path)
        calls = []
        sub_id = bus.subscribe("t", lambda e: calls.append(1))
        bus.unsubscribe(sub_id)
        bus.publish("t", {})
        assert calls == []

    def test_unsubscribe_returns_true(self, tmp_path):
        bus = _bus(tmp_path)
        sub_id = bus.subscribe("t", lambda e: None)
        assert bus.unsubscribe(sub_id) is True

    def test_unsubscribe_unknown_returns_false(self, tmp_path):
        bus = _bus(tmp_path)
        assert bus.unsubscribe("nonexistent") is False

    def test_unsubscribe_only_target_handler(self, tmp_path):
        bus = _bus(tmp_path)
        calls = []
        sub1 = bus.subscribe("t", lambda e: calls.append(1))
        bus.subscribe("t", lambda e: calls.append(2))
        bus.unsubscribe(sub1)
        bus.publish("t", {})
        assert calls == [2]


# ---------------------------------------------------------------------------
# TestTopicsAndSubscriberCount
# ---------------------------------------------------------------------------

class TestTopicsAndSubscriberCount:
    def test_topics_empty_bus(self, tmp_path):
        bus = _bus(tmp_path)
        assert bus.topics() == []

    def test_topics_after_subscribe(self, tmp_path):
        bus = _bus(tmp_path)
        bus.subscribe("a", lambda e: None)
        bus.subscribe("b", lambda e: None)
        topics = sorted(bus.topics())
        assert topics == ["a", "b"]

    def test_topics_removed_after_unsubscribe(self, tmp_path):
        bus = _bus(tmp_path)
        sid = bus.subscribe("solo", lambda e: None)
        bus.unsubscribe(sid)
        assert "solo" not in bus.topics()

    def test_subscriber_count(self, tmp_path):
        bus = _bus(tmp_path)
        bus.subscribe("t", lambda e: None)
        bus.subscribe("t", lambda e: None)
        assert bus.subscriber_count("t") == 2

    def test_subscriber_count_missing_topic(self, tmp_path):
        bus = _bus(tmp_path)
        assert bus.subscriber_count("missing") == 0


# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_events_saved_to_db(self, tmp_path):
        bus = _bus(tmp_path)
        bus.publish("test.save", {"val": 42})
        history = bus.history()
        assert len(history) == 1
        assert history[0]["topic"] == "test.save"
        assert history[0]["payload"]["val"] == 42

    def test_history_respects_limit(self, tmp_path):
        bus = _bus(tmp_path)
        for i in range(10):
            bus.publish("t", {"i": i})
        history = bus.history(limit=5)
        assert len(history) == 5

    def test_history_filtered_by_topic(self, tmp_path):
        bus = _bus(tmp_path)
        bus.publish("a", {"x": 1})
        bus.publish("b", {"x": 2})
        bus.publish("a", {"x": 3})
        history_a = bus.history(topic="a")
        assert all(h["topic"] == "a" for h in history_a)
        assert len(history_a) == 2

    def test_no_persist_returns_empty_history(self, tmp_path):
        bus = EventBus(db_path=tmp_path / "x.db", persist=False)
        bus.publish("t", {})
        assert bus.history() == []

    def test_events_ordered_newest_first(self, tmp_path):
        bus = _bus(tmp_path)
        bus.publish("t", {"i": 1})
        time.sleep(0.01)
        bus.publish("t", {"i": 2})
        history = bus.history(topic="t")
        assert history[0]["payload"]["i"] == 2

    def test_duplicate_id_ignored(self, tmp_path):
        bus = _bus(tmp_path)
        # Inject two events with same id manually
        evt = bus.publish("t", {"x": 1})
        con = sqlite3.connect(str(tmp_path / "test_bus.db"))
        con.execute(
            "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
            (evt.id, "t", "internal", json.dumps({"x": 99}), time.time()),
        )
        con.commit()
        con.close()
        history = bus.history()
        assert len(history) == 1


# ---------------------------------------------------------------------------
# TestAsyncDispatch
# ---------------------------------------------------------------------------

class TestAsyncDispatch:
    def test_async_dispatch_calls_handler(self, tmp_path):
        bus = EventBus(db_path=tmp_path / "a.db", async_dispatch=True)
        received = []

        def handler(e: Event):
            received.append(e)

        bus.subscribe("t", handler)
        bus.publish("t", {})
        # Aguarda thread concluir
        time.sleep(0.1)
        assert len(received) == 1


# ---------------------------------------------------------------------------
# TestFileWatcher
# ---------------------------------------------------------------------------

class TestFileWatcher:
    def test_detects_file_change(self, tmp_path):
        bus = _bus(tmp_path)
        changes = []
        bus.subscribe("file.changed", lambda e: changes.append(e.payload))

        test_file = tmp_path / "watch.txt"
        test_file.write_text("original")

        watcher = FileWatcher(bus, [test_file], interval_sec=0.05)
        watcher.start()
        time.sleep(0.15)

        test_file.write_text("modified")
        time.sleep(0.2)
        watcher.stop()

        assert any(c["path"] == str(test_file) for c in changes)

    def test_idempotent_start(self, tmp_path):
        bus = _bus(tmp_path)
        watcher = FileWatcher(bus, [], interval_sec=1.0)
        watcher.start()
        t1 = watcher._thread
        watcher.start()
        t2 = watcher._thread
        assert t1 is t2
        watcher.stop()

    def test_stop_sets_flag(self, tmp_path):
        bus = _bus(tmp_path)
        watcher = FileWatcher(bus, [], interval_sec=1.0)
        watcher.start()
        watcher.stop()
        assert watcher._stop is True

    def test_missing_file_no_crash(self, tmp_path):
        bus = _bus(tmp_path)
        watcher = FileWatcher(bus, [tmp_path / "nonexistent.txt"], interval_sec=0.05)
        watcher.start()
        time.sleep(0.15)
        watcher.stop()

    def test_source_is_file_watcher(self, tmp_path):
        bus = _bus(tmp_path)
        sources = []
        bus.subscribe("file.changed", lambda e: sources.append(e.source))

        test_file = tmp_path / "w.txt"
        test_file.write_text("a")
        watcher = FileWatcher(bus, [test_file], interval_sec=0.05)
        watcher.start()

        # Duas coisas precisam ser deterministicas aqui, e cada uma ja causou
        # (ou quase causou) um flake:
        #
        # 1. A MUDANCA precisa ser visivel no mtime. No Windows o relogio de
        #    arquivo tem granularidade de ~15.6ms: medido neste repo, 131 de
        #    200 escritas consecutivas ficam com mtime IDENTICO. Confiar em
        #    "escrevi de novo, logo o mtime mudou" e um flake de ~65%. O utime
        #    explicito torna a mudanca inequivoca sem depender do relogio.
        # 2. A DETECCAO nao pode ter prazo cravado: num runner carregado a
        #    thread de polling pode nao ganhar CPU em 150ms. Espera-se ate a
        #    condicao, com teto generoso.
        test_file.write_text("b")
        novo_mtime = test_file.stat().st_mtime + 10
        os.utime(test_file, (novo_mtime, novo_mtime))

        deadline = time.monotonic() + 5.0
        while not sources and time.monotonic() < deadline:
            time.sleep(0.02)
        watcher.stop()

        assert any(s == "file_watcher" for s in sources)

    def test_start_arma_o_watcher_antes_de_retornar(self, tmp_path):
        """Regressao (flake de CI, Windows/3.12): o snapshot inicial de mtime
        ficava dentro de `_loop`, entao `start()` retornava com o watcher ainda
        desarmado. Se a thread demorasse a ganhar CPU, o baseline era colhido
        DEPOIS da escrita seguinte — o mtime gravado ja era o novo, nenhuma
        mudanca era detectada e o evento nunca saia, em silencio.

        Assertiva deterministica (nao depende de timing): quando `start()`
        retorna, o mtime do arquivo JA tem que estar registrado.
        """
        bus = _bus(tmp_path)
        test_file = tmp_path / "w.txt"
        test_file.write_text("a")
        mtime_antes = test_file.stat().st_mtime

        watcher = FileWatcher(bus, [test_file], interval_sec=60.0)  # nunca poll
        watcher.start()
        try:
            assert watcher._mtimes.get(test_file) == mtime_antes
        finally:
            watcher.stop()

    def test_snapshot_de_arquivo_inexistente_nao_quebra_start(self, tmp_path):
        """Path que nao existe (ou ilegivel) vira baseline 0.0 — a primeira
        aparicao do arquivo conta como mudanca, e start() nao levanta."""
        bus = _bus(tmp_path)
        ausente = tmp_path / "nao_existe.txt"
        watcher = FileWatcher(bus, [ausente], interval_sec=60.0)
        watcher.start()
        try:
            assert watcher._mtimes.get(ausente) == 0.0
        finally:
            watcher.stop()


# ---------------------------------------------------------------------------
# TestWebhookHandler
# ---------------------------------------------------------------------------

class TestWebhookHandler:
    def test_handle_request_publishes_event(self, tmp_path):
        bus = _bus(tmp_path)
        received = []
        bus.subscribe("webhook.test", lambda e: received.append(e))
        handler = WebhookHandler(bus)
        handler.handle_request("webhook.test", {"data": "x"})
        assert len(received) == 1
        assert received[0].source == "webhook"

    def test_handle_request_returns_event(self, tmp_path):
        bus = _bus(tmp_path)
        handler = WebhookHandler(bus)
        evt = handler.handle_request("t", {})
        assert isinstance(evt, Event)

    def test_webhook_payload_correct(self, tmp_path):
        bus = _bus(tmp_path)
        received = []
        bus.subscribe("t", lambda e: received.append(e.payload))
        handler = WebhookHandler(bus)
        handler.handle_request("t", {"key": "val"})
        assert received[0]["key"] == "val"


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_event_bus_returns_same_instance(self, tmp_path):
        reset_event_bus()
        b1 = get_event_bus(db_path=tmp_path / "singleton.db")
        b2 = get_event_bus()
        assert b1 is b2

    def test_reset_clears_singleton(self, tmp_path):
        reset_event_bus()
        b1 = get_event_bus(db_path=tmp_path / "s.db")
        reset_event_bus()
        b2 = get_event_bus(db_path=tmp_path / "s2.db")
        assert b1 is not b2

    def teardown_method(self, method):
        reset_event_bus()


# ---------------------------------------------------------------------------
# Higiene do SQLite (conexao persistente, WAL, indice em ts, retencao)
# ---------------------------------------------------------------------------

class TestSqliteHygiene:
    """O `events` era o unico store do projeto sem WAL, sem busy_timeout, sem
    indice em `ts` (apesar do ORDER BY ts DESC) e sem retencao — abrindo ainda
    uma conexao NOVA por evento no caminho quente do publish. O audit_trail.py
    ja fazia tudo isso certo."""

    def test_wal_e_busy_timeout_ativos(self, tmp_path):
        bus = _bus(tmp_path)
        try:
            modo = bus._conn.execute("PRAGMA journal_mode").fetchone()[0]
            busy = bus._conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert modo.lower() == "wal", f"journal_mode={modo}, escritores concorrentes travam"
            assert busy >= 5000, f"busy_timeout={busy}"
        finally:
            bus.close()

    def test_indice_em_ts_existe(self, tmp_path):
        """history() faz ORDER BY ts DESC — sem indice era full scan + sort."""
        bus = _bus(tmp_path)
        try:
            idx = {r[1] for r in bus._conn.execute("PRAGMA index_list(events)").fetchall()}
            assert "idx_events_ts" in idx
        finally:
            bus.close()

    def test_query_de_history_usa_o_indice(self, tmp_path):
        """Prova via EXPLAIN que o plano deixou de ser scan+sort."""
        bus = _bus(tmp_path)
        try:
            plano = " ".join(
                str(r) for r in bus._conn.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM events ORDER BY ts DESC LIMIT 50"
                ).fetchall()
            )
            assert "idx_events_ts" in plano, f"plano nao usa o indice: {plano}"
            assert "TEMP B-TREE" not in plano.upper(), f"ainda ordena em memoria: {plano}"
        finally:
            bus.close()

    def test_conexao_e_reaproveitada_entre_publishes(self, tmp_path):
        """Antes: connect+insert+commit+close a CADA evento publicado."""
        bus = _bus(tmp_path)
        try:
            conn_inicial = bus._conn
            for i in range(50):
                bus.publish("t", {"i": i})
            assert bus._conn is conn_inicial
            assert len(bus.history(limit=100)) == 50
        finally:
            bus.close()

    def test_retencao_por_idade(self, tmp_path):
        bus = EventBus(db_path=tmp_path / "r.db", retention_days=1)
        try:
            antigo = Event(topic="t", payload={}, ts=time.time() - 10 * 86400)
            recente = Event(topic="t", payload={}, ts=time.time())
            bus._save_event(antigo)
            bus._save_event(recente)
            assert len(bus.history(limit=10)) == 2
            bus.prune()
            restantes = bus.history(limit=10)
            assert len(restantes) == 1 and restantes[0]["id"] == recente.id
        finally:
            bus.close()

    def test_retencao_por_quantidade(self, tmp_path):
        """Sem teto, a tabela crescia para sempre."""
        bus = EventBus(db_path=tmp_path / "n.db", max_events=10, retention_days=0)
        try:
            for i in range(100):
                bus.publish("t", {"i": i})
            bus.prune()
            assert len(bus.history(limit=999)) == 10
        finally:
            bus.close()

    def test_escrita_concorrente_nao_perde_evento(self, tmp_path):
        """WAL + busy_timeout + lock: 8 threads publicando nao podem levantar
        `database is locked` nem perder registro."""
        import threading

        bus = _bus(tmp_path)
        erros = []

        def publica(n):
            try:
                for i in range(25):
                    bus.publish("carga", {"t": n, "i": i})
            except Exception as exc:  # noqa: BLE001
                erros.append(exc)

        try:
            threads = [threading.Thread(target=publica, args=(n,)) for n in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert not erros, f"escrita concorrente falhou: {erros[:3]}"
            assert len(bus.history(limit=500)) == 200
        finally:
            bus.close()

    def test_close_e_idempotente(self, tmp_path):
        bus = _bus(tmp_path)
        bus.close()
        bus.close()
        assert bus._conn is None

    def test_persist_false_nao_toca_disco(self, tmp_path):
        db = tmp_path / "nao_criar.db"
        bus = EventBus(db_path=db, persist=False)
        bus.publish("t", {"x": 1})
        assert not db.exists()
        assert bus.history() == []
