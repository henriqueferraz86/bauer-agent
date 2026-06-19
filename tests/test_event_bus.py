"""Testes do EventBus: pub/sub, persistência, FileWatcher, WebhookHandler, singleton."""

from __future__ import annotations

import json
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
        time.sleep(0.15)
        test_file.write_text("b")
        time.sleep(0.15)
        watcher.stop()

        assert any(s == "file_watcher" for s in sources)


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
