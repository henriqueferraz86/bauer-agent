"""Tests for G5 — MultiMemoryProvider + load_memory_providers plugin discovery."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bauer.memory_provider import (
    LocalMemoryProvider,
    MemoryProvider,
    MultiMemoryProvider,
)
from bauer.plugin_hooks import load_memory_providers


# ─── helpers ──────────────────────────────────────────────────────────────────

def _mock_provider(
    *,
    block: str = "",
    schemas: list | None = None,
    nudge: bool = False,
) -> MagicMock:
    p = MagicMock(spec=MemoryProvider)
    p.system_prompt_block.return_value = block
    p.get_tool_schemas.return_value = schemas or []
    p.should_nudge.return_value = nudge
    p.nudge_message.return_value = "remember to write memory"
    return p


# ─── MultiMemoryProvider ──────────────────────────────────────────────────────

class TestMultiMemoryProvider:
    def test_initialize_calls_all(self, tmp_path):
        p1, p2 = _mock_provider(), _mock_provider()
        multi = MultiMemoryProvider([p1, p2])
        multi.initialize(tmp_path)
        p1.initialize.assert_called_once_with(tmp_path)
        p2.initialize.assert_called_once_with(tmp_path)

    def test_prefetch_calls_all(self):
        p1, p2 = _mock_provider(), _mock_provider()
        multi = MultiMemoryProvider([p1, p2])
        multi.prefetch()
        p1.prefetch.assert_called_once()
        p2.prefetch.assert_called_once()

    def test_sync_turn_calls_all(self):
        p1, p2 = _mock_provider(), _mock_provider()
        multi = MultiMemoryProvider([p1, p2])
        msgs = [{"role": "user", "content": "hi"}]
        multi.sync_turn(1, msgs)
        p1.sync_turn.assert_called_once_with(1, msgs)
        p2.sync_turn.assert_called_once_with(1, msgs)

    def test_on_session_end_calls_all(self):
        p1, p2 = _mock_provider(), _mock_provider()
        multi = MultiMemoryProvider([p1, p2])
        multi.on_session_end([])
        p1.on_session_end.assert_called_once()
        p2.on_session_end.assert_called_once()

    def test_on_pre_compress_calls_all(self):
        p1, p2 = _mock_provider(), _mock_provider()
        multi = MultiMemoryProvider([p1, p2])
        multi.on_pre_compress([])
        p1.on_pre_compress.assert_called_once()
        p2.on_pre_compress.assert_called_once()

    def test_on_memory_write_calls_all(self):
        p1, p2 = _mock_provider(), _mock_provider()
        multi = MultiMemoryProvider([p1, p2])
        multi.on_memory_write("key", "val")
        p1.on_memory_write.assert_called_once_with("key", "val")
        p2.on_memory_write.assert_called_once_with("key", "val")

    def test_system_prompt_block_concatenates(self):
        p1 = _mock_provider(block="Block 1")
        p2 = _mock_provider(block="Block 2")
        multi = MultiMemoryProvider([p1, p2])
        result = multi.system_prompt_block()
        assert "Block 1" in result
        assert "Block 2" in result

    def test_system_prompt_block_empty_providers(self):
        p1 = _mock_provider(block="")
        multi = MultiMemoryProvider([p1])
        assert multi.system_prompt_block() == ""

    def test_system_prompt_block_respects_char_limit(self):
        long_block = "x" * 5000
        p1 = _mock_provider(block=long_block)
        p2 = _mock_provider(block=long_block)
        multi = MultiMemoryProvider([p1, p2])
        result = multi.system_prompt_block()
        assert len(result) <= MultiMemoryProvider._MAX_BLOCK_CHARS + 5  # separator tolerance

    def test_get_tool_schemas_merges(self):
        schema1 = {"function": {"name": "tool_a"}, "type": "function"}
        schema2 = {"function": {"name": "tool_b"}, "type": "function"}
        p1 = _mock_provider(schemas=[schema1])
        p2 = _mock_provider(schemas=[schema2])
        multi = MultiMemoryProvider([p1, p2])
        result = multi.get_tool_schemas()
        names = {s["function"]["name"] for s in result}
        assert names == {"tool_a", "tool_b"}

    def test_get_tool_schemas_deduplicates(self):
        schema = {"function": {"name": "tool_a"}, "type": "function"}
        p1 = _mock_provider(schemas=[schema])
        p2 = _mock_provider(schemas=[schema])
        multi = MultiMemoryProvider([p1, p2])
        result = multi.get_tool_schemas()
        assert len(result) == 1

    def test_should_nudge_true_if_any_fires(self):
        p1 = _mock_provider(nudge=False)
        p2 = _mock_provider(nudge=True)
        multi = MultiMemoryProvider([p1, p2])
        assert multi.should_nudge(10) is True

    def test_should_nudge_false_if_none_fires(self):
        p1 = _mock_provider(nudge=False)
        p2 = _mock_provider(nudge=False)
        multi = MultiMemoryProvider([p1, p2])
        assert multi.should_nudge(10) is False

    def test_nudge_message_from_first_provider(self):
        p1 = _mock_provider()
        p1.nudge_message.return_value = "nudge from p1"
        multi = MultiMemoryProvider([p1])
        assert multi.nudge_message() == "nudge from p1"

    def test_error_in_one_provider_doesnt_block_others(self, tmp_path):
        bad = MagicMock(spec=MemoryProvider)
        bad.initialize.side_effect = RuntimeError("init failed")
        good = _mock_provider()
        multi = MultiMemoryProvider([bad, good])
        multi.initialize(tmp_path)
        good.initialize.assert_called_once()

    def test_empty_providers_list(self):
        multi = MultiMemoryProvider([])
        assert multi.system_prompt_block() == ""
        assert multi.get_tool_schemas() == []
        assert multi.should_nudge(5) is False


# ─── load_memory_providers ────────────────────────────────────────────────────

class TestLoadMemoryProviders:
    def test_returns_empty_when_no_plugin_dir(self, tmp_path):
        result = load_memory_providers(plugin_dir=tmp_path / "no_plugins")
        assert result == []

    def test_discovers_concrete_subclass(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "my_provider.py").write_text(
            "from bauer.memory_provider import MemoryProvider\n"
            "from pathlib import Path\n"
            "\n"
            "class MyProvider(MemoryProvider):\n"
            "    def initialize(self, workspace): pass\n",
            encoding="utf-8",
        )
        result = load_memory_providers(plugin_dir=plugin_dir)
        assert len(result) == 1
        assert isinstance(result[0], MemoryProvider)

    def test_skips_abstract_class(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "abstract_plugin.py").write_text(
            "from bauer.memory_provider import MemoryProvider\n"
            "\n"
            "class AbstractPlugin(MemoryProvider):\n"
            "    pass  # does not implement initialize()\n",
            encoding="utf-8",
        )
        result = load_memory_providers(plugin_dir=plugin_dir)
        assert result == []

    def test_skips_broken_plugin(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "broken.py").write_text(
            "raise RuntimeError('broken plugin')\n",
            encoding="utf-8",
        )
        result = load_memory_providers(plugin_dir=plugin_dir)
        assert result == []

    def test_multiple_plugins(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        for name in ("alpha", "beta"):
            (plugin_dir / f"{name}.py").write_text(
                "from bauer.memory_provider import MemoryProvider\n"
                f"class {name.capitalize()}Provider(MemoryProvider):\n"
                "    def initialize(self, workspace): pass\n",
                encoding="utf-8",
            )
        result = load_memory_providers(plugin_dir=plugin_dir)
        assert len(result) == 2

    def test_instantiation_error_skipped(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "bad_init.py").write_text(
            "from bauer.memory_provider import MemoryProvider\n"
            "class BadInit(MemoryProvider):\n"
            "    def __init__(self): raise RuntimeError('no init')\n"
            "    def initialize(self, workspace): pass\n",
            encoding="utf-8",
        )
        result = load_memory_providers(plugin_dir=plugin_dir)
        assert result == []

    def test_does_not_include_base_memoprovider_class(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        (plugin_dir / "re_exports.py").write_text(
            "from bauer.memory_provider import MemoryProvider, LocalMemoryProvider\n",
            encoding="utf-8",
        )
        result = load_memory_providers(plugin_dir=plugin_dir)
        for r in result:
            assert type(r) is not MemoryProvider


# ─── Integration: MultiMemoryProvider wraps LocalMemoryProvider ───────────────

class TestMultiWrapsLocal:
    def test_multi_with_local_provider(self, tmp_path):
        local = LocalMemoryProvider()
        multi = MultiMemoryProvider([local])
        multi.initialize(tmp_path)
        multi.prefetch()
        block = multi.system_prompt_block()
        assert isinstance(block, str)

    def test_multi_sync_turn_does_not_raise(self, tmp_path):
        local = LocalMemoryProvider()
        multi = MultiMemoryProvider([local])
        multi.initialize(tmp_path)
        multi.sync_turn(1, [{"role": "user", "content": "hi"}])

    def test_multi_on_session_end_does_not_raise(self, tmp_path):
        local = LocalMemoryProvider()
        multi = MultiMemoryProvider([local])
        multi.initialize(tmp_path)
        multi.on_session_end([])
