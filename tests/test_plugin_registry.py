"""Tests for bauer.plugin_registry — plugin.yaml manifests + install_plugin."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock
import urllib.error

import pytest

from bauer.plugin_registry import (
    PluginRegistry,
    PluginInfo,
    PluginManifest,
    _load_manifest,
    install_plugin,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    d = tmp_path / "workspace" / ".bauer" / "plugins"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def registry(tmp_path: Path) -> PluginRegistry:
    return PluginRegistry(workspace=tmp_path / "workspace", user_dir=tmp_path / "user_plugins")


# ---------------------------------------------------------------------------
# _load_manifest
# ---------------------------------------------------------------------------

class TestLoadManifest:
    def test_returns_none_when_absent(self, tmp_path: Path):
        assert _load_manifest(tmp_path / "missing.yaml") is None

    def test_parses_full_manifest(self, tmp_path: Path):
        m = tmp_path / "plugin.yaml"
        m.write_text(textwrap.dedent("""\
            name: my-plugin
            version: "1.2.3"
            description: "Does something"
            entry_point: my_plugin.py
            hooks:
              - tool_post_process
              - on_session_start
            requires:
              - requests>=2.28
            author: Henri
            homepage: https://example.com
        """), encoding="utf-8")
        manifest = _load_manifest(m)
        assert manifest is not None
        assert manifest.name == "my-plugin"
        assert manifest.version == "1.2.3"
        assert manifest.description == "Does something"
        assert manifest.hooks == ["tool_post_process", "on_session_start"]
        assert manifest.requires == ["requests>=2.28"]
        assert manifest.author == "Henri"
        assert manifest.homepage == "https://example.com"

    def test_partial_manifest(self, tmp_path: Path):
        m = tmp_path / "partial.yaml"
        m.write_text("name: minimal\n", encoding="utf-8")
        manifest = _load_manifest(m)
        assert manifest is not None
        assert manifest.name == "minimal"
        assert manifest.version == "0.0.0"
        assert manifest.hooks == []

    def test_invalid_yaml_returns_none(self, tmp_path: Path):
        m = tmp_path / "bad.yaml"
        m.write_text(": : : [\n", encoding="utf-8")
        assert _load_manifest(m) is None

    def test_non_dict_yaml_returns_none(self, tmp_path: Path):
        m = tmp_path / "list.yaml"
        m.write_text("- item1\n- item2\n", encoding="utf-8")
        assert _load_manifest(m) is None


# ---------------------------------------------------------------------------
# PluginRegistry.list_plugins — manifest enrichment
# ---------------------------------------------------------------------------

class TestPluginRegistryListWithManifest:
    def test_no_plugins(self, registry: PluginRegistry):
        assert registry.list_plugins() == []

    def test_plugin_without_manifest(self, plugin_dir: Path, registry: PluginRegistry):
        (plugin_dir / "simple.py").write_text(
            '"""Simple plugin."""\nhooks.on("post_tool_call", lambda **kw: None)\n',
            encoding="utf-8"
        )
        plugins = registry.list_plugins()
        assert len(plugins) == 1
        p = plugins[0]
        assert p.name == "simple"
        assert p.enabled
        assert not p.has_manifest
        assert p.version == ""

    def test_plugin_with_adjacent_yaml(self, plugin_dir: Path, registry: PluginRegistry):
        (plugin_dir / "enriched.py").write_text('"""Enriched."""\n', encoding="utf-8")
        (plugin_dir / "enriched.yaml").write_text(
            "name: enriched\nversion: '2.0'\nauthor: Test\ndescription: Enhanced plugin\nhooks:\n  - my_hook\n",
            encoding="utf-8"
        )
        plugins = registry.list_plugins()
        assert len(plugins) == 1
        p = plugins[0]
        assert p.has_manifest
        assert p.version == "2.0"
        assert p.author == "Test"
        assert p.description == "Enhanced plugin"
        assert p.hooks == ["my_hook"]

    def test_manifest_hooks_override_ast_hooks(self, plugin_dir: Path, registry: PluginRegistry):
        (plugin_dir / "override.py").write_text(
            'hooks.on("ast_detected_hook", lambda **kw: None)\n',
            encoding="utf-8"
        )
        (plugin_dir / "override.yaml").write_text(
            "name: override\nhooks:\n  - manifest_hook\n",
            encoding="utf-8"
        )
        plugins = registry.list_plugins()
        p = plugins[0]
        assert p.hooks == ["manifest_hook"]
        assert "ast_detected_hook" not in p.hooks

    def test_disabled_plugin_starts_with_underscore(self, plugin_dir: Path, registry: PluginRegistry):
        (plugin_dir / "_disabled.py").write_text('"""Disabled."""\n', encoding="utf-8")
        plugins = registry.list_plugins()
        p = plugins[0]
        assert not p.enabled

    def test_parse_error_captured(self, plugin_dir: Path, registry: PluginRegistry):
        (plugin_dir / "broken.py").write_text("def (\n", encoding="utf-8")
        plugins = registry.list_plugins()
        p = plugins[0]
        assert p.error != ""
        assert not p.enabled


# ---------------------------------------------------------------------------
# install_plugin
# ---------------------------------------------------------------------------

class TestInstallPlugin:
    def _mock_urlopen(self, content: bytes):
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        ctx.read = MagicMock(return_value=content)
        return ctx

    def test_installs_py_file(self, tmp_path: Path):
        dest = tmp_path / "plugins"
        url = "https://example.com/my_plugin.py"
        py_content = b'"""My plugin."""\n'

        call_count = [0]
        def fake_urlopen(req, timeout=30):
            call_count[0] += 1
            if call_count[0] == 1:
                return self._mock_urlopen(py_content)
            raise urllib.error.URLError("no manifest")

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            py_path, manifest_path = install_plugin(url, dest)

        assert py_path.exists()
        assert py_path.name == "my_plugin.py"
        assert py_path.read_bytes() == py_content
        assert manifest_path is None  # no manifest at adjacent URL

    def test_installs_py_and_manifest(self, tmp_path: Path):
        dest = tmp_path / "plugins"
        url = "https://example.com/good_plugin.py"
        py_content = b'"""Good plugin."""\n'
        manifest_content = b"name: good-plugin\nversion: '1.0'\n"

        call_count = [0]
        def fake_urlopen(req, timeout=30):
            call_count[0] += 1
            if "plugin.yaml" in req.full_url:
                return self._mock_urlopen(manifest_content)
            return self._mock_urlopen(py_content)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with patch("urllib.request.Request", side_effect=lambda u, headers=None: MagicMock(full_url=u)):
                py_path, manifest_path = install_plugin(url, dest)

        assert py_path.exists()
        assert manifest_path is not None and manifest_path.exists()

    def test_invalid_url_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="http"):
            install_plugin("file:///etc/passwd", tmp_path)

    def test_non_py_url_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match=r"\.py"):
            install_plugin("https://example.com/script.sh", tmp_path)

    def test_network_error_propagates(self, tmp_path: Path):
        url = "https://example.com/my_plugin.py"
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            with pytest.raises(urllib.error.URLError):
                install_plugin(url, tmp_path)

    def test_creates_dest_dir(self, tmp_path: Path):
        dest = tmp_path / "new" / "nested" / "dir"
        url = "https://example.com/p.py"
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = self._mock_urlopen(b"# plugin\n")
            install_plugin(url, dest)
        assert dest.exists()

    def test_url_without_py_extension_gets_py(self, tmp_path: Path):
        url = "https://example.com/myplugin"
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.return_value = self._mock_urlopen(b"# code\n")
            py_path, _ = install_plugin(url, tmp_path)
        assert py_path.name == "myplugin.py"


# ---------------------------------------------------------------------------
# install_dir
# ---------------------------------------------------------------------------

class TestInstallDir:
    def test_install_dir_is_workspace_plugins(self, registry: PluginRegistry, tmp_path: Path):
        expected = (tmp_path / "workspace" / ".bauer" / "plugins").resolve()
        assert registry.install_dir().resolve() == expected
