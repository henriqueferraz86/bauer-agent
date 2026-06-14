"""Tests for bauer.lazy_imports."""

from __future__ import annotations

import sys
from importlib import import_module
from unittest.mock import patch

import pytest

from bauer.lazy_imports import require, is_available


# ---------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------

class TestRequire:
    def test_available_module_returns_module(self):
        mod = require("pathlib")
        from pathlib import Path  # known installed
        assert hasattr(mod, "Path")

    def test_missing_module_raises_import_error(self):
        with pytest.raises(ImportError, match="bauer_nonexistent_pkg_xyz"):
            require("bauer_nonexistent_pkg_xyz")

    def test_install_hint_in_error_message(self):
        with pytest.raises(ImportError, match="pip install some-pkg"):
            require("bauer_nonexistent_xyz2", "pip install some-pkg")

    def test_no_hint_still_raises_import_error(self):
        with pytest.raises(ImportError):
            require("totally_fake_module_12345")

    def test_attr_extracts_nested_attribute(self):
        path_cls = require("pathlib", attr="Path")
        from pathlib import Path
        assert path_cls is Path

    def test_attr_dotted_path(self):
        # os.path is a dotted submodule attribute
        posix_or_nt = require("os", attr="path")
        import os
        assert posix_or_nt is os.path

    def test_missing_module_error_message_contains_package_name(self):
        pkg = "bauer_definitely_not_real_abc"
        with pytest.raises(ImportError) as exc_info:
            require(pkg)
        assert pkg in str(exc_info.value)

    def test_returns_same_object_as_import_module(self):
        mod = require("json")
        import json
        assert mod is json


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------

class TestIsAvailable:
    def test_standard_library_available(self):
        assert is_available("json") is True
        assert is_available("pathlib") is True
        assert is_available("collections") is True

    def test_missing_package_returns_false(self):
        assert is_available("bauer_fake_package_999") is False

    def test_already_imported_module_returns_true(self):
        import os  # noqa: F401 — ensure it's in sys.modules
        assert is_available("os") is True

    def test_does_not_raise_for_missing(self):
        # Must return False, not raise
        result = is_available("definitely_not_installed_xqz")
        assert result is False


# ---------------------------------------------------------------------------
# Named shortcuts
# ---------------------------------------------------------------------------

class TestNamedShortcuts:
    def test_require_anthropic_returns_module_when_installed(self):
        pytest.importorskip("anthropic")
        from bauer.lazy_imports import require_anthropic
        mod = require_anthropic()
        assert mod.__name__ == "anthropic"

    def test_require_anthropic_raises_when_missing(self):
        from bauer.lazy_imports import require_anthropic
        with patch.dict("sys.modules", {"anthropic": None}):
            with pytest.raises(ImportError, match="anthropic"):
                require_anthropic()

    def test_require_fastapi_returns_module_when_installed(self):
        pytest.importorskip("fastapi")
        from bauer.lazy_imports import require_fastapi
        mod = require_fastapi()
        assert mod.__name__ == "fastapi"

    def test_require_openai_raises_friendly_error_when_missing(self):
        from bauer.lazy_imports import require_openai
        with patch.dict("sys.modules", {"openai": None}):
            with pytest.raises(ImportError, match="openai"):
                require_openai()

    def test_require_pyyaml_uses_yaml_import_name(self):
        pytest.importorskip("yaml")
        from bauer.lazy_imports import require_pyyaml
        mod = require_pyyaml()
        assert mod.__name__ == "yaml"

    def test_require_playwright_raises_friendly_error_when_missing(self):
        from bauer.lazy_imports import require_playwright
        with patch.dict("sys.modules", {"playwright": None}):
            with pytest.raises(ImportError, match="playwright"):
                require_playwright()

    def test_require_websockets_raises_friendly_error_when_missing(self):
        from bauer.lazy_imports import require_websockets
        with patch.dict("sys.modules", {"websockets": None}):
            with pytest.raises(ImportError, match="websockets"):
                require_websockets()
