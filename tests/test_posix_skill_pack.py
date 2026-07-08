from __future__ import annotations

import subprocess

from bauer.core.policy import PolicyEngine
from bauer.core.skills import SkillExecutor, SkillRegistry


class FakeProcess:
    pid = 5150


def test_generic_open_app_resolves_by_platform():
    registry = SkillRegistry()

    assert registry.resolve_capability("os.open_app", platform_name="Windows").id == "windows.open_app"
    assert registry.resolve_capability("os.open_app", platform_name="Linux").id == "linux.open_app"
    assert registry.resolve_capability("os.open_app", platform_name="Darwin").id == "macos.open_app"


def test_linux_and_macos_manifests_are_registered():
    registry = SkillRegistry()

    assert registry.get("linux.open_app") is not None
    assert registry.get("linux.shell_safe") is not None
    assert registry.get("macos.open_app") is not None
    assert registry.get("macos.shell_safe") is not None
    assert any(match.id == "linux.open_app" for match in registry.find_by_capability("os.open_app"))
    assert any(match.id == "macos.open_app" for match in registry.find_by_capability("os.open_app"))


def test_linux_open_app_uses_linux_backend(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr("bauer.core.skills.posix.subprocess.Popen", fake_popen)
    manifest = SkillRegistry().get("linux.open_app")

    result = SkillExecutor(runtime_root=tmp_path).execute(manifest, {"app": "xdg-open", "args": ["README.md"]})

    assert result.status == "completed"
    assert result.output["pid"] == 5150
    assert calls == [["xdg-open", "README.md"]]


def test_macos_open_app_uses_open_command(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProcess()

    monkeypatch.setattr("bauer.core.skills.posix.subprocess.Popen", fake_popen)
    manifest = SkillRegistry().get("macos.open_app")

    result = SkillExecutor(runtime_root=tmp_path).execute(manifest, {"app": "Safari", "bundle": True})

    assert result.status == "completed"
    assert result.output["pid"] == 5150
    assert calls == [["open", "-a", "Safari"]]


def test_posix_shell_safe_requires_approval(tmp_path):
    registry = SkillRegistry()

    linux = SkillExecutor(runtime_root=tmp_path).execute(registry.get("linux.shell_safe"), {"command": "date"})
    macos = SkillExecutor(runtime_root=tmp_path).execute(registry.get("macos.shell_safe"), {"command": "date"})

    assert linux.status == "waiting_approval"
    assert macos.status == "waiting_approval"
    assert linux.output["approval"]["tool_name"] == "linux.shell_safe"
    assert macos.output["approval"]["tool_name"] == "macos.shell_safe"


def test_posix_shell_safe_runs_when_policy_allows(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("bauer.core.skills.posix.subprocess.run", fake_run)
    policy = PolicyEngine(rules=[{"id": "shell.allow.test", "operation": "shell.execute", "action": "allow"}])
    manifest = SkillRegistry().get("linux.shell_safe")

    result = SkillExecutor(runtime_root=tmp_path, policy_engine=policy).execute(manifest, {"command": "date"})

    assert result.status == "completed"
    assert result.output["shell"] == "bash"
    assert result.output["stdout"] == "ok\n"
    assert calls == [["bash", "-lc", "date"]]
