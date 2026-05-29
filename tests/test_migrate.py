"""Testes unitários para bauer/migrate.py."""

from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path

import pytest

from bauer.migrate import (
    HermesMigrator,
    MigrationResult,
    OpenClawMigrator,
    _merge_config,
)


# ── Fixtures de dados Hermes ──────────────────────────────────────────────────

HERMES_CONFIG_YAML = textwrap.dedent("""\
    model:
      api_key: ollama
      base_url: http://127.0.0.1:11434/v1
      default: llama3:8b
      provider: ollama-launch
    providers:
      ollama-launch:
        api: http://127.0.0.1:11434/v1
        default_model: llama3:8b
        models:
          - llama3:8b
          - qwen2.5:3b
        name: Ollama
    toolsets:
      - hermes-cli
      - web
""")

HERMES_HISTORY_JSON = json.dumps({
    "session-abc": [
        {"role": "user",      "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ],
    "session-xyz": [
        {"role": "human",  "content": "Explain DAGs"},
        {"role": "ai",     "content": "A DAG is..."},
    ],
})

OPENCLAW_SETTINGS_JSON = json.dumps({
    "version": 1,
    "gateway": {
        "url": "ws://localhost:18789",
        "token": "tok-main-123",
        "adapterType": "hermes",
        "profiles": {
            "hermes":    {"url": "ws://localhost:18789",          "token": ""},
            "openclaw":  {"url": "ws://hermes.example.com.br",    "token": "secret-oc-token"},
            "local":     {"url": "http://localhost:7770",         "token": ""},
        },
    },
    "activeFloorId": "hermes-first",
    "officeFloors": {
        "hermes-first": {
            "provider": "hermes",
            "status": "connected",
            "gatewayUrl": "ws://localhost:18789",
        },
    },
    "taskBoard": {
        "ws://localhost:18789": {
            "cards": [
                {"title": "Setup CI",        "status": "done"},
                {"title": "Write tests",     "status": "in_progress"},
                {"title": "Deploy staging",  "status": "todo"},
            ],
            "selectedCardId": None,
        }
    },
})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_hermes_dir(tmp: Path) -> Path:
    hd = tmp / ".hermes"
    hd.mkdir()
    (hd / "config.yaml").write_text(HERMES_CONFIG_YAML, encoding="utf-8")
    return hd


def _make_hermes_dir_with_history(tmp: Path) -> Path:
    hd = _make_hermes_dir(tmp)
    (hd / "clawd3d-history.json").write_text(HERMES_HISTORY_JSON, encoding="utf-8")
    return hd


def _make_openclaw_settings(tmp: Path) -> Path:
    sd = tmp / ".openclaw" / "claw3d"
    sd.mkdir(parents=True)
    settings = sd / "settings.json"
    settings.write_text(OPENCLAW_SETTINGS_JSON, encoding="utf-8")
    return settings


# ── Testes MigrationResult ────────────────────────────────────────────────────

class TestMigrationResult:
    def test_ok_with_no_errors(self):
        r = MigrationResult(source="test", dry_run=False)
        assert r.ok is True

    def test_not_ok_with_errors(self):
        r = MigrationResult(source="test", dry_run=False)
        r.error("something broke")
        assert r.ok is False

    def test_add_action(self):
        r = MigrationResult(source="test", dry_run=False)
        r.add("did X")
        assert "did X" in r.actions

    def test_warn(self):
        r = MigrationResult(source="test", dry_run=False)
        r.warn("watch out")
        assert "watch out" in r.warnings


# ── Testes _merge_config ──────────────────────────────────────────────────────

class TestMergeConfig:
    def test_creates_config_when_absent(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        changed = _merge_config(cfg, {"model": {"provider": "ollama"}}, dry_run=False)
        assert cfg.exists()
        assert any("model" in c for c in changed)

    def test_does_not_overwrite_existing_values(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  provider: openai\n", encoding="utf-8")
        changed = _merge_config(cfg, {"model": {"provider": "ollama"}}, dry_run=False)
        # provider já estava definido → não deve mudar
        assert changed == []
        import yaml
        data = yaml.safe_load(cfg.read_text())
        assert data["model"]["provider"] == "openai"

    def test_fills_empty_fields(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("model:\n  provider: ''\n", encoding="utf-8")
        changed = _merge_config(cfg, {"model": {"provider": "ollama"}}, dry_run=False)
        assert "model.provider" in changed

    def test_dry_run_does_not_write(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        _merge_config(cfg, {"model": {"provider": "ollama"}}, dry_run=True)
        assert not cfg.exists()


# ── Testes HermesMigrator ─────────────────────────────────────────────────────

class TestHermesMigratorDetect:
    def test_detect_true_when_config_exists(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        m = HermesMigrator(hermes_dir=hd, bauer_config=tmp_path / "config.yaml")
        assert m.detect() is True

    def test_detect_false_when_missing(self, tmp_path):
        m = HermesMigrator(hermes_dir=tmp_path / "nonexistent")
        assert m.detect() is False


class TestHermesMigratorSummary:
    def test_summary_with_valid_install(self, tmp_path):
        hd = _make_hermes_dir_with_history(tmp_path)
        m = HermesMigrator(hermes_dir=hd)
        s = m.source_summary()
        assert s["found"] is True
        assert s["provider"] == "ollama-launch"
        assert s["model"] == "llama3:8b"
        assert "hermes-cli" in s["toolsets"]
        assert s["session_count"] == 2
        assert s["total_messages"] == 4

    def test_summary_empty_history(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        (hd / "clawd3d-history.json").write_text("{}", encoding="utf-8")
        m = HermesMigrator(hermes_dir=hd)
        s = m.source_summary()
        assert s["session_count"] == 0


class TestHermesMigratorMigrateConfig:
    def test_maps_ollama_launch_to_ollama(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        cfg = tmp_path / "config.yaml"
        m = HermesMigrator(hermes_dir=hd, bauer_config=cfg)
        result = m.migrate(dry_run=False, import_history=False, import_agents=False)
        assert result.ok
        import yaml
        data = yaml.safe_load(cfg.read_text())
        assert data["model"]["provider"] == "ollama"
        assert data["model"]["name"] == "llama3:8b"
        # host sem /v1
        assert data["ollama"]["host"] == "http://127.0.0.1:11434"

    def test_dry_run_does_not_write_config(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        cfg = tmp_path / "config.yaml"
        result = HermesMigrator(hermes_dir=hd, bauer_config=cfg).migrate(
            dry_run=True, import_history=False, import_agents=False
        )
        assert result.ok
        assert not cfg.exists()

    def test_error_when_hermes_not_found(self, tmp_path):
        m = HermesMigrator(hermes_dir=tmp_path / "nope", bauer_config=tmp_path / "c.yaml")
        result = m.migrate(dry_run=False)
        assert not result.ok
        assert result.errors


class TestHermesMigratorMigrateHistory:
    def test_imports_history_sessions(self, tmp_path):
        hd = _make_hermes_dir_with_history(tmp_path)
        sessions_dir = tmp_path / "memory" / "sessions"
        m = HermesMigrator(
            hermes_dir=hd,
            bauer_config=tmp_path / "config.yaml",
            bauer_memory=tmp_path / "memory",
        )
        result = m.migrate(dry_run=False, import_config=False, import_agents=False)
        assert result.ok
        jsonl_files = list(sessions_dir.glob("hermes-*.jsonl"))
        assert len(jsonl_files) == 2
        # Verifica conteúdo do primeiro arquivo
        content = jsonl_files[0].read_text(encoding="utf-8")
        messages = [json.loads(l) for l in content.strip().splitlines()]
        assert all("role" in m and "content" in m for m in messages)

    def test_role_normalization(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        hist = {"sess": [
            {"role": "human",     "content": "hi"},
            {"role": "ai",        "content": "hello"},
            {"role": "assistant", "content": "bye"},
        ]}
        (hd / "clawd3d-history.json").write_text(json.dumps(hist), encoding="utf-8")
        sessions_dir = tmp_path / "memory" / "sessions"
        m = HermesMigrator(
            hermes_dir=hd,
            bauer_config=tmp_path / "config.yaml",
            bauer_memory=tmp_path / "memory",
        )
        m.migrate(dry_run=False, import_config=False, import_agents=False)
        jsonl = list(sessions_dir.glob("*.jsonl"))[0]
        roles = [json.loads(l)["role"] for l in jsonl.read_text().strip().splitlines()]
        assert roles == ["user", "assistant", "assistant"]

    def test_empty_history_adds_action(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        (hd / "clawd3d-history.json").write_text("{}", encoding="utf-8")
        m = HermesMigrator(hermes_dir=hd, bauer_config=tmp_path / "c.yaml",
                            bauer_memory=tmp_path / "mem")
        result = m.migrate(dry_run=False, import_config=False, import_agents=False)
        assert result.ok
        assert any("vazio" in a.lower() for a in result.actions)

    def test_missing_history_file_warns(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        # sem clawd3d-history.json
        m = HermesMigrator(hermes_dir=hd, bauer_config=tmp_path / "c.yaml",
                            bauer_memory=tmp_path / "mem")
        result = m.migrate(dry_run=False, import_config=False, import_agents=False)
        assert result.ok
        assert any("histórico" in w.lower() or "history" in w.lower() for w in result.warnings)


class TestHermesMigratorAgents:
    def test_creates_hermes_default_agent(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        agents_file = tmp_path / "agents.yaml"
        m = HermesMigrator(
            hermes_dir=hd,
            bauer_config=tmp_path / "c.yaml",
            bauer_agents=agents_file,
        )
        result = m.migrate(dry_run=False, import_config=False, import_history=False)
        assert result.ok
        assert agents_file.exists()
        from bauer.agent_registry import AgentRegistry
        reg = AgentRegistry(agents_file)
        ag = reg.get("hermes-default")
        assert ag is not None
        # tools derivadas de hermes-cli + web
        assert "read_file" in ag.tools
        assert "web_search" in ag.tools

    def test_does_not_duplicate_agent(self, tmp_path):
        hd = _make_hermes_dir(tmp_path)
        agents_file = tmp_path / "agents.yaml"
        m = HermesMigrator(hermes_dir=hd, bauer_config=tmp_path / "c.yaml",
                            bauer_agents=agents_file)
        m.migrate(dry_run=False, import_config=False, import_history=False)
        result2 = m.migrate(dry_run=False, import_config=False, import_history=False)
        assert result2.ok
        # segundo run deve avisar que já existe
        assert any("hermes-default" in w and ("ja existe" in w or "já existe" in w) for w in result2.warnings)


# ── Testes OpenClawMigrator ───────────────────────────────────────────────────

class TestOpenClawMigratorDetect:
    def test_detect_true_when_settings_exist(self, tmp_path):
        sp = _make_openclaw_settings(tmp_path)
        m = OpenClawMigrator(settings_path=sp)
        assert m.detect() is True

    def test_detect_false_when_missing(self, tmp_path):
        m = OpenClawMigrator(settings_path=tmp_path / "nope.json")
        assert m.detect() is False


class TestOpenClawMigratorSummary:
    def test_summary_with_valid_settings(self, tmp_path):
        sp = _make_openclaw_settings(tmp_path)
        m = OpenClawMigrator(settings_path=sp)
        s = m.source_summary()
        assert s["found"] is True
        assert s["profile_count"] == 3
        assert "openclaw" in s["profiles"]
        assert s["task_card_count"] == 3
        assert s["active_adapter"] == "hermes"


class TestOpenClawMigratorConfig:
    def test_maps_hermes_floor_to_ollama(self, tmp_path):
        sp = _make_openclaw_settings(tmp_path)
        cfg = tmp_path / "config.yaml"
        m = OpenClawMigrator(settings_path=sp, bauer_config=cfg)
        result = m.migrate(dry_run=False, import_auth=False, import_tasks=False)
        assert result.ok
        import yaml
        data = yaml.safe_load(cfg.read_text())
        assert data["model"]["provider"] == "ollama"

    def test_dry_run_no_write(self, tmp_path):
        sp = _make_openclaw_settings(tmp_path)
        cfg = tmp_path / "config.yaml"
        m = OpenClawMigrator(settings_path=sp, bauer_config=cfg)
        result = m.migrate(dry_run=True, import_auth=False, import_tasks=False)
        assert result.ok
        assert not cfg.exists()


class TestOpenClawMigratorTasks:
    def test_imports_task_cards(self, tmp_path):
        sp = _make_openclaw_settings(tmp_path)
        ws = tmp_path / "workspace"
        m = OpenClawMigrator(settings_path=sp, bauer_config=tmp_path / "c.yaml",
                              bauer_workspace=ws)
        result = m.migrate(dry_run=False, import_config=False, import_auth=False)
        assert result.ok
        from bauer.workspace_manager import WorkspaceManager
        wm = WorkspaceManager(ws)
        tasks = wm.list_tasks()
        assert len(tasks) == 3
        titles = [t.title for t in tasks]
        assert "Setup CI" in titles
        assert "Write tests" in titles
        assert "Deploy staging" in titles

    def test_task_status_mapping(self, tmp_path):
        sp = _make_openclaw_settings(tmp_path)
        ws = tmp_path / "workspace"
        m = OpenClawMigrator(settings_path=sp, bauer_config=tmp_path / "c.yaml",
                              bauer_workspace=ws)
        m.migrate(dry_run=False, import_config=False, import_auth=False)
        from bauer.workspace_manager import WorkspaceManager
        wm = WorkspaceManager(ws)
        tasks = {t.title: t.status for t in wm.list_tasks()}
        assert tasks["Setup CI"] == "DONE"
        assert tasks["Write tests"] == "IN_PROGRESS"
        assert tasks["Deploy staging"] == "TODO"

    def test_dry_run_no_tasks_written(self, tmp_path):
        sp = _make_openclaw_settings(tmp_path)
        ws = tmp_path / "workspace"
        m = OpenClawMigrator(settings_path=sp, bauer_config=tmp_path / "c.yaml",
                              bauer_workspace=ws)
        result = m.migrate(dry_run=True, import_config=False, import_auth=False)
        assert result.ok
        tasks_file = ws / "TASKS.md"
        assert not tasks_file.exists()

    def test_empty_task_board_action(self, tmp_path):
        settings = {"version": 1, "gateway": {"profiles": {}}, "taskBoard": {}}
        sp = tmp_path / "settings.json"
        sp.write_text(json.dumps(settings), encoding="utf-8")
        ws = tmp_path / "workspace"
        m = OpenClawMigrator(settings_path=sp, bauer_config=tmp_path / "c.yaml",
                              bauer_workspace=ws)
        result = m.migrate(dry_run=False, import_config=False, import_auth=False)
        assert result.ok
        assert any("vazio" in a.lower() or "empty" in a.lower() or "sem tasks" in a.lower()
                   for a in result.actions)


class TestOpenClawMigratorError:
    def test_error_when_not_found(self, tmp_path):
        m = OpenClawMigrator(settings_path=tmp_path / "nope.json")
        result = m.migrate(dry_run=False)
        assert not result.ok
        assert result.errors

    def test_error_on_invalid_json(self, tmp_path):
        sp = tmp_path / "bad.json"
        sp.write_text("{ not valid json }", encoding="utf-8")
        m = OpenClawMigrator(settings_path=sp)
        result = m.migrate(dry_run=False)
        assert not result.ok
