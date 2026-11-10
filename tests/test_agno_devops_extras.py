"""Plano 067 / sprint 74 — contrato do extra ``agno-devops`` (roda sempre).

Os guards com as bibliotecas reais ficam em
``test_agno_devops_extras_installed.py``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEVOPS_PACKAGES = {"boto3", "kubernetes", "pygithub"}


def _name(requirement: str) -> str:
    return requirement.split(">")[0].split("=")[0].split("[")[0].strip().lower()


def test_devops_extra_is_optional_and_default_install_unchanged():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert {_name(req) for req in project["optional-dependencies"]["agno-devops"]} == DEVOPS_PACKAGES
    assert not {_name(req) for req in project["dependencies"]} & DEVOPS_PACKAGES

    packages = {pkg["name"]: pkg for pkg in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"]}
    root = packages["bauer-agent"]
    seen: set[str] = set()
    stack = [dep["name"] for dep in root.get("dependencies", [])]
    while stack:
        name = stack.pop()
        if name in seen or name not in packages:
            continue
        seen.add(name)
        stack.extend(dep["name"] for dep in packages[name].get("dependencies", []))
    heavy = DEVOPS_PACKAGES | {"botocore", "s3transfer", "pynacl", "durationpy", "websocket-client"}
    assert not seen & heavy, "o extra agno-devops vazou para a instalação padrão"
    assert {dep["name"] for dep in root["optional-dependencies"]["agno-devops"]} == DEVOPS_PACKAGES


def test_install_hints_point_to_the_extra():
    from bauer.core.runtime.agno_capabilities.manifest import reviewed_policies

    policies = reviewed_policies()
    for capability_id in ("bauer.aws", "bauer.kubernetes", "agno.github"):
        assert "bauer-agent[agno-devops]" in policies[capability_id].install_hint
