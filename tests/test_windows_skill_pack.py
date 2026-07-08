from __future__ import annotations

import subprocess

from bauer.core.events import EventBus
from bauer.core.policy import ApprovalManager, PolicyEngine
from bauer.core.skills import SkillExecutor, SkillRegistry


class FakeProcess:
    pid = 4242


def test_windows_skill_manifests_are_registered():
    registry = SkillRegistry()

    assert registry.get("windows.open_app") is not None
    assert registry.get("windows.browser") is not None
    assert registry.get("windows.control_panel") is not None
    assert registry.get("windows.powershell_safe") is not None
    assert registry.find_by_capability("os.windows.open_app")[0].id == "windows.open_app"
    assert any(match.id == "windows.browser" for match in registry.find_by_capability("browser.navigate"))


def test_windows_open_app_executes_and_audits(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr("bauer.core.skills.windows.subprocess.Popen", fake_popen)
    bus = EventBus(root=tmp_path)
    manifest = SkillRegistry().get("windows.open_app")

    result = SkillExecutor(runtime_root=tmp_path, event_bus=bus).execute(manifest, {"app": "notepad.exe"})

    assert result.status == "completed"
    assert result.output["pid"] == 4242
    assert calls == [["notepad.exe"]]
    events = [event.event_type for event in bus.list_events()]
    assert "tool.call.requested" in events
    assert "tool.call.completed" in events
    assert "skill.executed" in events
    audit_actions = [record["action"] for record in bus.store.list("audit")]
    assert "tool.call.completed" in audit_actions


def test_windows_browser_uses_default_browser(monkeypatch, tmp_path):
    opened: list[str] = []

    def fake_open(url, new=0):
        opened.append(url)
        return True

    monkeypatch.setattr("bauer.core.skills.windows.webbrowser.open", fake_open)
    manifest = SkillRegistry().get("windows.browser")

    result = SkillExecutor(runtime_root=tmp_path).execute(manifest, {"url": "https://example.com"})

    assert result.status == "completed"
    assert result.output["opened"] is True
    assert opened == ["https://example.com"]


def test_windows_control_panel_requires_policy_approval(tmp_path):
    manifest = SkillRegistry().get("windows.control_panel")
    bus = EventBus(root=tmp_path)

    result = SkillExecutor(runtime_root=tmp_path, event_bus=bus).execute(manifest, {"target": "settings"})

    assert result.status == "waiting_approval"
    assert result.output["approval"]["operation"] == "skill.execute"
    assert result.output["approval"]["tool_name"] == "windows.control_panel"
    events = [event.event_type for event in bus.list_events()]
    assert "approval.requested" in events


def test_windows_control_panel_runs_with_approved_request(monkeypatch, tmp_path):
    started: list[str] = []

    def fake_startfile(uri):
        started.append(uri)

    monkeypatch.setattr("bauer.core.skills.windows.sys.platform", "win32")
    monkeypatch.setattr("bauer.core.skills.windows.os.startfile", fake_startfile, raising=False)
    manifest = SkillRegistry().get("windows.control_panel")
    first = SkillExecutor(runtime_root=tmp_path).execute(manifest, {"target": "settings"})
    approval_id = first.output["approval"]["id"]
    ApprovalManager(root=tmp_path).approve(approval_id)

    result = SkillExecutor(runtime_root=tmp_path).execute(
        manifest,
        {"target": "settings", "approval_id": approval_id},
    )

    assert result.status == "completed"
    assert started == ["ms-settings:"]


def test_windows_powershell_requires_approval_by_default(tmp_path):
    manifest = SkillRegistry().get("windows.powershell_safe")

    result = SkillExecutor(runtime_root=tmp_path).execute(manifest, {"command": "Get-Date"})

    assert result.status == "waiting_approval"
    assert "shell.execute" in result.output["decision"]["reason"]


def test_windows_powershell_runs_when_policy_allows(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("bauer.core.skills.windows.subprocess.run", fake_run)
    manifest = SkillRegistry().get("windows.powershell_safe")
    policy = PolicyEngine(rules=[{"id": "shell.allow.test", "operation": "shell.execute", "action": "allow"}])

    result = SkillExecutor(runtime_root=tmp_path, policy_engine=policy).execute(manifest, {"command": "Get-Date"})

    assert result.status == "completed"
    assert result.output["exit_code"] == 0
    assert result.output["stdout"] == "ok\n"
    assert captured[0][-2:] == ["-Command", "Get-Date"]
