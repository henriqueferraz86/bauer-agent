"""Skill system — YAML-based composable skill definitions for Bauer Agent.

Skills are declarative prompt fragments, tool sequences, or workflow templates
that can be installed from local files, directories, or remote URLs and then
invoked via the CLI or agent.

A skill is a YAML file with the following schema::

    name: summarise_code
    version: "1.0"
    description: Summarise source code into a compact design document.
    tags: [code, documentation]
    author: bauer
    invoke: |
        Read the file {path} and produce a structured summary covering:
        purpose, public API, dependencies, and notable patterns.
        Output as markdown with headers.
    tools: [read_file, glob_files]            # optional — tools required
    model: claude-3-5-haiku-20241022          # optional — preferred model
    params:                                   # optional named parameters
      path:
        description: File or directory to summarise.
        required: true

Usage::

    from bauer.skill_system import SkillManager

    manager = SkillManager()
    manager.install_from_file("/path/to/summarise_code.yaml")

    skill = manager.get("summarise_code")
    prompt = skill.render({"path": "bauer/agent.py"})

Or via CLI::

    bauer skill install summarise_code.yaml
    bauer skill list
    bauer skill show summarise_code
    bauer skill remove summarise_code
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SkillError(Exception):
    """Base error for skill system operations."""


class SkillNotFound(SkillError):
    """Raised when a skill name cannot be resolved."""


class SkillValidationError(SkillError):
    """Raised when a skill YAML fails validation."""


class SkillAlreadyExists(SkillError):
    """Raised on install when skill already exists and force=False."""


# ---------------------------------------------------------------------------
# Skill dataclass
# ---------------------------------------------------------------------------


@dataclass
class SkillParam:
    """One named parameter in a skill definition."""

    description: str = ""
    required: bool = False
    default: str = ""


@dataclass
class Skill:
    """One installed skill.

    Attributes
    ----------
    name:       Unique identifier (slug format: ``[a-z0-9_-]+``).
    version:    Semver-like version string.
    description: Human-readable one-liner.
    invoke:     Prompt template (use ``{param}`` placeholders).
    tags:       Free-form categorisation tags.
    author:     Creator of the skill.
    tools:      List of tool names this skill requires/prefers.
    model:      Preferred model identifier (optional).
    params:     Named parameter definitions.
    installed_at: Unix timestamp.
    source:     Original file / URL this skill was installed from.
    """

    name: str
    description: str = ""
    version: str = "1.0"
    invoke: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    tools: list[str] = field(default_factory=list)
    model: str = ""
    params: dict[str, SkillParam] = field(default_factory=dict)
    installed_at: float = field(default_factory=time.time)
    source: str = ""

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def render(self, values: dict[str, str] | None = None) -> str:
        """Render the invoke template with the given parameter values.

        Missing required parameters raise :exc:`SkillError`.
        Missing optional parameters use their defaults.
        """
        provided = values or {}
        resolved: dict[str, str] = {}

        for pname, pdef in self.params.items():
            if pname in provided:
                resolved[pname] = str(provided[pname])
            elif pdef.required:
                raise SkillError(
                    f"Skill '{self.name}': required parameter '{pname}' not provided. "
                    f"Description: {pdef.description}"
                )
            else:
                resolved[pname] = pdef.default

        # Also pass through any extra values not declared in params
        for k, v in provided.items():
            if k not in resolved:
                resolved[k] = str(v)

        try:
            return self.invoke.format(**resolved)
        except KeyError as exc:
            raise SkillError(
                f"Skill '{self.name}': unknown placeholder {exc} in invoke template."
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Convert SkillParam dicts back to plain dicts
        d["params"] = {
            k: {"description": v["description"], "required": v["required"], "default": v["default"]}
            for k, v in d["params"].items()
        }
        return d

    def to_yaml_str(self) -> str:
        """Return a human-readable YAML representation (no pyyaml dependency)."""
        lines = [
            f"name: {self.name}",
            f"version: \"{self.version}\"",
            f"description: {self.description}",
            f"author: {self.author}",
        ]
        if self.tags:
            lines.append(f"tags: [{', '.join(self.tags)}]")
        if self.tools:
            lines.append(f"tools: [{', '.join(self.tools)}]")
        if self.model:
            lines.append(f"model: {self.model}")
        if self.params:
            lines.append("params:")
            for pname, pdef in self.params.items():
                lines.append(f"  {pname}:")
                lines.append(f"    description: {pdef.description}")
                lines.append(f"    required: {str(pdef.required).lower()}")
                if pdef.default:
                    lines.append(f"    default: {pdef.default}")
        lines.append("invoke: |")
        for iline in self.invoke.splitlines():
            lines.append(f"    {iline}")
        return "\n".join(lines) + "\n"

    def summary(self) -> str:
        """One-line summary for list display."""
        tags_str = f" [{', '.join(self.tags)}]" if self.tags else ""
        return f"{self.name} v{self.version}{tags_str} — {self.description}"


# ---------------------------------------------------------------------------
# Parsing helpers (no pyyaml required — simple YAML parser)
# ---------------------------------------------------------------------------


def _parse_yaml_simple(text: str) -> dict[str, Any]:
    """Parse a subset of YAML sufficient for skill files.

    Handles: scalars, quoted strings, block scalars (|), lists, nested maps.
    Falls back to ``json`` for JSON payloads.
    Does NOT handle: anchors, aliases, multi-document, complex types.
    """
    try:
        import yaml  # prefer pyyaml when available
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    # Minimal YAML parser
    lines = text.splitlines()
    return _parse_yaml_lines(lines, 0)[0]


def _parse_yaml_lines(lines: list[str], start: int, indent: int = 0) -> tuple[Any, int]:
    """Recursive minimal YAML parser. Returns (value, next_line_index)."""
    result: dict[str, Any] | list[Any] | None = None
    i = start

    while i < len(lines):
        raw = lines[i]
        stripped = raw.rstrip()
        if not stripped or stripped.lstrip().startswith("#"):
            i += 1
            continue

        cur_indent = len(raw) - len(raw.lstrip())
        if cur_indent < indent:
            break  # dedent — caller handles

        if stripped.lstrip().startswith("- "):
            # List item
            if not isinstance(result, list):
                result = []
            item_text = stripped.lstrip()[2:].strip()
            if item_text:
                result.append(_parse_scalar(item_text))
            i += 1
            continue

        if ":" in stripped:
            key_part, _, val_part = stripped.lstrip().partition(":")
            key = key_part.strip()
            val = val_part.strip()

            if not isinstance(result, dict):
                result = {}

            if val == "|" or val == ">":
                # Block scalar — collect indented lines
                block_indent: int | None = None
                block_lines: list[str] = []
                i += 1
                while i < len(lines):
                    bl = lines[i]
                    if not bl.strip():
                        block_lines.append("")
                        i += 1
                        continue
                    bi = len(bl) - len(bl.lstrip())
                    if block_indent is None:
                        block_indent = bi
                    if bi < (block_indent or 0):
                        break
                    block_lines.append(bl[(block_indent or 0):].rstrip())
                    i += 1
                result[key] = "\n".join(block_lines).rstrip() + "\n"
                continue

            if not val:
                # Nested map
                i += 1
                if i < len(lines):
                    next_raw = lines[i]
                    next_indent = len(next_raw) - len(next_raw.lstrip()) if next_raw.strip() else cur_indent
                    if next_indent > cur_indent:
                        nested, i = _parse_yaml_lines(lines, i, next_indent)
                        result[key] = nested
                        continue
                result[key] = None
                continue

            result[key] = _parse_scalar(val)
            i += 1
            continue

        i += 1

    return result or {}, i


def _parse_scalar(s: str) -> Any:
    """Parse a YAML scalar string to a Python value."""
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() == "null" or s.lower() == "~":
        return None
    # Inline list: [a, b, c]
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(x.strip()) for x in inner.split(",") if x.strip()]
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


# ---------------------------------------------------------------------------
# Skill parsing
# ---------------------------------------------------------------------------


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise SkillValidationError(
            f"Invalid skill name '{name}'. "
            "Must be lowercase alphanumeric + underscore/hyphen, starting with a letter/digit."
        )


def skill_from_dict(d: dict[str, Any], *, source: str = "") -> Skill:
    """Build a :class:`Skill` from a parsed YAML dict.

    Raises :exc:`SkillValidationError` for missing required fields.
    """
    name = str(d.get("name") or "").strip()
    if not name:
        raise SkillValidationError("Skill YAML missing required field 'name'.")
    _validate_name(name)

    invoke = str(d.get("invoke") or "").strip()
    if not invoke:
        raise SkillValidationError(f"Skill '{name}' YAML missing required field 'invoke'.")

    # Parse params
    params_raw = d.get("params") or {}
    params: dict[str, SkillParam] = {}
    if isinstance(params_raw, dict):
        for pname, pval in params_raw.items():
            if isinstance(pval, dict):
                params[pname] = SkillParam(
                    description=str(pval.get("description") or ""),
                    required=bool(pval.get("required", False)),
                    default=str(pval.get("default") or ""),
                )
            else:
                params[pname] = SkillParam(description=str(pval or ""))

    tags = d.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    tools = d.get("tools") or []
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]

    return Skill(
        name=name,
        description=str(d.get("description") or ""),
        version=str(d.get("version") or "1.0"),
        invoke=invoke,
        tags=list(tags),
        author=str(d.get("author") or ""),
        tools=list(tools),
        model=str(d.get("model") or ""),
        params=params,
        source=source,
    )


def skill_from_yaml(text: str, *, source: str = "") -> Skill:
    """Parse a skill from YAML text."""
    try:
        d = _parse_yaml_simple(text)
    except Exception as exc:
        raise SkillValidationError(f"YAML parse error: {exc}") from exc
    if not isinstance(d, dict):
        raise SkillValidationError("Skill YAML must be a mapping at the top level.")
    return skill_from_dict(d, source=source)


# ---------------------------------------------------------------------------
# SkillManager
# ---------------------------------------------------------------------------


class SkillManager:
    """Install, list, and remove skills.

    Skills are persisted as individual JSON files under ``~/.bauer/skills/``.

    Parameters
    ----------
    skills_dir:
        Directory where skill JSON files are stored.
        Defaults to ``~/.bauer/skills``.
    """

    def __init__(self, skills_dir: Path | str | None = None) -> None:
        if skills_dir is None:
            base = Path(os.environ.get("BAUER_HOME", str(Path.home() / ".bauer")))
            skills_dir = base / "skills"
        self._dir = Path(skills_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def install_from_file(self, path: Path | str, *, force: bool = False) -> Skill:
        """Install a skill from a local YAML file.

        Parameters
        ----------
        path:
            Path to the YAML file.
        force:
            Overwrite if already installed.  Raises :exc:`SkillAlreadyExists` otherwise.
        """
        p = Path(path)
        if not p.exists():
            raise SkillError(f"File not found: {p}")
        text = p.read_text(encoding="utf-8")
        skill = skill_from_yaml(text, source=str(p.resolve()))
        self._persist(skill, force=force)
        return skill

    def install_from_yaml(self, text: str, *, source: str = "", force: bool = False) -> Skill:
        """Install a skill from a YAML string.

        Parameters
        ----------
        text:
            Raw YAML skill definition.
        source:
            Human-readable origin (e.g. a URL or path).
        force:
            Overwrite if already installed.
        """
        skill = skill_from_yaml(text, source=source)
        self._persist(skill, force=force)
        return skill

    def install_from_url(self, url: str, *, force: bool = False) -> Skill:
        """Download and install a skill from a URL.

        Requires ``httpx`` or ``urllib`` (stdlib fallback).
        """
        text = self._fetch_url(url)
        skill = skill_from_yaml(text, source=url)
        self._persist(skill, force=force)
        return skill

    def install_from_directory(
        self,
        directory: Path | str,
        *,
        force: bool = False,
        pattern: str = "*.yaml",
    ) -> list[Skill]:
        """Install all skills from YAML files in a directory.

        Returns the list of successfully installed skills.
        """
        d = Path(directory)
        installed: list[Skill] = []
        for p in sorted(d.glob(pattern)):
            try:
                skill = self.install_from_file(p, force=force)
                installed.append(skill)
            except (SkillAlreadyExists, SkillValidationError):
                pass  # Skip silently; caller can set force=True
        return installed

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, name: str) -> Skill:
        """Return the installed skill with the given name.

        Raises :exc:`SkillNotFound` if not found.
        """
        p = self._skill_path(name)
        if not p.exists():
            raise SkillNotFound(
                f"Skill '{name}' is not installed. "
                f"Run: bauer skill list"
            )
        return self._load(p)

    def list_skills(
        self,
        *,
        tags: list[str] | None = None,
        query: str | None = None,
    ) -> list[Skill]:
        """Return all installed skills, optionally filtered.

        Parameters
        ----------
        tags:
            If set, only return skills with ALL of these tags.
        query:
            If set, only return skills whose name or description contain
            this string (case-insensitive).
        """
        skills: list[Skill] = []
        for p in sorted(self._dir.glob("*.json")):
            try:
                s = self._load(p)
            except Exception:
                continue
            if tags and not set(tags).issubset(set(s.tags)):
                continue
            if query:
                q = query.lower()
                if q not in s.name.lower() and q not in s.description.lower():
                    continue
            skills.append(s)
        return skills

    def exists(self, name: str) -> bool:
        """Return True if skill is installed."""
        return self._skill_path(name).exists()

    # ------------------------------------------------------------------
    # Mutate
    # ------------------------------------------------------------------

    def remove(self, name: str) -> bool:
        """Remove an installed skill.  Returns True if it existed."""
        p = self._skill_path(name)
        if p.exists():
            p.unlink()
            return True
        return False

    def update_from_yaml(self, name: str, text: str) -> Skill:
        """Replace an installed skill with new YAML content.

        Raises :exc:`SkillNotFound` if the skill doesn't exist.
        """
        if not self.exists(name):
            raise SkillNotFound(f"Skill '{name}' is not installed.")
        skill = skill_from_yaml(text, source=f"update:{name}")
        if skill.name != name:
            raise SkillError(
                f"Cannot rename skill in-place: expected '{name}', got '{skill.name}'."
            )
        self._persist(skill, force=True)
        return skill

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _skill_path(self, name: str) -> Path:
        safe_name = name.replace("/", "_").replace("\\", "_")
        return self._dir / f"{safe_name}.json"

    def _persist(self, skill: Skill, *, force: bool) -> None:
        p = self._skill_path(skill.name)
        if p.exists() and not force:
            raise SkillAlreadyExists(
                f"Skill '{skill.name}' is already installed. "
                "Use force=True to overwrite."
            )
        data = skill.to_dict()
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _load(p: Path) -> Skill:
        d = json.loads(p.read_text(encoding="utf-8"))
        params = {
            k: SkillParam(
                description=v.get("description", ""),
                required=bool(v.get("required", False)),
                default=str(v.get("default", "")),
            )
            for k, v in (d.get("params") or {}).items()
        }
        return Skill(
            name=d["name"],
            description=d.get("description", ""),
            version=d.get("version", "1.0"),
            invoke=d.get("invoke", ""),
            tags=d.get("tags") or [],
            author=d.get("author", ""),
            tools=d.get("tools") or [],
            model=d.get("model", ""),
            params=params,
            installed_at=d.get("installed_at", 0.0),
            source=d.get("source", ""),
        )

    @staticmethod
    def _fetch_url(url: str) -> str:
        try:
            import httpx
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except ImportError:
            pass
        # stdlib fallback
        import urllib.request
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return resp.read().decode("utf-8")


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

_default_manager: SkillManager | None = None


def get_default_manager() -> SkillManager:
    """Return the shared :class:`SkillManager` instance."""
    global _default_manager
    if _default_manager is None:
        _default_manager = SkillManager()
    return _default_manager
