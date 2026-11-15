from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
import yaml


def _load_setup():
    path = Path(__file__).parents[1] / "deploy" / "tool-gateway-telegram-canary-setup.py"
    spec = importlib.util.spec_from_file_location("tool_gateway_telegram_canary_setup", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_backup():
    path = Path(__file__).parents[1] / "deploy" / "backup_sqlite_volume.py"
    spec = importlib.util.spec_from_file_location("backup_sqlite_volume", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepare(module, root: Path, monkeypatch, *, users=None, allow_all=False):
    module.ROOT = root / "prep"
    module.WORKSPACE = root / "workspace" / "telegram-tool-gateway-canary"
    module.MEMORY = root / "memory"
    module.AGENTOS_MEMORY = root / "agentos-memory"
    module.ROOT.mkdir(parents=True)
    (module.ROOT / "operator.json").write_text(json.dumps({
        "telegram_token": "fake-telegram-token",
        "allowed_users": users if users is not None else [12345],
        "allow_all": allow_all,
        "openrouter_api_key": "openrouter-secret-$literal",
        "model": "deepseek/deepseek-chat",
    }), encoding="utf-8")
    monkeypatch.setenv("BAUER_HOME", str(module.MEMORY / "bauer-home"))
    module.main()


def test_setup_creates_isolated_read_only_canary_and_separates_credentials(tmp_path, monkeypatch):
    module = _load_setup()
    _prepare(module, tmp_path, monkeypatch)

    config = yaml.safe_load((module.ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert config["telegram"]["enabled"] is True
    assert config["telegram"]["allow_all"] is False
    assert config["telegram"]["allowed_users"] == [12345]
    adapter = config["runtime"]["adapters"]["agentos_http"]
    assert adapter["enabled"] is True
    assert adapter["experimental_read_only"] is True
    assert adapter["external_read_tools"] is True
    assert adapter["agent_id"] == "bauer.telegram.canary"

    bauer_env = (module.ROOT / ".env.bauer-canary").read_text(encoding="utf-8")
    agentos_env = (module.ROOT / ".env.agentos-canary").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN='fake-telegram-token'" in bauer_env
    assert "TELEGRAM_BOT_TOKEN" not in agentos_env
    assert "BAUER_TOOL_GATEWAY_SERVICE_KEY" in bauer_env
    assert "BAUER_TOOL_GATEWAY_SERVICE_KEY" not in agentos_env
    assert "OPENROUTER_API_KEY='openrouter-secret-$literal'" in agentos_env
    assert "OPENROUTER_API_KEY" not in bauer_env

    marker = module.WORKSPACE / "gateway-canary-marker.txt"
    assert marker.read_text(encoding="utf-8").strip() == module.MARKER
    registry = json.loads((module.MEMORY / "bauer-home" / "projects.json").read_text(encoding="utf-8"))
    assert len(registry["projects"]) == 1
    assert registry["projects"][0]["path"] == str(module.WORKSPACE)
    bindings = module.ChannelProjectStore(module.MEMORY / "sessions" / "channel_projects.sqlite3")
    assert bindings.binding("telegram:12345:12345") == (registry["projects"][0]["id"], str(module.WORKSPACE))


@pytest.mark.parametrize("users,allow_all", [([12345, 67890], False), ([12345], True), ([], False)])
def test_setup_refuses_nonexclusive_telegram_allowlist(tmp_path, monkeypatch, users, allow_all):
    module = _load_setup()
    module.ROOT = tmp_path / "prep"
    module.ROOT.mkdir()
    (module.ROOT / "operator.json").write_text(json.dumps({
        "telegram_token": "fake-telegram-token",
        "allowed_users": users,
        "allow_all": allow_all,
        "openrouter_api_key": "openrouter-secret",
        "model": "deepseek/deepseek-chat",
    }), encoding="utf-8")
    with pytest.raises(SystemExit, match="one allowlisted operator"):
        module.main()
    assert not (module.ROOT / ".env.bauer-canary").exists()


def test_sqlite_backup_uses_consistent_snapshot_and_validates_it(tmp_path):
    module = _load_backup()
    source = tmp_path / "source"
    destination = tmp_path / "backup"
    source.mkdir()
    destination.mkdir()
    database = source / "runtime_state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('snapshot')")
    module.SOURCE = source
    module.DESTINATION = destination

    module.main()

    with sqlite3.connect(destination / database.name) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("snapshot",)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
