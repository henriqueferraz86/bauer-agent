"""G7 — Code Intelligence Light.

AST-based Python symbol analysis + grep-based cross-language call-site search.
No LSP, no external dependencies beyond the stdlib.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any


# ── Public API ────────────────────────────────────────────────────────────────

def get_python_symbols(file_path: str) -> dict[str, list[dict]]:
    """Return top-level functions, classes, and module-level assigns from a .py file.

    Returns:
        {
          "functions": [{"name": str, "line": int, "args": [str], "is_async": bool}],
          "classes":   [{"name": str, "line": int, "bases": [str]}],
          "variables": [{"name": str, "line": int}],
        }
    """
    src = Path(file_path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(file_path))
    except SyntaxError as exc:
        return {"error": str(exc), "functions": [], "classes": [], "variables": []}

    functions: list[dict] = []
    classes: list[dict] = []
    variables: list[dict] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "args": [a.arg for a in node.args.args],
                "is_async": isinstance(node, ast.AsyncFunctionDef),
            })
        elif isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(f"{_attr_chain(base)}")
            classes.append({"name": node.name, "line": node.lineno, "bases": bases})
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    variables.append({"name": target.id, "line": node.lineno})
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            variables.append({"name": node.target.id, "line": node.lineno})

    return {"functions": functions, "classes": classes, "variables": variables}


def find_symbol_definitions(
    symbol: str,
    workspace: str,
    *,
    extensions: tuple[str, ...] = (".py",),
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """Find where a function or class named `symbol` is defined in the workspace.

    Returns:
        [{"file": str, "line": int, "type": str, "signature": str}]
    """
    pattern = re.compile(
        r"^(?P<indent>[ \t]*)(?P<kw>async[ \t]+def|def|class)[ \t]+" + re.escape(symbol) + r"[ \t]*[\(:]",
        re.MULTILINE,
    )
    results: list[dict] = []
    for py_file in _iter_files(workspace, extensions):
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in pattern.finditer(src):
            lineno = src[: m.start()].count("\n") + 1
            kw = m.group("kw").strip()
            sym_type = "class" if kw == "class" else "function"
            sig_end = src.find("\n", m.start())
            signature = src[m.start():sig_end].strip() if sig_end != -1 else symbol
            results.append({
                "file": str(py_file),
                "line": lineno,
                "type": sym_type,
                "signature": signature,
            })
            if len(results) >= max_results:
                return results
    return results


def get_imports(file_path: str) -> list[str]:
    """Return all import statements from a Python file as normalized strings.

    Examples:
        "import os"
        "from pathlib import Path"
        "from . import utils"
    """
    src = Path(file_path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src, filename=str(file_path))
    except SyntaxError:
        return []

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                imports.append(f"import {alias.name}" + (f" as {alias.asname}" if alias.asname else ""))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level_dots = "." * (node.level or 0)
            names = ", ".join(
                (a.name + (f" as {a.asname}" if a.asname else "")) for a in node.names
            )
            imports.append(f"from {level_dots}{module} import {names}")

    return imports


def get_call_sites(
    symbol: str,
    workspace: str,
    *,
    file_pattern: str = "*.py",
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """Find where `symbol` is called/referenced in the workspace.

    Uses a simple regex approach — works across all text-based file types.

    Returns:
        [{"file": str, "line": int, "context": str}]
    """
    call_re = re.compile(r"\b" + re.escape(symbol) + r"\b")
    results: list[dict] = []

    extensions = _extensions_from_glob(file_pattern)
    for src_file in _iter_files(workspace, extensions):
        try:
            lines = src_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines, start=1):
            if call_re.search(line):
                results.append({
                    "file": str(src_file),
                    "line": i,
                    "context": line.strip(),
                })
                if len(results) >= max_results:
                    return results
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _attr_chain(node: ast.Attribute) -> str:
    parts: list[str] = [node.attr]
    current: ast.expr = node.value
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _iter_files(workspace: str, extensions: tuple[str, ...]) -> list[Path]:
    root = Path(workspace)
    found: list[Path] = []
    for ext in extensions:
        found.extend(root.rglob(f"*{ext}"))
    # Exclude common noise directories
    return [
        p for p in found
        if not any(part.startswith(".") or part in ("__pycache__", "node_modules", ".git")
                   for part in p.parts)
    ]


def _extensions_from_glob(pattern: str) -> tuple[str, ...]:
    """Extract file extension(s) from a glob pattern like '*.py' or '**/*.ts'."""
    match = re.search(r"\*(\.[a-zA-Z0-9]+)$", pattern)
    if match:
        return (match.group(1),)
    return (".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp", ".h")
