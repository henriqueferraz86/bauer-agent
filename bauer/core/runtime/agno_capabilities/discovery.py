"""Static discovery of the Agno toolkit catalog installed with the SDK.

Every ``agno/tools`` module is read as *source text* and parsed with ``ast``.
Nothing from the SDK (or from any optional connector package) is imported:
listing the catalog must not execute third-party import side effects, pull
heavy extras into the server process or touch the network.

The result describes, per module, which ``Toolkit`` classes exist, which
optional imports guard it (``try: import x / except ImportError``), which
environment variables it reads and which methods it registers as tools. This
is metadata for humans and for the reviewed manifest; it never grants access
by itself.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from importlib.machinery import PathFinder
from pathlib import Path

#: Folders inside ``agno/tools`` that are SDK plumbing, not integrations.
_IGNORED_DIRS = frozenset({"models", "__pycache__"})
_PIP_HINT = re.compile(r"pip install\s+[`'\"]?([A-Za-z0-9_.\-\[\], ]+?)[`'\"]?(?:\s|$|\.|`)")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


@dataclass(frozen=True, slots=True)
class SdkToolkitModule:
    """Facts about one ``agno.tools`` module, derived without importing it."""

    id: str
    module: str
    toolkit_classes: tuple[str, ...] = ()
    optional_imports: tuple[str, ...] = ()
    pip_hints: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    #: Constructor parameter names of the toolkit class(es) (AST).
    init_args: tuple[str, ...] = ()
    parse_error: bool = False

    @property
    def is_toolkit(self) -> bool:
        return bool(self.toolkit_classes)


@dataclass(slots=True)
class _ModuleScan:
    classes: dict[str, list[str]] = field(default_factory=dict)
    optional_imports: set[str] = field(default_factory=set)
    pip_hints: set[str] = field(default_factory=set)
    env_vars: set[str] = field(default_factory=set)
    functions_by_class: dict[str, set[str]] = field(default_factory=dict)
    init_args_by_class: dict[str, set[str]] = field(default_factory=dict)


def agno_tools_dirs() -> list[Path]:
    """Return the on-disk ``agno/tools`` package directories (no import)."""
    try:
        agno_spec = PathFinder.find_spec("agno")
        if agno_spec is None or not agno_spec.submodule_search_locations:
            return []
        tools_spec = PathFinder.find_spec("agno.tools", list(agno_spec.submodule_search_locations))
        if tools_spec is None or not tools_spec.submodule_search_locations:
            return []
        return [Path(item) for item in tools_spec.submodule_search_locations if Path(item).is_dir()]
    except (ImportError, AttributeError, ValueError, OSError):
        return []


def module_spec_exists(dotted: str) -> bool:
    """Whether ``dotted`` is importable, resolving each segment via PathFinder.

    ``importlib.util.find_spec("a.b")`` imports ``a``; this walk only reads
    the filesystem so optional connectors are never executed to answer it.
    """
    parts = [part for part in dotted.split(".") if part]
    if not parts:
        return False
    try:
        spec = PathFinder.find_spec(parts[0])
        for index in range(1, len(parts)):
            if spec is None or not spec.submodule_search_locations:
                return False
            spec = PathFinder.find_spec(
                ".".join(parts[: index + 1]), list(spec.submodule_search_locations)
            )
        return spec is not None
    except (ImportError, AttributeError, ValueError, OSError):
        return False


def capability_id_for(module: str) -> str:
    """Map ``agno.tools.google.gmail`` → ``agno.google.gmail``.

    Package modules named like their package (``mcp/mcp.py``) or called
    ``toolkit`` collapse into the package id (``agno.mcp``).
    """
    rest = module.removeprefix("agno.tools.").split(".")
    if len(rest) == 2 and rest[1] in {rest[0], "toolkit"}:
        rest = rest[:1]
    return "agno." + ".".join(rest)


@lru_cache(maxsize=4)
def _discover_cached(roots: tuple[str, ...]) -> tuple[SdkToolkitModule, ...]:
    modules: list[SdkToolkitModule] = []
    for root in roots:
        base = Path(root)
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(base)
            if any(part.startswith("_") or part in _IGNORED_DIRS for part in relative.parts[:-1]):
                continue
            if path.name.startswith("_"):
                continue
            if len(relative.parts) > 2:
                # Only top-level modules and one family level are integrations.
                continue
            module = "agno.tools." + ".".join(relative.with_suffix("").parts)
            modules.append(_scan_module(path, module))
    resolved = _resolve_inherited_toolkits(modules)
    # A module and its package can collapse to the same id (``mcp/mcp.py``):
    # keep the first toolkit-bearing entry so ids stay unique.
    unique: dict[str, SdkToolkitModule] = {}
    for entry in resolved:
        current = unique.get(entry.id)
        if current is None or (not current.is_toolkit and entry.is_toolkit):
            unique[entry.id] = entry
    return tuple(sorted(unique.values(), key=lambda item: item.id))


def discover_sdk_modules() -> tuple[SdkToolkitModule, ...]:
    """Enumerate every module under ``agno/tools`` with its static facts."""
    roots = tuple(str(path) for path in agno_tools_dirs())
    if not roots:
        return ()
    return _discover_cached(roots)


def _scan_module(path: Path, module: str) -> SdkToolkitModule:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
        return SdkToolkitModule(id=capability_id_for(module), module=module, parse_error=True)
    scan = _ModuleScan()
    for node in tree.body:
        if isinstance(node, ast.Try) and _handles_import_error(node):
            for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                scan.optional_imports.update(_imported_roots(inner))
            for handler in node.handlers:
                for inner in ast.walk(handler):
                    if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                        scan.pip_hints.update(
                            hint.strip() for hint in _PIP_HINT.findall(inner.value) if hint.strip()
                        )
        elif isinstance(node, ast.ClassDef):
            scan.classes[node.name] = [_base_name(base) for base in node.bases]
            scan.functions_by_class[node.name] = _registered_functions(node)
            scan.init_args_by_class[node.name] = _init_args(node)
    for inner in ast.walk(tree):
        name = _env_var_name(inner)
        if name:
            scan.env_vars.add(name)
    scan.optional_imports.discard("agno")
    scan.optional_imports.difference_update(sys.stdlib_module_names)
    scan.pip_hints = {hint for hint in scan.pip_hints if not hint.startswith("-")}
    return SdkToolkitModule(
        id=capability_id_for(module),
        module=module,
        toolkit_classes=tuple(sorted(name for name, bases in scan.classes.items() if "Toolkit" in bases)),
        optional_imports=tuple(sorted(scan.optional_imports)),
        pip_hints=tuple(sorted(scan.pip_hints)),
        env_vars=tuple(sorted(scan.env_vars)),
        functions=tuple(
            sorted(
                {
                    fn
                    for name, bases in scan.classes.items()
                    if "Toolkit" in bases
                    for fn in scan.functions_by_class.get(name, set())
                }
            )
        ),
        init_args=tuple(
            sorted(
                {
                    arg
                    for name, bases in scan.classes.items()
                    if "Toolkit" in bases
                    for arg in scan.init_args_by_class.get(name, set())
                }
            )
        ),
    )


def _resolve_inherited_toolkits(modules: list[SdkToolkitModule]) -> list[SdkToolkitModule]:
    """Second pass: classes that inherit another module's Toolkit subclass.

    Kept intentionally shallow (direct subclass names only) — the reviewed
    manifest is the authority; this only avoids hiding a toolkit from the
    catalog because it does not inherit ``Toolkit`` literally.
    """
    known = {cls for item in modules for cls in item.toolkit_classes}
    if not known:
        return modules
    resolved: list[SdkToolkitModule] = []
    for item in modules:
        if item.is_toolkit or item.parse_error:
            resolved.append(item)
            continue
        extra = _subclasses_of(item.module, known)
        if extra:
            resolved.append(
                SdkToolkitModule(
                    id=item.id,
                    module=item.module,
                    toolkit_classes=tuple(sorted(extra)),
                    optional_imports=item.optional_imports,
                    pip_hints=item.pip_hints,
                    env_vars=item.env_vars,
                    functions=item.functions,
                )
            )
        else:
            resolved.append(item)
    return resolved


def _subclasses_of(module: str, known: set[str]) -> set[str]:
    for root in agno_tools_dirs():
        path = root.joinpath(*module.removeprefix("agno.tools.").split(".")).with_suffix(".py")
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            return set()
        return {
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef)
            and any(_base_name(base) in known for base in node.bases)
        }
    return set()


def _handles_import_error(node: ast.Try) -> bool:
    for handler in node.handlers:
        names: list[ast.expr] = []
        if isinstance(handler.type, ast.Tuple):
            names.extend(handler.type.elts)
        elif handler.type is not None:
            names.append(handler.type)
        if any(_base_name(name) in {"ImportError", "ModuleNotFoundError"} for name in names):
            return True
    return False


def _imported_roots(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Import):
        return {alias.name.split(".")[0] for alias in node.names}
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        return {node.module.split(".")[0]}
    return set()


def _base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _registered_functions(cls: ast.ClassDef) -> set[str]:
    """Methods referenced as ``self.<name>`` inside tool lists of ``__init__``."""
    methods = {item.name for item in cls.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found: set[str] = set()
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(_base_name(dec.func if isinstance(dec, ast.Call) else dec) == "tool" for dec in item.decorator_list):
                found.add(item.name)
            if item.name != "__init__":
                continue
            for inner in ast.walk(item):
                if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
                    if inner.value.id == "self" and inner.attr in methods and not inner.attr.startswith("_"):
                        found.add(inner.attr)
    return found


def _init_args(cls: ast.ClassDef) -> set[str]:
    for item in cls.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            args = item.args
            return {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs if a.arg != "self"}
    return set()


def _env_var_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        func = node.func
        name = _base_name(func)
        is_getenv = name == "getenv"
        is_environ_get = (
            name == "get"
            and isinstance(func, ast.Attribute)
            and _base_name(func.value) == "environ"
        )
        if (is_getenv or is_environ_get) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and _ENV_NAME.match(first.value):
                return first.value
    if isinstance(node, ast.Subscript) and _base_name(node.value) == "environ":
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and _ENV_NAME.match(key.value):
            return key.value
    return None
