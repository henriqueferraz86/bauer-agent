"""Operational registry for local Bauer plugins.

Plugins are Python files (.py) optionally accompanied by a plugin.yaml manifest.

plugin.yaml format:
    name: my-plugin          # identifier (default: filename stem)
    version: "1.0.0"
    description: "Short description"
    entry_point: my_plugin.py   # py file (default: <name>.py)
    hooks:
      - tool_post_process    # override auto-detected hooks
    requires:
      - requests>=2.28       # pip-installable deps (informational)
    author: "Author Name"
    homepage: https://github.com/user/my-plugin

Installation:
    bauer plugin install <url>   # downloads .py (+ plugin.yaml if adjacent)
"""

from __future__ import annotations

import ast
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PluginManifest:
    """Parsed plugin.yaml contents."""
    name: str
    version: str = "0.0.0"
    description: str = ""
    entry_point: str = ""
    hooks: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    author: str = ""
    homepage: str = ""


@dataclass(frozen=True)
class PluginInfo:
    name: str
    path: str
    enabled: bool
    hooks: list[str]
    description: str = ""
    error: str = ""
    version: str = ""
    author: str = ""
    homepage: str = ""
    has_manifest: bool = False


def _load_manifest(manifest_path: Path) -> PluginManifest | None:
    """Parse plugin.yaml. Returns None on any failure."""
    if not manifest_path.exists():
        return None
    try:
        import yaml  # type: ignore[import]
        raw: Any = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return PluginManifest(
            name=str(raw.get("name", "")),
            version=str(raw.get("version", "0.0.0")),
            description=str(raw.get("description", "")),
            entry_point=str(raw.get("entry_point", "")),
            hooks=[str(h) for h in raw.get("hooks", [])],
            requires=[str(r) for r in raw.get("requires", [])],
            author=str(raw.get("author", "")),
            homepage=str(raw.get("homepage", "")),
        )
    except Exception:
        return None


class PluginRegistry:
    """Discovers hook plugins without importing them."""

    def __init__(self, workspace: str | Path = "workspace", user_dir: str | Path | None = None):
        self.workspace = Path(workspace).resolve()
        self.user_dir = Path(user_dir) if user_dir is not None else Path.home() / ".bauer" / "plugins"
        self.workspace_dir = self.workspace / ".bauer" / "plugins"

    def list_plugins(self) -> list[PluginInfo]:
        plugins: list[PluginInfo] = []
        seen: set[Path] = set()
        for directory in (self.workspace_dir, self.user_dir):
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.py")):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                plugins.append(self._inspect(path))
        return plugins

    def install_dir(self) -> Path:
        return self.workspace_dir

    def _inspect(self, path: Path) -> PluginInfo:
        # Try loading adjacent plugin.yaml (same stem or plain "plugin.yaml")
        manifest = (
            _load_manifest(path.with_suffix(".yaml"))
            or _load_manifest(path.parent / "plugin.yaml")
        )
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            doc = ast.get_docstring(tree) or ""
            # Manifest hooks take precedence over auto-detected hooks
            if manifest and manifest.hooks:
                hooks = sorted(manifest.hooks)
            else:
                hooks = sorted(_find_hook_names(tree))
            description = (manifest.description if manifest and manifest.description
                           else (doc.splitlines()[0][:160] if doc else ""))
            return PluginInfo(
                name=manifest.name or path.stem if manifest else path.stem,
                path=str(path.resolve()),
                enabled=not path.name.startswith("_"),
                hooks=hooks,
                description=description,
                version=manifest.version if manifest else "",
                author=manifest.author if manifest else "",
                homepage=manifest.homepage if manifest else "",
                has_manifest=manifest is not None,
            )
        except Exception as exc:
            return PluginInfo(
                name=path.stem,
                path=str(path.resolve()),
                enabled=False,
                hooks=[],
                error=str(exc),
                has_manifest=manifest is not None,
            )


def install_plugin(url: str, dest_dir: Path) -> tuple[Path, Path | None]:
    """Download a plugin .py file (and adjacent plugin.yaml) from a URL.

    Returns (py_path, manifest_path | None).
    Raises ValueError for unsupported URLs, urllib.error.URLError for network errors.
    """
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"URL deve começar com http:// ou https:// — recebido: {url!r}")

    dest_dir.mkdir(parents=True, exist_ok=True)

    # Determine filename from URL
    url_path = url.split("?")[0].rstrip("/")
    filename = url_path.split("/")[-1]
    if not filename.endswith(".py"):
        filename = filename + ".py" if "." not in filename else filename
        if not filename.endswith(".py"):
            raise ValueError(f"URL deve apontar para um arquivo .py — recebido: {url!r}")

    py_dest = dest_dir / filename
    _download(url, py_dest)

    # Try to also fetch an adjacent plugin.yaml
    manifest_dest: Path | None = None
    manifest_url = url_path.rsplit("/", 1)[0] + "/plugin.yaml"
    yaml_dest = py_dest.with_suffix(".yaml")
    try:
        _download(manifest_url, yaml_dest)
        manifest_dest = yaml_dest
    except Exception:
        # No manifest — that's fine
        if yaml_dest.exists():
            yaml_dest.unlink(missing_ok=True)

    return py_dest, manifest_dest


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "bauer-agent/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        dest.write_bytes(resp.read())


def _find_hook_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "on"
            and isinstance(func.value, ast.Name)
            and func.value.id == "hooks"
        ):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            names.add(node.args[0].value)
    return names
