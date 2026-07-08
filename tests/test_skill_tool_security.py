from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from bauer.core.policy import ApprovalManager, PolicyEngine
from bauer.core.skills import SkillExecutor, SkillManifest, SkillManifestError, SkillRegistry
from bauer.secrets_scanner import has_secrets, redact
from bauer.shell_runner import ShellRunner
from bauer.tool_router import SandboxError, ToolError, ToolRouter


def _router(workspace: Path, *, policy_root: Path) -> ToolRouter:
    return ToolRouter(
        workspace=workspace,
        shell_runner=ShellRunner(workspace=workspace, safe_mode=True, timeout=5),
        policy_enabled=True,
        policy_root=policy_root,
        session_id="security-session",
        run_id="security-run",
    )


def test_security_policy_decisions_cover_sensitive_operations(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    engine = PolicyEngine(workspace=workspace)

    delete = engine.evaluate("filesystem.delete", {"path": str(workspace.parent / "outside.txt")})
    shell = engine.evaluate("shell.execute", {"command": "rm -rf build"})
    social = engine.evaluate("social.publish", {"text": "publish this"})
    production = engine.evaluate("production.deploy", {"environment": "production"})
    secret_network = engine.evaluate("network.http", {"url": "https://api.example.com", "contains_secret": True})

    assert delete.action == "ask"
    assert "filesystem.delete.ask" in delete.matched_rules
    assert shell.action == "ask"
    assert social.action == "ask"
    assert production.action == "ask"
    assert production.risk_level == "high"
    assert secret_network.action == "deny"
    assert "secret_exfiltration" in secret_network.matched_rules[0]


def test_delete_outside_workspace_requires_approval_before_execution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("do not delete", encoding="utf-8")
    router = _router(workspace, policy_root=tmp_path / "runtime")

    with pytest.raises(ToolError, match="waiting_approval") as exc_info:
        router.execute({"action": "delete_file", "args": {"path": str(outside), "confirm": True}})

    approval_id = re.search(r"approval_id=(appr-[0-9a-f-]+)", str(exc_info.value)).group(1)  # type: ignore[union-attr]
    approval = ApprovalManager(root=tmp_path / "runtime").get(approval_id)
    assert outside.exists()
    assert approval is not None
    assert approval.operation == "filesystem.delete"


def test_destructive_shell_command_requires_approval_before_execution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    router = _router(workspace, policy_root=tmp_path / "runtime")
    command = f'"{sys.executable}" -c "print(123)"'

    with pytest.raises(ToolError, match="waiting_approval") as exc_info:
        router.execute({"action": "run_command", "args": {"command": command}})

    approval_id = re.search(r"approval_id=(appr-[0-9a-f-]+)", str(exc_info.value)).group(1)  # type: ignore[union-attr]
    approval = ApprovalManager(root=tmp_path / "runtime").get(approval_id)
    assert approval is not None
    assert approval.operation == "shell.execute"
    assert approval.status == "pending"


def test_path_traversal_read_is_blocked_by_sandbox(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    router = ToolRouter(workspace=workspace)

    with pytest.raises(SandboxError):
        router.execute({"action": "read_file", "args": {"path": "../secret.txt"}})


def test_secret_material_is_not_allowed_to_leave_over_network():
    secret = "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz1234567890"
    assert has_secrets(secret)
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in redact(secret)

    decision = PolicyEngine().evaluate(
        "network.http",
        {"url": "https://api.example.com/collect", "contains_secret": has_secrets(secret)},
    )

    assert decision.action == "deny"
    assert decision.risk_level == "high"


def test_social_publish_requires_approval_for_skill():
    manifest = SkillManifest.from_mapping(
        {
            "id": "security.social",
            "name": "Social Publish",
            "version": "1.0.0",
            "description": "Publishes social content.",
            "capabilities": ["social.publish"],
            "permissions": ["social.publish"],
            "risk": "high",
            "platforms": ["windows", "linux", "darwin"],
            "inputs": {},
            "outputs": {},
        }
    )

    result = SkillExecutor().execute(manifest, {"text": "publish"})

    assert result.status == "waiting_approval"
    assert result.output["approval"]["operation"] == "skill.execute"
    assert "social.publish" in result.output["decision"]["reason"]


def test_production_deploy_requires_approval_for_skill():
    manifest = SkillManifest.from_mapping(
        {
            "id": "security.deploy",
            "name": "Production Deploy",
            "version": "1.0.0",
            "description": "Deploys production.",
            "capabilities": ["deploy.production"],
            "permissions": ["production.deploy"],
            "risk": "critical",
            "platforms": ["windows", "linux", "darwin"],
            "inputs": {},
            "outputs": {},
        }
    )

    result = SkillExecutor().execute(manifest, {"environment": "production"})

    assert result.status == "waiting_approval"
    assert "production.deploy" in result.output["decision"]["reason"]


def test_skill_without_valid_manifest_is_rejected(tmp_path):
    invalid = tmp_path / "skill.yaml"
    invalid.write_text(
        "id: broken.skill\n"
        "name: Broken\n"
        "version: 1.0.0\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillManifestError):
        SkillManifest.from_file(invalid)

    valid, errors = SkillRegistry([tmp_path]).validate_all()
    assert valid == []
    assert errors
