"""Internal Bauer agent spec and mappings for runtime adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class AgentSpec:
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    runtime_adapter: str = "bauer_native"
    model: str = ""
    provider: str = ""
    instructions: str = ""
    tools: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    autonomy: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)
    policies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "runtime_adapter": self.runtime_adapter,
            "model": self.model,
            "provider": self.provider,
            "instructions": self.instructions,
            "tools": list(self.tools),
            "skills": list(self.skills),
            "permissions": list(self.permissions),
            "autonomy": dict(self.autonomy),
            "limits": dict(self.limits),
            "memory": dict(self.memory),
            "policies": list(self.policies),
        }

    def model_spec(self) -> dict[str, str]:
        return {"provider": self.provider, "name": self.model}


def parse_agents_yaml(path: str | Path = "agents.yaml") -> list[AgentSpec]:
    source = Path(path)
    if not source.exists():
        return []
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return []
    specs: list[AgentSpec] = []
    for item in raw.get("agents", []):
        if isinstance(item, dict) and item.get("name"):
            specs.append(agent_spec_from_mapping(item))
    return specs


def get_agent_spec(name: str, path: str | Path = "agents.yaml") -> AgentSpec | None:
    for spec in parse_agents_yaml(path):
        if spec.id == name or spec.name == name:
            return spec
    return None


def agent_spec_from_mapping(data: dict[str, Any]) -> AgentSpec:
    name = str(data.get("name") or data.get("id") or "").strip()
    instructions = str(data.get("instructions") or data.get("system") or data.get("system_prompt") or "")
    model_value = data.get("model")
    provider = str(data.get("provider") or "")
    model_name = ""
    if isinstance(model_value, dict):
        provider = str(model_value.get("provider") or provider)
        model_name = str(model_value.get("name") or model_value.get("id") or "")
    else:
        model_name = str(model_value or "")
    return AgentSpec(
        id=str(data.get("id") or name),
        name=name,
        version=str(data.get("version") or "0.1.0"),
        description=str(data.get("description") or ""),
        runtime_adapter=str(data.get("runtime_adapter") or "bauer_native"),
        model=model_name,
        provider=provider,
        instructions=instructions,
        tools=_string_list(data.get("tools")),
        skills=_string_list(data.get("skills") or data.get("capabilities")),
        permissions=_string_list(data.get("permissions")),
        autonomy=dict(data.get("autonomy") or {}) if isinstance(data.get("autonomy"), dict) else {},
        limits=dict(data.get("limits") or {}) if isinstance(data.get("limits"), dict) else {},
        memory=dict(data.get("memory") or {}) if isinstance(data.get("memory"), dict) else {},
        policies=_string_list(data.get("policies")),
    )


def agno_agent_spec_from_bauer(spec: AgentSpec) -> dict[str, Any]:
    return {
        "id": spec.id,
        "name": spec.name,
        "description": spec.description,
        "model": spec.model,
        "provider": spec.provider,
        "instructions": spec.instructions,
        "tools": list(spec.tools),
        "skills": list(spec.skills),
        "memory": dict(spec.memory),
        "policies": list(spec.policies),
    }


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
