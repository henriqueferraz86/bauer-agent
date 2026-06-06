"""Tests for level-8 gap closure foundations."""

from __future__ import annotations

import json
from pathlib import Path

from bauer.automation_scheduler import AutomationScheduler
from bauer.automation_store import AutomationStore
from bauer.gateway_outbox import GatewayOutbox
from bauer.memory_index import MemoryIndex
from bauer.plugin_registry import PluginRegistry
from bauer.schema_migrations import MigrationLedger, ensure_level8_migrations
from bauer.secret_policy import SecretPolicy
from bauer.skill_registry import SkillRegistry
from bauer.trajectory_store import TrajectoryStore
from bauer.workspace_manager import WorkspaceManager


def test_secret_policy_redacts_values_and_filters_worker_env():
    policy = SecretPolicy()

    text = policy.sanitize_text("token sk-test12345678901234567890 here")
    env = policy.safe_worker_env(
        {"PATH": "x", "OPENAI_API_KEY": "sk-secret", "CUSTOM": "no"},
        extra={"BAUER_KANBAN_TASK": "001", "PASSWORD": "bad"},
    )

    assert "sk-test" not in text
    assert env["PATH"] == "x"
    assert env["BAUER_KANBAN_TASK"] == "001"
    assert "OPENAI_API_KEY" not in env
    assert "PASSWORD" not in env
    assert "CUSTOM" not in env


def test_gateway_outbox_delivers_file_and_redacts_payload(tmp_path: Path):
    outbox = GatewayOutbox(tmp_path)
    outbox.enqueue(
        channel="file",
        target="deliveries/out.jsonl",
        payload={"message": "hello", "api_key": "sk-test12345678901234567890"},
    )

    result = outbox.deliver_once()

    delivered = tmp_path / "deliveries" / "out.jsonl"
    data = json.loads(delivered.read_text(encoding="utf-8").splitlines()[0])
    assert result.delivered
    assert data["payload"]["message"] == "hello"
    assert data["payload"]["api_key"] == "[REDACTED]"
    assert outbox.list_messages()[0].status == "delivered"


def test_automation_delivery_creates_outbox_message(tmp_path: Path):
    workspace = tmp_path / "workspace"
    WorkspaceManager(workspace).init_project("Delivery")
    store = AutomationStore(workspace)
    store.create_job(
        name="notify",
        prompt="Notify me",
        schedule="every 1h",
        next_run_at="2026-06-01T09:00:00+00:00",
        metadata={"delivery": "file:notifications.jsonl"},
    )

    AutomationScheduler(workspace).tick(now="2026-06-01T09:00:00+00:00")

    messages = GatewayOutbox(workspace).list_messages()
    assert len(messages) == 1
    assert messages[0].payload["type"] == "automation.queued"
    assert messages[0].payload["job_name"] == "notify"


def test_memory_index_rebuild_and_search(tmp_path: Path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "MEMORY.md").write_text(
        "# MEMORY\n\n## [2026-06-01] Dispatcher\n\nCrash recovery and durable claims.\n",
        encoding="utf-8",
    )

    index = MemoryIndex(memory)
    count = index.rebuild()
    hits = index.search("durable claims")

    assert count == 1
    assert hits
    assert hits[0].file == "MEMORY.md"


def test_schema_migration_ledger_is_idempotent(tmp_path: Path):
    records = ensure_level8_migrations(tmp_path)
    again = ensure_level8_migrations(tmp_path)
    ledger = MigrationLedger(tmp_path)

    assert len(records) >= 6
    assert len(again) == len(records)
    assert len(ledger.list_records()) == len(records)


def test_trajectory_store_appends_sanitized_records(tmp_path: Path):
    store = TrajectoryStore(tmp_path)
    record = store.append(
        kind="debug",
        objective="Fix bug",
        input={"token": "sk-test12345678901234567890"},
        output={"summary": "ok"},
    )

    listed = store.list()
    assert record.trajectory_id == listed[0].trajectory_id
    assert listed[0].input["token"] == "[REDACTED]"


def test_plugin_registry_discovers_hooks_without_importing(tmp_path: Path):
    plugin_dir = tmp_path / "workspace" / ".bauer" / "plugins"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "demo.py").write_text(
        '"""Demo plugin."""\nfrom bauer.plugin_hooks import hooks\n@hooks.on("pre_tool_call")\ndef h(**kwargs): pass\n',
        encoding="utf-8",
    )

    plugins = PluginRegistry(tmp_path / "workspace", user_dir=tmp_path / "none").list_plugins()

    assert plugins[0].name == "demo"
    assert plugins[0].hooks == ["pre_tool_call"]
    assert plugins[0].description == "Demo plugin."


def test_skill_registry_can_approve_pending_suggestion(tmp_path: Path):
    memory = tmp_path / "memory"
    memory.mkdir()
    (memory / "SKILLS_LEARNED.md").write_text(
        "## [2026-06-01] sugestão: debug_code\n\n- ocorrencias: 3\n- status: pendente_aprovacao\n",
        encoding="utf-8",
    )

    path = SkillRegistry(memory).approve_suggestion(
        "debug_code",
        workspace=tmp_path / "workspace",
        description="Debug code",
        content="Steps",
    )

    skills = json.loads(path.read_text(encoding="utf-8"))
    assert skills["debug_code"]["description"] == "Debug code"
