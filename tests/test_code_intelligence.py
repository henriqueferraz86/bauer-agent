"""Tests for G7 — Code Intelligence Light (AST-based, no LSP)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bauer.code_intelligence import (
    find_symbol_definitions,
    get_call_sites,
    get_imports,
    get_python_symbols,
)


# ─── Fixture helpers ──────────────────────────────────────────────────────────

def _write_py(tmp_path: Path, name: str, content: str) -> Path:
    f = tmp_path / name
    f.write_text(content, encoding="utf-8")
    return f


_SAMPLE_PY = """\
import os
from pathlib import Path
from typing import Optional

MY_CONST: int = 42
_PRIVATE = "hidden"


class Animal:
    pass


class Dog(Animal):
    def __init__(self, name: str) -> None:
        self.name = name

    def bark(self) -> str:
        return "woof"


def greet(name: str, times: int = 1) -> str:
    return f"hello {name}" * times


async def fetch_data(url: str) -> bytes:
    return b""
"""


# ─── get_python_symbols ───────────────────────────────────────────────────────

class TestGetPythonSymbols:
    def test_finds_functions(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        names = [fn["name"] for fn in result["functions"]]
        assert "greet" in names
        assert "fetch_data" in names

    def test_function_args(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        greet = next(fn for fn in result["functions"] if fn["name"] == "greet")
        assert "name" in greet["args"]
        assert "times" in greet["args"]

    def test_async_function_flagged(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        fetch = next(fn for fn in result["functions"] if fn["name"] == "fetch_data")
        assert fetch["is_async"] is True

    def test_sync_function_not_flagged(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        greet = next(fn for fn in result["functions"] if fn["name"] == "greet")
        assert greet["is_async"] is False

    def test_finds_classes(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        names = [cls["name"] for cls in result["classes"]]
        assert "Animal" in names
        assert "Dog" in names

    def test_class_bases(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        dog = next(cls for cls in result["classes"] if cls["name"] == "Dog")
        assert "Animal" in dog["bases"]

    def test_finds_module_variables(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        names = [v["name"] for v in result["variables"]]
        assert "MY_CONST" in names
        assert "_PRIVATE" in names

    def test_line_numbers_positive(self, tmp_path):
        f = _write_py(tmp_path, "sample.py", _SAMPLE_PY)
        result = get_python_symbols(str(f))
        for fn in result["functions"]:
            assert fn["line"] >= 1
        for cls in result["classes"]:
            assert cls["line"] >= 1

    def test_syntax_error_returns_error_key(self, tmp_path):
        f = _write_py(tmp_path, "broken.py", "def (\n")
        result = get_python_symbols(str(f))
        assert "error" in result

    def test_empty_file(self, tmp_path):
        f = _write_py(tmp_path, "empty.py", "")
        result = get_python_symbols(str(f))
        assert result["functions"] == []
        assert result["classes"] == []


# ─── find_symbol_definitions ──────────────────────────────────────────────────

class TestFindSymbolDefinitions:
    def test_finds_function_in_file(self, tmp_path):
        _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        results = find_symbol_definitions("greet", str(tmp_path))
        assert any("greet" in r["signature"] for r in results)

    def test_finds_class_in_file(self, tmp_path):
        _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        results = find_symbol_definitions("Animal", str(tmp_path))
        assert len(results) >= 1
        assert results[0]["type"] == "class"

    def test_returns_file_path(self, tmp_path):
        _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        results = find_symbol_definitions("greet", str(tmp_path))
        assert all("mod.py" in r["file"] for r in results)

    def test_returns_line_number(self, tmp_path):
        _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        results = find_symbol_definitions("greet", str(tmp_path))
        assert all(r["line"] >= 1 for r in results)

    def test_not_found_returns_empty(self, tmp_path):
        _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        results = find_symbol_definitions("nonexistent_symbol_xyz", str(tmp_path))
        assert results == []

    def test_finds_across_multiple_files(self, tmp_path):
        _write_py(tmp_path, "a.py", "def my_func(): pass\n")
        _write_py(tmp_path, "b.py", "def my_func(x): return x\n")
        results = find_symbol_definitions("my_func", str(tmp_path))
        files = {r["file"] for r in results}
        assert len(files) == 2

    def test_async_function_found(self, tmp_path):
        _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        results = find_symbol_definitions("fetch_data", str(tmp_path))
        assert any("fetch_data" in r["signature"] for r in results)


# ─── get_imports ──────────────────────────────────────────────────────────────

class TestGetImports:
    def test_finds_simple_imports(self, tmp_path):
        f = _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        imports = get_imports(str(f))
        assert any("os" in imp for imp in imports)

    def test_finds_from_imports(self, tmp_path):
        f = _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        imports = get_imports(str(f))
        assert any("pathlib" in imp and "Path" in imp for imp in imports)

    def test_typing_import_found(self, tmp_path):
        f = _write_py(tmp_path, "mod.py", _SAMPLE_PY)
        imports = get_imports(str(f))
        assert any("Optional" in imp for imp in imports)

    def test_empty_file_returns_empty(self, tmp_path):
        f = _write_py(tmp_path, "empty.py", "")
        assert get_imports(str(f)) == []

    def test_syntax_error_returns_empty(self, tmp_path):
        f = _write_py(tmp_path, "broken.py", "def (\n")
        assert get_imports(str(f)) == []

    def test_relative_import(self, tmp_path):
        f = _write_py(tmp_path, "sub.py", "from . import utils\nfrom .helpers import foo\n")
        imports = get_imports(str(f))
        assert any("." in imp for imp in imports)

    def test_alias_import(self, tmp_path):
        f = _write_py(tmp_path, "alias.py", "import numpy as np\n")
        imports = get_imports(str(f))
        assert any("np" in imp for imp in imports)


# ─── get_call_sites ───────────────────────────────────────────────────────────

class TestGetCallSites:
    def test_finds_function_call(self, tmp_path):
        caller = _write_py(tmp_path, "caller.py", "from mod import greet\nresult = greet('world')\n")
        results = get_call_sites("greet", str(tmp_path))
        assert any("greet" in r["context"] for r in results)

    def test_returns_file_and_line(self, tmp_path):
        _write_py(tmp_path, "caller.py", "greet('world')\n")
        results = get_call_sites("greet", str(tmp_path))
        assert all("file" in r and "line" in r for r in results)

    def test_not_found_returns_empty(self, tmp_path):
        _write_py(tmp_path, "caller.py", "pass\n")
        results = get_call_sites("nonexistent_xyz_func", str(tmp_path))
        assert results == []

    def test_finds_across_multiple_files(self, tmp_path):
        _write_py(tmp_path, "a.py", "greet('alice')\n")
        _write_py(tmp_path, "b.py", "greet('bob')\n")
        results = get_call_sites("greet", str(tmp_path))
        files = {r["file"] for r in results}
        assert len(files) == 2

    def test_word_boundary_match(self, tmp_path):
        _write_py(tmp_path, "mod.py", "greet_extended('x')\ngreet('y')\n")
        results = get_call_sites("greet", str(tmp_path))
        # Both lines match "\bgreet\b" — greet_extended also matches on the word boundary
        assert any(r["line"] == 2 for r in results)

    def test_context_contains_line_text(self, tmp_path):
        _write_py(tmp_path, "mod.py", "x = my_func(42)\n")
        results = get_call_sites("my_func", str(tmp_path))
        assert results[0]["context"] == "x = my_func(42)"


# ─── ToolRouter integration ───────────────────────────────────────────────────

class TestToolRouterCodeIntelligence:
    def _make_router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path, audit_enabled=False)

    def test_code_symbols_via_router(self, tmp_path):
        _write_py(tmp_path, "example.py", "def hello(): pass\nclass Foo: pass\n")
        router = self._make_router(tmp_path)
        result = router.execute({"action": "code_symbols", "args": {"file": "example.py"}})
        assert "hello" in result
        assert "Foo" in result

    def test_find_definition_via_router(self, tmp_path):
        _write_py(tmp_path, "mymod.py", "def my_target_func(): pass\n")
        router = self._make_router(tmp_path)
        result = router.execute({"action": "find_definition", "args": {"symbol": "my_target_func"}})
        assert "my_target_func" in result

    def test_get_imports_via_router(self, tmp_path):
        _write_py(tmp_path, "mymod.py", "import os\nfrom pathlib import Path\n")
        router = self._make_router(tmp_path)
        result = router.execute({"action": "get_imports", "args": {"file": "mymod.py"}})
        assert "os" in result
        assert "Path" in result

    def test_find_usages_via_router(self, tmp_path):
        _write_py(tmp_path, "user.py", "my_fn()\nmy_fn(x=1)\n")
        router = self._make_router(tmp_path)
        result = router.execute({"action": "find_usages", "args": {"symbol": "my_fn"}})
        assert "my_fn" in result

    def test_code_symbols_present_in_available_tools(self, tmp_path):
        router = self._make_router(tmp_path)
        tools = router.available_tools()
        assert "code_symbols" in tools
        assert "find_definition" in tools
        assert "get_imports" in tools
        assert "find_usages" in tools
