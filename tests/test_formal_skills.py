from __future__ import annotations

from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.policy import PolicyEngine
from bauer.core.skills import SkillExecutor, SkillManifest, SkillRegistry


def test_formal_manifest_required_schema_validates():
    manifest = SkillManifest.from_mapping(
        {
            "id": "test.skill",
            "name": "Test Skill",
            "version": "1.0.0",
            "description": "A test skill.",
            "capabilities": ["test.capability"],
            "permissions": ["filesystem.read"],
            "risk": "low",
            "platforms": ["windows"],
            "inputs": {"prompt": {"type": "string"}},
            "outputs": {"result": {"type": "text"}},
        }
    )

    assert manifest.id == "test.skill"
    assert manifest.capabilities == ["test.capability"]


def test_skill_registry_finds_built_in_capability():
    registry = SkillRegistry()

    matches = registry.find_by_capability("code.review")

    assert any(match.id == "bauer.coding" for match in matches)
    assert registry.get("bauer.devops") is not None


def test_legacy_skill_yaml_is_adapted_for_compatibility():
    registry = SkillRegistry()

    legacy = registry.get("legacy.coding.code-review")

    assert legacy is not None
    assert legacy.legacy is True
    assert "code.review" in legacy.capabilities


def test_skill_executor_uses_policy_permissions():
    manifest = SkillManifest.from_mapping(
        {
            "id": "test.shell",
            "name": "Shell Skill",
            "version": "1.0.0",
            "description": "Needs shell.",
            "capabilities": ["shell.test"],
            "permissions": ["shell.execute"],
            "risk": "high",
            "platforms": ["windows"],
            "inputs": {},
            "outputs": {},
        }
    )

    result = SkillExecutor(policy_engine=PolicyEngine()).execute(manifest, {"command": "git status"})

    assert result.status == "waiting_approval"
    assert "shell.execute" in result.output["decision"]["reason"]


def test_skills_cli_validate_inspect_capabilities_and_find():
    runner = CliRunner()

    validate = runner.invoke(app, ["skills", "validate"])
    inspect = runner.invoke(app, ["skills", "inspect", "bauer.coding"])
    capabilities = runner.invoke(app, ["skills", "capabilities"])
    find = runner.invoke(app, ["skills", "find", "devops.operate"])

    assert validate.exit_code == 0
    assert inspect.exit_code == 0
    assert '"id": "bauer.coding"' in inspect.output
    assert capabilities.exit_code == 0
    assert "code.review" in capabilities.output
    assert find.exit_code == 0
    assert "bauer.devops" in find.output
