"""Restricted routers use only explicitly allowed built-ins and metadata logs."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from bauer.core.events import EventBus
from bauer.core.policy import ApprovalManager
from bauer.plugin_hooks import hooks
from bauer.tool_registry import ToolRegistry
from bauer.tool_router import ToolError, ToolRouter


def test_restricted_router_dispatches_builtin_without_hooks_or_payload_logs(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    private_name = "private-project-name.txt"
    private_text = "fictional confidential test content"
    (workspace / private_name).write_text(private_text, encoding="utf-8")
    registry = ToolRegistry()
    extension = Mock(return_value="extension result")
    registry.register("read_file", extension, description="extension", args={})
    registry.register("extra_read", extension, description="extra", args={})
    monkeypatch.setattr(ToolRegistry, "_instance", registry)
    load_hooks = Mock()
    emit_hook = Mock()
    monkeypatch.setattr(hooks, "ensure_plugins_loaded", load_hooks)
    monkeypatch.setattr(hooks, "emit", emit_hook)
    bus = EventBus(root=tmp_path / "runtime")
    audit_dir = tmp_path / "audit"
    router = ToolRouter(
        workspace=workspace,
        tool_allowlist={"read_file", "extra_read"},
        policy_root=tmp_path / "runtime",
        event_bus=bus,
        audit_logs_dir=audit_dir,
        restricted_builtin_only=True,
    )

    assert router.available_tools() == ["read_file"]
    assert router.tool_info("read_file")["source"] == "builtin"
    assert private_text in router.execute_native_call("read_file", {"path": private_name})
    with pytest.raises(ToolError):
        router.execute_native_call("extra_read", {})
    with pytest.raises(ToolError):
        router.execute_native_call("read_file", {"path": "another-private-name.txt"})
    extension.assert_not_called()
    load_hooks.assert_not_called()
    emit_hook.assert_not_called()

    audit = (audit_dir / "audit.jsonl").read_text(encoding="utf-8")
    events = bus.list_events()
    assert any(event.event_type == "tool.call.completed" for event in events)
    assert any(event.event_type == "tool.call.failed" for event in events)
    for value in (private_name, private_text, "another-private-name.txt"):
        assert value not in audit
        assert value not in str(events)


@pytest.mark.parametrize("decision", ["ask", "deny"])
def test_restricted_router_rejects_non_allow_without_approval(tmp_path, monkeypatch, decision):
    workspace = tmp_path / "project"
    workspace.mkdir()
    rules = tmp_path / "rules.yaml"
    rules.write_text(f"rules:\n  - operation: filesystem.read\n    action: {decision}\n", encoding="utf-8")
    approval = Mock()
    monkeypatch.setattr(ApprovalManager, "request", approval)
    monkeypatch.setattr(ApprovalManager, "authorize", approval)
    read_file = Mock()
    monkeypatch.setattr(ToolRouter, "_read_file", read_file)
    router = ToolRouter(
        workspace=workspace,
        tool_allowlist={"read_file"},
        policy_root=tmp_path / "runtime",
        policy_rules_path=rules,
        restricted_builtin_only=True,
    )

    with pytest.raises(ToolError, match="allow policy decision"):
        router.execute_native_call("read_file", {"path": "file.txt"})
    approval.assert_not_called()
    read_file.assert_not_called()


def test_restricted_router_requires_an_allowlist(tmp_path: Path):
    with pytest.raises(ValueError, match="explicit tool allowlist"):
        ToolRouter(workspace=tmp_path, restricted_builtin_only=True)


def test_restricted_router_requires_durable_request_audit(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    bus = EventBus(root=tmp_path / "runtime")
    real_publish = bus.publish

    def publish(event_type, **kwargs):
        if event_type == "tool.call.requested":
            raise OSError("audit unavailable")
        return real_publish(event_type, **kwargs)

    monkeypatch.setattr(bus, "publish", publish)
    read_file = Mock()
    monkeypatch.setattr(ToolRouter, "_read_file", read_file)
    router = ToolRouter(workspace=workspace, tool_allowlist={"read_file"}, event_bus=bus,
                        policy_root=tmp_path / "runtime", restricted_builtin_only=True)

    with pytest.raises(ToolError, match="audit unavailable"):
        router.execute_native_call("read_file", {"path": "file.txt"})
    read_file.assert_not_called()
