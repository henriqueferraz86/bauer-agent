from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bauer.automation_scheduler import AutomationScheduler
from bauer.automation_store import AutomationStore
from bauer.cli import app
from bauer.gateway_channels import GatewayChannelRegistry, resolve_delivery_spec
from bauer.gateway_outbox import GatewayOutbox
from bauer.workspace_manager import WorkspaceManager


class _DummyResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):  # type: ignore[no-untyped-def]
        return False


def test_gateway_channel_registry_persists_and_resolves(tmp_path: Path):
    registry = GatewayChannelRegistry(tmp_path)
    saved = registry.upsert(
        name="Alerts Main",
        platform="telegram",
        target="12345",
        metadata={"token_env": "TELEGRAM_BOT_TOKEN"},
    )

    loaded = GatewayChannelRegistry(tmp_path).get("alerts-main")
    resolved = resolve_delivery_spec("channel:alerts-main", GatewayChannelRegistry(tmp_path))

    assert saved.name == "alerts-main"
    assert loaded is not None
    assert loaded.platform == "telegram"
    assert resolved.channel == "telegram"
    assert resolved.target == "12345"
    assert resolved.metadata["gateway_channel"] == "alerts-main"
    assert resolved.metadata["token_env"] == "TELEGRAM_BOT_TOKEN"


def test_gateway_channel_registry_rejects_raw_secret_metadata(tmp_path: Path):
    registry = GatewayChannelRegistry(tmp_path)

    try:
        registry.upsert(name="bad", platform="telegram", target="1", metadata={"token": "raw-secret"})
    except ValueError as exc:
        assert "must not store secrets" in str(exc)
    else:
        raise AssertionError("raw token metadata should be rejected")


def test_gateway_outbox_delivers_telegram_with_env_token(tmp_path: Path, monkeypatch):
    captured: dict[str, object] = {}

    def fake_urlopen(req, timeout=0):  # type: ignore[no-untyped-def]
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _DummyResponse()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token-123")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    outbox = GatewayOutbox(tmp_path)
    message = outbox.enqueue(
        channel="telegram",
        target="chat-1",
        payload={"text": "hello telegram"},
        metadata={"token_env": "TELEGRAM_BOT_TOKEN"},
    )
    result = outbox.deliver_message(message.message_id)

    assert result.delivered == [message.message_id]
    assert "api.telegram.org/bottoken-123/sendMessage" in str(captured["url"])
    assert captured["body"] == {"chat_id": "chat-1", "text": "hello telegram"}


def test_automation_delivery_resolves_registered_channel(tmp_path: Path):
    workspace = tmp_path / "workspace"
    WorkspaceManager(workspace).init_project("Delivery")
    GatewayChannelRegistry(workspace).upsert(
        name="alerts",
        platform="file",
        target="deliveries/alerts.jsonl",
    )
    AutomationStore(workspace).create_job(
        name="notify",
        prompt="Notify me",
        schedule="every 1h",
        next_run_at="2026-06-01T09:00:00+00:00",
        metadata={"delivery": "channel:alerts"},
    )

    AutomationScheduler(workspace).tick(now="2026-06-01T09:00:00+00:00")

    message = GatewayOutbox(workspace).list_messages()[0]
    assert message.channel == "file"
    assert message.target == "deliveries/alerts.jsonl"
    assert message.metadata["gateway_channel"] == "alerts"

    result = GatewayOutbox(workspace).deliver_message(message.message_id)
    delivered = workspace / "deliveries" / "alerts.jsonl"
    assert result.delivered == [message.message_id]
    assert delivered.exists()


def test_gateway_channel_cli_and_send_dry_run(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    add = runner.invoke(
        app,
        [
            "gateway-channel-add",
            "alerts",
            "file",
            "deliveries/alerts.jsonl",
            "--workspace",
            str(workspace),
        ],
    )
    listed = runner.invoke(app, ["gateway-channels", "--workspace", str(workspace), "--json"])
    dry_run = runner.invoke(app, ["gateway-send", "alerts", "hello", "--workspace", str(workspace), "--dry-run"])

    assert add.exit_code == 0
    assert listed.exit_code == 0
    assert json.loads(listed.output)[0]["name"] == "alerts"
    assert dry_run.exit_code == 0
    assert json.loads(dry_run.output)["source"] == "channel:alerts"


def test_gateway_send_cli_rejects_raw_secret_metadata(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    result = runner.invoke(
        app,
        ["gateway-send", "file", "hello", "--workspace", str(workspace), "--target", "deliveries/out.jsonl", "--metadata-json", '{"authorization":"Bearer raw"}'],
    )

    assert result.exit_code == 2


def test_gateway_send_cli_deliver_now_direct_file(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    result = runner.invoke(
        app,
        ["gateway-send", "file", "hello file", "--workspace", str(workspace), "--target", "deliveries/out.jsonl", "--deliver-now"],
    )

    delivered = workspace / "deliveries" / "out.jsonl"
    data = json.loads(delivered.read_text(encoding="utf-8").splitlines()[0])
    assert result.exit_code == 0
    assert data["payload"]["text"] == "hello file"
