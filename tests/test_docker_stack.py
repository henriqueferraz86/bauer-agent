"""Contrato estático do stack Docker integrado.

Os testes não iniciam Docker, não fazem pull de imagens e não acessam a rede.
O smoke test real fica reservado para um host com Docker disponível.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parent.parent


def _compose() -> dict:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_exposes_the_four_bauer_surfaces_without_exposing_ollama():
    services = _compose()["services"]

    assert {"ollama", "ollama-init", "bauer", "agentos", "agent-ui"} <= services.keys()
    assert services["bauer"]["ports"] == ["8000:8000"]
    assert services["agentos"]["ports"] == ["7777:7777"]
    assert services["agent-ui"]["ports"] == ["3000:3000"]
    assert "ports" not in services["ollama"]


def test_agentos_shares_runtime_and_waits_for_ollama_init():
    agentos = _compose()["services"]["agentos"]

    assert agentos["command"] == ["python", "-m", "bauer.agentos_app"]
    assert agentos["depends_on"]["ollama-init"]["condition"] == "service_completed_successfully"
    volume_targets = {item.split(":", 1)[1] for item in agentos["volumes"]}
    assert {"/app/workspace", "/app/memory", "/app/logs"} <= volume_targets


def test_agent_ui_is_built_from_official_repo_and_has_update_controls():
    dockerfile = (ROOT / "agent-ui" / "Dockerfile").read_text(encoding="utf-8")
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "https://github.com/agno-agi/agent-ui.git" in dockerfile
    assert "ARG AGENT_UI_REF=main" in dockerfile
    assert "ARG AGENT_UI_ENDPOINT=http://localhost:7777" in dockerfile
    assert "pnpm-workspace.yaml" in dockerfile
    assert "onlyBuiltDependencies" in dockerfile
    assert "allowBuilds" in dockerfile
    assert (ROOT / "agent-ui" / "patch-endpoint.mjs").exists()
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "AGENT_UI_REF: ${AGENT_UI_REF:-main}" in compose_text
    assert "AGENT_UI_ENDPOINT: ${AGENT_UI_ENDPOINT:-http://localhost:7777}" in compose_text


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="requer bash POSIX")
def test_install_help_exposes_docker_modes():
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--docker" in result.stdout
    assert "--no-docker" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt", reason="requer bash POSIX")
def test_install_rejects_conflicting_docker_modes():
    result = subprocess.run(
        ["bash", str(ROOT / "install.sh"), "--docker", "--no-docker"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "apenas uma" in result.stderr
