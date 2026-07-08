from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.skills import SkillMarketplace, SkillMarketplaceError, SkillRegistry
from bauer.desktop_api import build_desktop_router


def _package(root: Path, skill_id: str = "local.echo") -> Path:
    pkg = root / "skill-package"
    (pkg / "tests").mkdir(parents=True)
    (pkg / "examples").mkdir()
    (pkg / "skill.yaml").write_text(
        f"""
id: {skill_id}
name: Local Echo
version: 1.0.0
description: Local marketplace echo skill.
capabilities:
  - local.echo
permissions:
  - filesystem.read
risk: low
platforms:
  - windows
  - linux
  - darwin
inputs:
  text:
    type: string
outputs:
  result:
    type: text
""".strip(),
        encoding="utf-8",
    )
    (pkg / "skill.py").write_text("def run(inputs):\n    return inputs\n", encoding="utf-8")
    (pkg / "README.md").write_text("# Local Echo\n", encoding="utf-8")
    (pkg / "tests" / "test_echo.py").write_text("def test_echo():\n    assert True\n", encoding="utf-8")
    (pkg / "examples" / "input.json").write_text('{"text":"hi"}\n', encoding="utf-8")
    return pkg


def test_skill_package_format_and_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    pkg = _package(tmp_path)
    market = SkillMarketplace()

    info = market.package(pkg)
    again = market.package(pkg)

    assert info.id == "local.echo"
    assert info.package_hash.startswith("sha256:")
    assert info.package_hash == again.package_hash
    assert "skill.yaml" in info.files
    assert "skill.py" in info.files


def test_install_requires_explicit_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    pkg = _package(tmp_path)

    try:
        SkillMarketplace().install(pkg)
    except SkillMarketplaceError as exc:
        assert "explicit approval" in str(exc)
    else:
        raise AssertionError("install should require explicit approval")


def test_invalid_skill_package_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    pkg = tmp_path / "bad-package"
    pkg.mkdir()
    (pkg / "skill.py").write_text("pass\n", encoding="utf-8")

    try:
        SkillMarketplace().package(pkg)
    except SkillMarketplaceError as exc:
        assert "skill.yaml" in str(exc)
    else:
        raise AssertionError("package without manifest should be rejected")


def test_install_uninstall_updates_index_and_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    pkg = _package(tmp_path)
    market = SkillMarketplace()

    installed = market.install(pkg, yes=True)

    assert installed.installed_path
    assert market.index()["local.echo"]["package_hash"] == installed.package_hash
    assert SkillRegistry().get("local.echo") is not None

    removed = market.uninstall("local.echo")

    assert removed.id == "local.echo"
    assert "local.echo" not in market.index()
    assert SkillRegistry().get("local.echo") is None


def test_skills_cli_package_install_uninstall_and_dashboard(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    pkg = _package(tmp_path)
    runner = CliRunner()

    packaged = runner.invoke(app, ["skills", "package", str(pkg)])
    blocked = runner.invoke(app, ["skills", "install", str(pkg)])
    installed = runner.invoke(app, ["skills", "install", str(pkg), "--yes"])

    api = FastAPI()
    api.include_router(build_desktop_router())
    client = TestClient(api)
    skills = client.get("/api/skills").json()["skills"]

    removed = runner.invoke(app, ["skills", "uninstall", "local.echo"])

    assert packaged.exit_code == 0
    assert "sha256:" in packaged.output
    assert blocked.exit_code == 1
    assert "permis" in blocked.output.lower()
    assert installed.exit_code == 0
    assert "installed" in installed.output
    assert any(skill["id"] == "local.echo" for skill in skills)
    assert removed.exit_code == 0
    assert "uninstalled" in removed.output
