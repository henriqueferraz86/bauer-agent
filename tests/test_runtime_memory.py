from __future__ import annotations

from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.events import EventBus
from bauer.core.runtime.memory import RuntimeMemoryManager


def test_runtime_memory_write_has_source_and_event(tmp_path):
    manager = RuntimeMemoryManager(root=tmp_path)

    record = manager.write(
        scope="project",
        content="The runtime adapter default is agno.",
        source="agents.yaml",
        confidence=0.8,
        run_id="run-1",
    )

    assert record.source == "agents.yaml"
    assert record.confidence == 0.8

    events = EventBus(root=tmp_path).list_events(run_id="run-1")
    assert [event.event_type for event in events] == ["memory.written"]
    assert events[0].data["memory_id"] == record.id
    assert events[0].data["source"] == "agents.yaml"


def test_runtime_memory_search_filters_expired_records(tmp_path):
    manager = RuntimeMemoryManager(root=tmp_path)
    manager.write(
        scope="agent",
        content="Code agent prefers small patches.",
        source="operator",
        confidence=0.9,
    )
    manager.write(
        scope="agent",
        content="Code agent should use the old runtime.",
        source="operator",
        confidence=0.4,
        valid_until=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    )

    active_hits = manager.search("code agent", scope="agent")
    all_hits = manager.search("code agent", scope="agent", include_expired=True)

    assert len(active_hits) == 1
    assert active_hits[0].content == "Code agent prefers small patches."
    assert len(all_hits) == 2


def test_runtime_memory_can_be_revised_and_expired(tmp_path):
    manager = RuntimeMemoryManager(root=tmp_path)
    record = manager.write(
        scope="user",
        content="Prefer native runtime.",
        source="chat",
        confidence=0.5,
    )

    revised = manager.revise(
        record.id,
        content="Prefer Agno runtime for integration tests.",
        confidence=0.95,
        run_id="run-2",
    )
    assert revised.id == record.id
    assert revised.created_at == record.created_at
    assert revised.updated_at >= record.updated_at
    assert revised.content == "Prefer Agno runtime for integration tests."
    assert revised.confidence == 0.95

    expired = manager.expire(record.id, reason="superseded", run_id="run-2")
    assert manager.is_expired(expired)
    assert manager.get(record.id).valid_until == expired.valid_until
    assert manager.search("agno runtime") == []
    assert manager.search("agno runtime", include_expired=True)[0].id == record.id

    event_types = [event.event_type for event in EventBus(root=tmp_path).list_events(run_id="run-2")]
    assert event_types == ["memory.revised", "memory.expired"]


def test_runtime_memory_validates_scope_confidence_and_content(tmp_path):
    manager = RuntimeMemoryManager(root=tmp_path)

    for kwargs in (
        {"scope": "global", "content": "x", "source": "test"},
        {"scope": "user", "content": "", "source": "test"},
        {"scope": "user", "content": "x", "source": ""},
        {"scope": "user", "content": "x", "source": "test", "confidence": 1.1},
    ):
        try:
            manager.write(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")


def test_runtime_memory_cli_add_search_revise_expire(tmp_path):
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "memory",
            "runtime-add",
            "project",
            "Scheduler runs daily review.",
            "--source",
            "schedule.yaml",
            "--runtime-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    memory_id = result.output.strip().split()[-1]

    result = runner.invoke(
        app,
        ["memory", "runtime-search", "daily", "--runtime-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    assert "Busca runtime: daily" in result.output
    assert RuntimeMemoryManager(root=tmp_path).search("daily")[0].source == "schedule.yaml"

    result = runner.invoke(
        app,
        [
            "memory",
            "runtime-revise",
            memory_id,
            "--content",
            "Scheduler runs weekly review.",
            "--runtime-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    result = runner.invoke(
        app,
        ["memory", "runtime-expire", memory_id, "--reason", "outdated", "--runtime-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output

    event_types = [event.event_type for event in EventBus(root=tmp_path).list_events()]
    assert event_types == ["memory.written", "memory.revised", "memory.expired"]
