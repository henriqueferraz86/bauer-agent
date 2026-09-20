"""Manifesto versionado e permissões do plugin runtime."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class PluginManifestError(ValueError):
    """Manifesto ausente, inválido ou incompatível com o runtime."""


ALLOWED_PERMISSIONS = frozenset(
    {
        "filesystem.read",
        "filesystem.write",
        "network.http",
        "shell.execute",
        "runtime.events",
        "runtime.tools",
    }
)
ALLOWED_CAPABILITIES = frozenset({"tools", "events", "memory", "channels", "agents"})
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}(?:[-+][0-9A-Za-z.-]+)?$")


@dataclass(frozen=True)
class PluginManifest:
    """Validated plugin contract."""

    id: str
    name: str
    version: str
    bauer_min_version: str
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    permission_rules: dict[str, Any] = field(default_factory=dict)
    tools: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    entry_point: str = ""
    source: str = ""

    @classmethod
    def from_file(cls, path: str | Path) -> "PluginManifest":
        manifest_path = Path(path)
        if not manifest_path.exists():
            raise PluginManifestError(f"Manifesto não encontrado: {manifest_path}")
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PluginManifestError(f"YAML inválido em {manifest_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise PluginManifestError(f"Manifesto precisa ser um mapeamento YAML: {manifest_path}")
        return cls.from_mapping(raw, source=manifest_path)

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, source: str | Path = "") -> "PluginManifest":
        identifier = str(raw.get("id", "")).strip().lower()
        name = str(raw.get("name", "")).strip()
        version = str(raw.get("version", "")).strip()
        bauer = raw.get("bauer")
        if not isinstance(bauer, dict):
            raise PluginManifestError("manifest.bauer é obrigatório e deve ser um mapa")
        min_version = str(bauer.get("min_version", "")).strip()
        if not _ID_RE.fullmatch(identifier):
            raise PluginManifestError("manifest.id deve usar [a-z0-9._-] e ter 2–64 caracteres")
        if not name:
            raise PluginManifestError("manifest.name é obrigatório")
        if not _VERSION_RE.fullmatch(version):
            raise PluginManifestError(f"manifest.version inválido: {version!r}")
        if not min_version:
            raise PluginManifestError("manifest.bauer.min_version é obrigatório")

        capabilities = _string_tuple(raw.get("capabilities"), "capabilities")
        unknown_capabilities = set(capabilities) - ALLOWED_CAPABILITIES
        if unknown_capabilities:
            raise PluginManifestError(
                "capabilities desconhecidas: " + ", ".join(sorted(unknown_capabilities))
            )
        tools = _string_tuple(raw.get("tools"), "tools")
        if tools and "tools" not in capabilities:
            raise PluginManifestError("manifest.tools exige capability 'tools'")

        permission_rules = _validate_permission_rules(raw.get("permissions"))
        permissions = tuple(sorted(_normalize_permissions(permission_rules)))
        if "runtime.tools" in permissions and "tools" not in capabilities:
            raise PluginManifestError("runtime.tools exige capability 'tools'")
        if "runtime.events" in permissions and "events" not in capabilities:
            raise PluginManifestError("runtime.events exige capability 'events'")

        return cls(
            id=identifier,
            name=name,
            version=version,
            bauer_min_version=min_version,
            capabilities=capabilities,
            permissions=permissions,
            permission_rules=permission_rules,
            tools=tools,
            requires=_string_tuple(raw.get("requires"), "requires"),
            entry_point=str(raw.get("entry_point", "")).strip(),
            source=str(source),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "bauer": {"min_version": self.bauer_min_version},
            "capabilities": list(self.capabilities),
            "permissions": self.permission_rules,
            "tools": list(self.tools),
            "requires": list(self.requires),
            "entry_point": self.entry_point,
        }


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise PluginManifestError(f"manifest.{field_name} deve ser uma lista de strings não vazias")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _validate_permission_rules(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        rules: dict[str, Any] = {item: True for item in _string_tuple(value, "permissions")}
        unknown = set(rules) - ALLOWED_PERMISSIONS
        if unknown:
            raise PluginManifestError("permissões desconhecidas: " + ", ".join(sorted(unknown)))
        return rules
    if not isinstance(value, dict) or not value:
        raise PluginManifestError("manifest.permissions deve declarar pelo menos uma permissão")

    unknown_groups = set(value) - {"network", "filesystem", "shell", "runtime"}
    if unknown_groups:
        raise PluginManifestError("grupos de permissão desconhecidos: " + ", ".join(sorted(unknown_groups)))
    rules: dict[str, Any] = {}
    if "network" in value:
        if not isinstance(value["network"], list) or any(not isinstance(item, str) or not item.strip() for item in value["network"]):
            raise PluginManifestError("permissions.network deve ser uma lista de hosts")
        rules["network"] = list(dict.fromkeys(item.strip() for item in value["network"]))
    if "filesystem" in value:
        filesystem = value["filesystem"]
        if not isinstance(filesystem, dict) or set(filesystem) - {"read", "write"}:
            raise PluginManifestError("permissions.filesystem aceita apenas read/write")
        rules["filesystem"] = dict(filesystem)
    if "shell" in value:
        rules["shell"] = value["shell"]
    if "runtime" in value:
        runtime = value["runtime"]
        if not isinstance(runtime, list) or any(item not in {"events", "tools"} for item in runtime):
            raise PluginManifestError("permissions.runtime aceita events/tools")
        rules["runtime"] = list(dict.fromkeys(runtime))
    return rules


def _normalize_permissions(rules: dict[str, Any]) -> set[str]:
    normalized: set[str] = set()
    if rules.get("network"):
        normalized.add("network.http")
    filesystem = rules.get("filesystem") or {}
    for access in ("read", "write"):
        if filesystem.get(access):
            normalized.add(f"filesystem.{access}")
    if rules.get("shell"):
        normalized.add("shell.execute")
    for capability in rules.get("runtime") or []:
        normalized.add(f"runtime.{capability}")
    return normalized
