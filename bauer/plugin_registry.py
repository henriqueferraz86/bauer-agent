"""Operational registry for local Bauer plugins."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PluginInfo:
    name: str
    path: str
    enabled: bool
    hooks: list[str]
    description: str = ""
    error: str = ""


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

    def _inspect(self, path: Path) -> PluginInfo:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            doc = ast.get_docstring(tree) or ""
            hooks = sorted(_find_hook_names(tree))
            return PluginInfo(
                name=path.stem,
                path=str(path.resolve()),
                enabled=not path.name.startswith("_"),
                hooks=hooks,
                description=doc.splitlines()[0][:160] if doc else "",
            )
        except Exception as exc:
            return PluginInfo(
                name=path.stem,
                path=str(path.resolve()),
                enabled=False,
                hooks=[],
                error=str(exc),
            )


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
