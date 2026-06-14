"""Skill bundles — aliases that load multiple skills under one slash command.

A bundle groups N skill slugs together. Invoking ``/<bundle-name>`` loads
every referenced skill's full content into the agent context, the same way
``/<skill-name>`` does — but for multiple skills at once.

Storage
-------
Bundles live in ``~/.bauer/skill-bundles/*.yaml``.  Each file::

    name: backend-dev
    description: "Backend feature work — code review, testing, PR workflow."
    skills:
      - code-review
      - tdd
      - git-workflow
    instruction: |
      Optional extra guidance injected above the skill bodies.

Public API
----------
- :class:`SkillBundle`
- :class:`SkillBundleManager`
- :func:`get_default_bundle_manager`
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_SLUG_INVALID = re.compile(r"[^a-z0-9-]")
_SLUG_MULTI_HYPHEN = re.compile(r"-{2,}")


def _slugify(name: str) -> str:
    slug = name.lower().replace(" ", "-").replace("_", "-")
    slug = _SLUG_INVALID.sub("", slug)
    slug = _SLUG_MULTI_HYPHEN.sub("-", slug)
    return slug.strip("-")


@dataclass
class SkillBundle:
    name: str
    description: str = ""
    skills: list[str] = field(default_factory=list)
    instruction: str = ""

    @property
    def slug(self) -> str:
        return _slugify(self.name)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.description:
            d["description"] = self.description
        d["skills"] = list(self.skills)
        if self.instruction:
            d["instruction"] = self.instruction
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SkillBundle":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            skills=list(data.get("skills") or []),
            instruction=data.get("instruction", ""),
        )


class SkillBundleManager:
    """CRUD manager for skill bundles stored on disk."""

    def __init__(self, bundles_dir: str | Path | None = None) -> None:
        if bundles_dir is None:
            self._dir = Path.home() / ".bauer" / "skill-bundles"
        else:
            self._dir = Path(bundles_dir)

    def _path(self, slug: str) -> Path:
        return self._dir / f"{slug}.yaml"

    def _load_file(self, path: Path) -> SkillBundle | None:
        try:
            text = path.read_text(encoding="utf-8")
            if _HAS_YAML:
                data = _yaml.safe_load(text) or {}
            else:
                data = _parse_simple_yaml(text)
            if not data.get("name"):
                data["name"] = path.stem
            return SkillBundle.from_dict(data)
        except Exception:
            return None

    def list_bundles(self) -> list[SkillBundle]:
        if not self._dir.exists():
            return []
        bundles = []
        for p in sorted(self._dir.glob("*.yaml")):
            b = self._load_file(p)
            if b:
                bundles.append(b)
        return bundles

    def get(self, name: str) -> SkillBundle | None:
        slug = _slugify(name)
        path = self._path(slug)
        if path.exists():
            return self._load_file(path)
        # Try matching by name in all bundles
        for b in self.list_bundles():
            if _slugify(b.name) == slug:
                return b
        return None

    def save(self, bundle: SkillBundle) -> Path:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(bundle.slug)
        data = bundle.to_dict()
        if _HAS_YAML:
            text = _yaml.dump(data, allow_unicode=True, default_flow_style=False)
        else:
            lines = [f"name: {data['name']}"]
            if data.get("description"):
                lines.append(f"description: {data['description']}")
            lines.append("skills:")
            for s in data.get("skills", []):
                lines.append(f"  - {s}")
            if data.get("instruction"):
                lines.append(f"instruction: |\n  {data['instruction']}")
            text = "\n".join(lines) + "\n"
        path.write_text(text, encoding="utf-8")
        return path

    def delete(self, name: str) -> bool:
        slug = _slugify(name)
        path = self._path(slug)
        if path.exists():
            path.unlink()
            return True
        return False

    def resolve_bundle(
        self,
        name: str,
        skill_manager=None,
    ) -> str | None:
        """Return combined skill content for a bundle, or None if not found."""
        bundle = self.get(name)
        if bundle is None:
            return None

        if skill_manager is None:
            from .skill_system import get_default_manager
            skill_manager = get_default_manager()

        parts: list[str] = []
        if bundle.instruction:
            parts.append(bundle.instruction.strip())

        for slug in bundle.skills:
            try:
                skill = skill_manager.get(slug)
                if skill:
                    parts.append(f"## Skill: {skill.name}\n\n{skill.render()}")
                else:
                    parts.append(f"## Skill: {slug}\n\n[não encontrada]")
            except Exception:
                parts.append(f"## Skill: {slug}\n\n[não encontrada]")

        if not parts:
            return None
        return "\n\n---\n\n".join(parts)


def get_default_bundle_manager() -> SkillBundleManager:
    return SkillBundleManager()


# ---------------------------------------------------------------------------
# Minimal YAML parser fallback (no pyyaml)
# ---------------------------------------------------------------------------

def _parse_simple_yaml(text: str) -> dict:
    """Very limited YAML parser for bundle files — handles flat keys and lists."""
    result: dict = {}
    lines = text.splitlines()
    i = 0
    current_list_key: str | None = None
    current_list: list = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if stripped.endswith(":") and not stripped.startswith("-"):
            if current_list_key:
                result[current_list_key] = current_list
                current_list_key = None
                current_list = []
            key = stripped[:-1].strip()
            current_list_key = key
            current_list = []
            i += 1
            continue
        if stripped.startswith("- ") and current_list_key:
            current_list.append(stripped[2:].strip())
            i += 1
            continue
        if ":" in stripped and not stripped.startswith("-"):
            if current_list_key and current_list:
                result[current_list_key] = current_list
                current_list_key = None
                current_list = []
            key, _, val = stripped.partition(":")
            result[key.strip()] = val.strip().strip('"').strip("'")
        i += 1
    if current_list_key and current_list:
        result[current_list_key] = current_list
    return result
