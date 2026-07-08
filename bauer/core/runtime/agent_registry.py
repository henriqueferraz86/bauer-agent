"""Formal Agent registry for runtime-governed agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .agent_spec import AgentSpec, agent_spec_from_mapping


class AgentRegistryError(ValueError):
    pass


class AgentRegistry:
    def __init__(self, roots: list[str | Path] | None = None):
        formal_root = Path(__file__).resolve().parents[2] / "data" / "agent_specs"
        self.roots = [Path(root) for root in (roots or [formal_root, Path("agents.yaml")])]

    def list(self) -> list[AgentSpec]:
        specs: dict[tuple[str, str], AgentSpec] = {}
        for path in self._spec_paths():
            for raw in self._read_specs(path):
                spec = agent_spec_from_mapping(raw)
                if not spec.permissions:
                    spec.permissions.extend(_permissions_from_tools(spec.tools))
                self._validate(spec, path)
                specs[(spec.id, spec.version)] = spec
        return sorted(specs.values(), key=lambda item: (item.id, _version_key(item.version)))

    def get(self, agent_id: str, version: str | None = None) -> AgentSpec | None:
        matches = [
            spec
            for spec in self.list()
            if spec.id == agent_id or spec.name == agent_id
        ]
        if version is not None:
            for spec in matches:
                if spec.version == version:
                    return spec
            return None
        return matches[-1] if matches else None

    def versions(self, agent_id: str) -> list[str]:
        return [spec.version for spec in self.list() if spec.id == agent_id or spec.name == agent_id]

    def by_permission(self, permission: str) -> list[AgentSpec]:
        needle = permission.strip().lower()
        return [
            spec
            for spec in self.list()
            if any(item.lower() == needle for item in spec.permissions)
        ]

    def _spec_paths(self) -> list[Path]:
        paths: list[Path] = []
        for root in self.roots:
            if root.is_file():
                paths.append(root)
                continue
            if not root.exists():
                continue
            paths.extend(sorted(root.rglob("agent.yaml")))
            paths.extend(sorted(path for path in root.rglob("*.yaml") if path.name != "agent.yaml"))
        return paths

    def _read_specs(self, path: Path) -> list[dict[str, Any]]:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise AgentRegistryError(f"{path}: agent spec must be a mapping")
        if isinstance(raw.get("agents"), list):
            return [item for item in raw["agents"] if isinstance(item, dict)]
        return [raw]

    def _validate(self, spec: AgentSpec, path: Path) -> None:
        if not spec.id:
            raise AgentRegistryError(f"{path}: id is required")
        if not spec.name:
            raise AgentRegistryError(f"{path}: name is required")
        if not spec.version:
            raise AgentRegistryError(f"{path}: version is required")
        if not spec.runtime_adapter:
            raise AgentRegistryError(f"{path}: runtime_adapter is required")
        if not spec.permissions:
            raise AgentRegistryError(f"{path}: permissions must not be empty")


def _version_key(version: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for item in version.replace("-", ".").split("."):
        parts.append(int(item) if item.isdigit() else item)
    return tuple(parts)


def _permissions_from_tools(tools: list[str]) -> list[str]:
    permissions = {"runtime.execute"}
    for tool in tools:
        if tool in {"read_file", "list_dir", "glob_files", "search_text"}:
            permissions.add("filesystem.read")
        elif tool in {"write_file", "append_file", "patch"}:
            permissions.add("filesystem.write")
        elif tool in {"run_command", "execute_code"}:
            permissions.add("shell.execute")
        elif tool.startswith("web_") or tool == "web_search":
            permissions.add("network.http")
    return sorted(permissions)
