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


def _volume_target(volume: str | dict) -> str:
    if isinstance(volume, str):
        return volume.split(":", 1)[1]
    return volume["target"]


def test_compose_exposes_the_four_bauer_surfaces_without_exposing_ollama():
    services = _compose()["services"]

    assert {"ollama", "ollama-init", "bauer", "agentos", "agent-ui"} <= services.keys()
    assert services["bauer"]["ports"] == ["8000:8000", "127.0.0.1:1455:1455"]
    assert services["agentos"]["ports"] == ["7777:7777"]
    assert services["agent-ui"]["ports"] == ["3000:3000"]
    assert "ports" not in services["ollama"]


def test_agentos_shares_runtime_and_waits_for_ollama_init():
    agentos = _compose()["services"]["agentos"]

    assert agentos["command"] == ["python", "-m", "bauer.agentos_app"]
    assert agentos["depends_on"]["ollama-init"]["condition"] == "service_completed_successfully"
    volume_targets = {_volume_target(item) for item in agentos["volumes"]}
    assert {"/app/workspace", "/app/memory", "/app/logs"} <= volume_targets
    assert "BAUER_HOME=/app/memory/bauer-home" in agentos["environment"]


def test_bauer_persists_openai_oauth_and_limits_callback_to_loopback():
    bauer = _compose()["services"]["bauer"]

    assert "127.0.0.1:1455:1455" in bauer["ports"]
    assert "BAUER_HOME=/app/memory/bauer-home" in bauer["environment"]
    assert "BAUER_OAUTH_CALLBACK_HOST=0.0.0.0" in bauer["environment"]


def test_config_binds_do_not_create_missing_host_paths_as_directories():
    services = _compose()["services"]

    for service_name in ("bauer", "agentos"):
        bind_mounts = {
            volume["target"]: volume
            for volume in services[service_name]["volumes"]
            if isinstance(volume, dict)
        }
        for target in ("/app/config.yaml", "/app/models.yaml"):
            mount = bind_mounts[target]
            assert mount["type"] == "bind"
            assert mount["read_only"] is True
            assert mount["bind"]["create_host_path"] is False


def test_agent_ui_is_built_from_official_repo_and_has_update_controls():
    dockerfile = (ROOT / "agent-ui" / "Dockerfile").read_text(encoding="utf-8")
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "https://github.com/agno-agi/agent-ui.git" in dockerfile
    assert "ARG AGENT_UI_REF=main" in dockerfile
    assert "ARG AGENT_UI_ENDPOINT=http://localhost:7777" in dockerfile
    assert "pnpm-workspace.yaml" in dockerfile
    assert "COPY --from=builder /app/pnpm-workspace.yaml ./pnpm-workspace.yaml" in dockerfile
    assert "onlyBuiltDependencies" in dockerfile
    assert "allowBuilds" in dockerfile
    assert (ROOT / "agent-ui" / "patch-endpoint.mjs").exists()
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "AGENT_UI_REF: ${AGENT_UI_REF:-main}" in compose_text
    assert "AGENT_UI_ENDPOINT: ${AGENT_UI_ENDPOINT:-http://localhost:7777}" in compose_text


def test_bauer_image_installs_all_server_extra_dependencies_before_source_copy():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    metadata_copy = dockerfile.index("COPY bauer/__init__.py")
    server_install = dockerfile.index('pip install --no-cache-dir ".[server]"')
    source_copy = dockerfile.index("COPY bauer/ ./bauer/")
    assert metadata_copy < server_install < source_copy
    assert "argon2-cffi google-auth" in dockerfile


def test_runbook_documents_api_key_commands_for_linux_and_windows():
    runbook = (ROOT / "docs" / "runbooks" / "docker-agentos.md").read_text(encoding="utf-8")

    assert "#### Linux" in runbook
    assert "grep '^BAUER_SERVE_API_KEY=' .env" in runbook
    assert "#### Windows (PowerShell)" in runbook
    assert "docker inspect bauer-agent" in runbook
    assert "BAUER_SERVE_API_KEY não encontrada" in runbook
    assert "X-API-Key" in runbook

    auth_runbook = (ROOT / "docs" / "runbooks" / "serve-auth.md").read_text(encoding="utf-8")
    assert "Chave de bootstrap" in auth_runbook
    assert "BAUER_AUTH_GOOGLE_CLIENT_ID" in auth_runbook
    assert "cookie `HttpOnly`" in auth_runbook


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
