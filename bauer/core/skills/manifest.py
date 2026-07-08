"""Formal skill manifest schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class SkillManifestError(ValueError):
    pass


REQUIRED_FIELDS = (
    "id",
    "name",
    "version",
    "description",
    "capabilities",
    "permissions",
    "risk",
    "platforms",
    "inputs",
    "outputs",
)


@dataclass(slots=True)
class SkillManifest:
    id: str
    name: str
    version: str
    description: str
    capabilities: list[str]
    permissions: list[str]
    risk: str
    platforms: list[str]
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    path: str = ""
    legacy: bool = False

    @classmethod
    def from_file(cls, path: str | Path) -> "SkillManifest":
        source = Path(path)
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise SkillManifestError(f"{source}: manifest must be a mapping")
        if all(field in raw for field in REQUIRED_FIELDS):
            manifest = cls.from_mapping(raw)
            manifest.path = str(source)
            return manifest
        manifest = cls.from_legacy_mapping(raw, source)
        return manifest

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "SkillManifest":
        missing = [field for field in REQUIRED_FIELDS if field not in raw]
        if missing:
            raise SkillManifestError(f"missing required fields: {', '.join(missing)}")
        manifest = cls(
            id=_required_str(raw, "id"),
            name=_required_str(raw, "name"),
            version=_required_str(raw, "version"),
            description=_required_str(raw, "description"),
            capabilities=_str_list(raw.get("capabilities")),
            permissions=_str_list(raw.get("permissions")),
            risk=_required_str(raw, "risk"),
            platforms=_str_list(raw.get("platforms")),
            inputs=dict(raw.get("inputs") or {}),
            outputs=dict(raw.get("outputs") or {}),
        )
        manifest.validate()
        return manifest

    @classmethod
    def from_legacy_mapping(cls, raw: dict[str, Any], source: Path) -> "SkillManifest":
        name = str(raw.get("name") or source.stem).strip()
        category = source.parent.name if source.parent.name != "skills" else "general"
        tools = _str_list(raw.get("tools"))
        manifest = cls(
            id=f"legacy.{category}.{source.stem}",
            name=name,
            version=str(raw.get("version") or "1.0.0"),
            description=str(raw.get("description") or raw.get("content") or "")[:240],
            capabilities=_legacy_capabilities(category, source.stem, raw),
            permissions=_permissions_from_tools(tools),
            risk=_risk_from_permissions(_permissions_from_tools(tools)),
            platforms=["windows", "linux", "darwin"],
            inputs=dict(raw.get("params") or {}),
            outputs={"type": "text"},
            path=str(source),
            legacy=True,
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not self.id:
            raise SkillManifestError("id is required")
        if not self.name:
            raise SkillManifestError("name is required")
        if not self.version:
            raise SkillManifestError("version is required")
        if not self.description:
            raise SkillManifestError("description is required")
        if not self.capabilities:
            raise SkillManifestError(f"{self.id}: capabilities must not be empty")
        if not self.permissions:
            raise SkillManifestError(f"{self.id}: permissions must not be empty")
        if self.risk not in {"low", "medium", "high", "critical"}:
            raise SkillManifestError(f"{self.id}: invalid risk {self.risk!r}")
        if not self.platforms:
            raise SkillManifestError(f"{self.id}: platforms must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "risk": self.risk,
            "platforms": list(self.platforms),
            "inputs": dict(self.inputs),
            "outputs": dict(self.outputs),
            "path": self.path,
            "legacy": self.legacy,
        }


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = str(raw.get(key) or "").strip()
    if not value:
        raise SkillManifestError(f"{key} is required")
    return value


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _legacy_capabilities(category: str, stem: str, raw: dict[str, Any]) -> list[str]:
    tags = _str_list(raw.get("tags"))
    values = [f"skill.{category}.{stem}", *[f"tag.{tag}" for tag in tags]]
    if category == "coding":
        values.append("code.review" if "review" in stem else "code.modify")
    if category == "devops":
        values.append("devops.operate")
    if category == "research":
        values.append("research.search")
    return values


def _permissions_from_tools(tools: list[str]) -> list[str]:
    permissions = {"runtime.execute"}
    for tool in tools:
        if tool in {"read_file", "list_dir", "glob_files", "search_text"}:
            permissions.add("filesystem.read")
        elif tool in {"write_file", "append_file", "patch"}:
            permissions.add("filesystem.write")
        elif tool in {"run_command", "execute_code"}:
            permissions.add("shell.execute")
        elif tool.startswith("web_"):
            permissions.add("network.request")
    return sorted(permissions)


def _risk_from_permissions(permissions: list[str]) -> str:
    if any(permission in permissions for permission in {"shell.execute", "social.publish", "os.ui_control"}):
        return "high"
    if "filesystem.write" in permissions or "network.request" in permissions:
        return "medium"
    return "low"
