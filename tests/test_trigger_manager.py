"""Tests for bauer/trigger_manager.py."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from bauer.trigger_manager import (
    BaseTrigger,
    CronTrigger,
    FilesystemTrigger,
    GitTrigger,
    TriggerEvent,
    TriggerManager,
    WebhookTrigger,
)


# ===========================================================================
# TriggerEvent
# ===========================================================================


class TestTriggerEvent:
    def test_defaults(self):
        ev = TriggerEvent(trigger_id="t1", trigger_type="cron", reason="tick")
        assert ev.payload == {}
        assert ev.timestamp > 0

    def test_payload(self):
        ev = TriggerEvent(trigger_id="t1", trigger_type="fs", reason="r",
                          payload={"path": "/foo"})
        assert ev.payload["path"] == "/foo"


# ===========================================================================
# BaseTrigger (via CronTrigger)
# ===========================================================================


class TestBaseTrigger:
    def test_initial_state(self):
        t = CronTrigger("c1", interval_seconds=60)
        assert t.trigger_id == "c1"
        assert t.enabled is True
        assert t.fire_count == 0
        assert t.last_fired is None

    def test_stats(self):
        t = CronTrigger("c1", interval_seconds=60)
        s = t.stats()
        assert s["trigger_id"] == "c1"
        assert s["trigger_type"] == "CronTrigger"
        assert s["fire_count"] == 0

    def test_disabled(self):
        t = CronTrigger("c1", interval_seconds=0.01, enabled=False)
        assert not t.enabled


# ===========================================================================
# CronTrigger
# ===========================================================================


class TestCronTrigger:
    @pytest.mark.asyncio
    async def test_fires_after_interval(self):
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = CronTrigger("cron1", interval_seconds=0.05, max_fires=1)
        shutdown = asyncio.Event()

        await asyncio.wait_for(t.run(cb, shutdown), timeout=2.0)

        assert len(events) == 1
        assert events[0].trigger_id == "cron1"
        assert events[0].trigger_type == "cron"

    @pytest.mark.asyncio
    async def test_fire_on_start(self):
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = CronTrigger("cron2", interval_seconds=10.0, fire_on_start=True, max_fires=1)
        shutdown = asyncio.Event()

        await asyncio.wait_for(t.run(cb, shutdown), timeout=1.0)

        assert len(events) == 1
        assert events[0].reason == "cron_start"

    @pytest.mark.asyncio
    async def test_fires_multiple_times(self):
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = CronTrigger("cron3", interval_seconds=0.03, max_fires=3)
        shutdown = asyncio.Event()

        await asyncio.wait_for(t.run(cb, shutdown), timeout=3.0)

        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_shutdown_stops_trigger(self):
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = CronTrigger("cron4", interval_seconds=10.0)
        shutdown = asyncio.Event()

        async def stop_after():
            await asyncio.sleep(0.05)
            shutdown.set()

        await asyncio.gather(
            t.run(cb, shutdown),
            stop_after(),
        )
        assert len(events) == 0  # never fired — shutdown before interval

    @pytest.mark.asyncio
    async def test_disabled_does_not_fire(self):
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = CronTrigger("cron5", interval_seconds=0.01, enabled=False)
        shutdown = asyncio.Event()
        shutdown.set()  # already set — but disabled should return before loop

        await t.run(cb, shutdown)
        assert events == []

    @pytest.mark.asyncio
    async def test_fire_count_increments(self):
        async def cb(ev):
            pass

        t = CronTrigger("cron6", interval_seconds=0.03, max_fires=2)
        shutdown = asyncio.Event()
        await asyncio.wait_for(t.run(cb, shutdown), timeout=2.0)
        assert t.fire_count == 2
        assert t.last_fired is not None


# ===========================================================================
# FilesystemTrigger
# ===========================================================================


class TestFilesystemTrigger:
    @pytest.mark.asyncio
    async def test_fires_on_file_creation(self, tmp_path):
        target = tmp_path / "watch.txt"
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = FilesystemTrigger("fs1", target, poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def create_file():
            await asyncio.sleep(0.08)
            target.write_text("hello")
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), create_file())

        created_events = [e for e in events if e.payload.get("event") == "created"]
        assert len(created_events) >= 1

    @pytest.mark.asyncio
    async def test_fires_on_file_modification(self, tmp_path):
        target = tmp_path / "watch.txt"
        target.write_text("initial")
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = FilesystemTrigger("fs2", target, events={"modified"},
                               poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def modify_file():
            await asyncio.sleep(0.08)
            target.write_text("changed content")
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), modify_file())

        modified_events = [e for e in events if e.payload.get("event") == "modified"]
        assert len(modified_events) >= 1

    @pytest.mark.asyncio
    async def test_fires_on_file_deletion(self, tmp_path):
        target = tmp_path / "watch.txt"
        target.write_text("exists")
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = FilesystemTrigger("fs3", target, events={"deleted"},
                               poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def delete_file():
            await asyncio.sleep(0.08)
            target.unlink()
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), delete_file())

        deleted_events = [e for e in events if e.payload.get("event") == "deleted"]
        assert len(deleted_events) >= 1

    @pytest.mark.asyncio
    async def test_event_filter_respected(self, tmp_path):
        """Only watch 'deleted' — creation should not fire."""
        target = tmp_path / "watch.txt"
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = FilesystemTrigger("fs4", target, events={"deleted"},
                               poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def create_and_stop():
            await asyncio.sleep(0.08)
            target.write_text("new file")
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), create_and_stop())
        # Created event should NOT have fired
        assert all(e.payload.get("event") != "created" for e in events)

    @pytest.mark.asyncio
    async def test_payload_contains_path(self, tmp_path):
        target = tmp_path / "w.txt"
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = FilesystemTrigger("fs5", target, poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def create_and_stop():
            await asyncio.sleep(0.08)
            target.write_text("x")
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), create_and_stop())
        assert any("path" in e.payload for e in events)


# ===========================================================================
# GitTrigger
# ===========================================================================


class TestGitTrigger:
    def _make_fake_repo(self, tmp_path: Path, commit: str) -> Path:
        git = tmp_path / ".git"
        git.mkdir()
        (git / "refs" / "heads").mkdir(parents=True)
        (git / "refs" / "heads" / "master").write_text(commit + "\n")
        return tmp_path

    @pytest.mark.asyncio
    async def test_fires_on_new_commit(self, tmp_path):
        ref_file = tmp_path / ".git" / "refs" / "heads" / "master"
        self._make_fake_repo(tmp_path, "aaa111")
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = GitTrigger("git1", repo_path=tmp_path, branch="master",
                       poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def new_commit():
            await asyncio.sleep(0.1)
            ref_file.write_text("bbb222\n")
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), new_commit())

        assert len(events) >= 1
        assert events[0].payload["new_commit"] == "bbb222"
        assert events[0].payload["old_commit"] == "aaa111"

    @pytest.mark.asyncio
    async def test_no_fire_without_change(self, tmp_path):
        self._make_fake_repo(tmp_path, "abc123")
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = GitTrigger("git2", repo_path=tmp_path, branch="master",
                       poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def just_stop():
            await asyncio.sleep(0.2)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), just_stop())
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_payload_contains_branch(self, tmp_path):
        ref_file = tmp_path / ".git" / "refs" / "heads" / "master"
        self._make_fake_repo(tmp_path, "aaa000")
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = GitTrigger("git3", repo_path=tmp_path, branch="master",
                       poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def commit():
            await asyncio.sleep(0.1)
            ref_file.write_text("bbb999\n")
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), commit())
        assert events[0].payload["branch"] == "master"

    @pytest.mark.asyncio
    async def test_missing_repo_does_not_crash(self, tmp_path):
        """No .git directory — trigger should run but never fire."""
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = GitTrigger("git4", repo_path=tmp_path / "nope", poll_interval_seconds=0.05)
        shutdown = asyncio.Event()

        async def stop():
            await asyncio.sleep(0.15)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), stop())
        assert len(events) == 0


# ===========================================================================
# TriggerManager
# ===========================================================================


class TestTriggerManager:
    @pytest.mark.asyncio
    async def test_add_and_list(self):
        mgr = TriggerManager()
        t1 = CronTrigger("t1", interval_seconds=60)
        t2 = CronTrigger("t2", interval_seconds=120)
        mgr.add(t1)
        mgr.add(t2)
        triggers = mgr.list_triggers()
        assert len(triggers) == 2

    @pytest.mark.asyncio
    async def test_add_duplicate_raises(self):
        mgr = TriggerManager()
        mgr.add(CronTrigger("t1", interval_seconds=60))
        with pytest.raises(ValueError, match="already registered"):
            mgr.add(CronTrigger("t1", interval_seconds=30))

    def test_remove(self):
        mgr = TriggerManager()
        mgr.add(CronTrigger("t1", interval_seconds=60))
        ok = mgr.remove("t1")
        assert ok is True
        assert mgr.get("t1") is None

    def test_remove_nonexistent(self):
        mgr = TriggerManager()
        assert mgr.remove("nope") is False

    def test_get(self):
        mgr = TriggerManager()
        t = CronTrigger("t1", interval_seconds=60)
        mgr.add(t)
        assert mgr.get("t1") is t

    @pytest.mark.asyncio
    async def test_start_runs_triggers(self):
        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        mgr = TriggerManager()
        mgr.add(CronTrigger("c1", interval_seconds=0.05, fire_on_start=True, max_fires=1))

        await mgr.start(cb)
        await asyncio.sleep(0.2)
        await mgr.stop()

        assert any(e.trigger_id == "c1" for e in events)

    @pytest.mark.asyncio
    async def test_start_twice_raises(self):
        mgr = TriggerManager()
        mgr.add(CronTrigger("c1", interval_seconds=100))
        await mgr.start(lambda ev: None)
        with pytest.raises(RuntimeError, match="already running"):
            await mgr.start(lambda ev: None)
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self):
        mgr = TriggerManager()
        mgr.add(CronTrigger("c1", interval_seconds=100))
        await mgr.start(lambda ev: None)
        assert mgr.is_running is True
        await mgr.stop()
        assert mgr.is_running is False

    def test_stats_structure(self):
        mgr = TriggerManager()
        mgr.add(CronTrigger("c1", interval_seconds=60))
        s = mgr.stats()
        assert s["running"] is False
        assert s["trigger_count"] == 1
        assert len(s["triggers"]) == 1

    @pytest.mark.asyncio
    async def test_multiple_triggers_fire_independently(self):
        fired = []

        async def cb(ev: TriggerEvent):
            fired.append(ev.trigger_id)

        mgr = TriggerManager()
        mgr.add(CronTrigger("fast", interval_seconds=0.03, max_fires=2))
        mgr.add(CronTrigger("slow", interval_seconds=0.1, max_fires=1))

        await mgr.start(cb)
        await asyncio.sleep(0.5)
        await mgr.stop()

        assert fired.count("fast") >= 2
        assert fired.count("slow") >= 1


# ===========================================================================
# WebhookTrigger (light test — just that it listens and handles)
# ===========================================================================


class TestWebhookTrigger:
    @pytest.mark.asyncio
    async def test_fires_on_post(self):
        import socket

        # Find a free port
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = WebhookTrigger("wh1", port=port, path="/trigger")
        shutdown = asyncio.Event()

        async def send_request():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            body = b'{"hello": "world"}'
            request = (
                f"POST /trigger HTTP/1.1\r\n"
                f"Host: localhost\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"Content-Type: application/json\r\n"
                f"\r\n"
            ).encode() + body
            writer.write(request)
            await writer.drain()
            await reader.read(100)  # read response
            writer.close()
            await asyncio.sleep(0.1)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), send_request())
        assert len(events) == 1
        assert events[0].payload.get("hello") == "world"

    @pytest.mark.asyncio
    async def test_rejects_wrong_path(self):
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = WebhookTrigger("wh2", port=port, path="/specific")
        shutdown = asyncio.Event()

        async def send_wrong_path():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"POST /wrong HTTP/1.1\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            response = await reader.read(100)
            writer.close()
            assert b"404" in response
            await asyncio.sleep(0.1)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), send_wrong_path())
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_secret_validation(self):
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        events = []

        async def cb(ev: TriggerEvent):
            events.append(ev)

        t = WebhookTrigger("wh3", port=port, path="/trigger", secret="mysecret")
        shutdown = asyncio.Event()

        async def send_bad_secret():
            await asyncio.sleep(0.1)
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                b"POST /trigger HTTP/1.1\r\n"
                b"X-Trigger-Secret: wrongsecret\r\n"
                b"Content-Length: 0\r\n\r\n"
            )
            await writer.drain()
            response = await reader.read(100)
            writer.close()
            assert b"401" in response
            await asyncio.sleep(0.1)
            shutdown.set()

        await asyncio.gather(t.run(cb, shutdown), send_bad_secret())
        assert len(events) == 0
