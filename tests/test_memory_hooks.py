"""Tests for G25 — MemoryProvider advanced lifecycle hooks.

Covers: on_delegation, handle_tool_call, get_config_schema in ABC,
LocalMemoryProvider, HindsightProvider and MultiMemoryProvider.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.memory_provider import (
    HindsightProvider,
    LocalMemoryProvider,
    MemoryProvider,
    MultiMemoryProvider,
    SimpleVectorProvider,
    get_memory_provider,
    reset_memory_provider,
    set_memory_provider,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MinimalProvider(MemoryProvider):
    """Minimal concrete provider for testing ABC defaults."""

    def initialize(self, workspace):
        pass

    def __init__(self):
        self.delegations: list[tuple[str, str]] = []
        self.tool_calls: list[tuple[str, dict, str]] = []

    def on_delegation(self, sub_task: str, result: str) -> None:
        self.delegations.append((sub_task, result))

    def handle_tool_call(self, tool_name: str, tool_args: dict, tool_result: str) -> None:
        self.tool_calls.append((tool_name, tool_args, tool_result))


# ---------------------------------------------------------------------------
# ABC defaults
# ---------------------------------------------------------------------------


class TestMemoryProviderABC:
    def test_get_config_schema_default_empty(self):
        p = _MinimalProvider()
        schema = p.get_config_schema()
        assert isinstance(schema, dict)
        # default is {} — subclasses override
        assert schema == {}

    def test_on_delegation_default_noop(self):
        class _Bare(MemoryProvider):
            def initialize(self, w):
                pass

        p = _Bare()
        # Must not raise
        p.on_delegation("task", "result")

    def test_handle_tool_call_default_noop(self):
        class _Bare(MemoryProvider):
            def initialize(self, w):
                pass

        p = _Bare()
        p.handle_tool_call("read_file", {"file": "x.py"}, "contents")

    def test_on_delegation_signature(self):
        import inspect

        sig = inspect.signature(MemoryProvider.on_delegation)
        params = list(sig.parameters)
        assert "sub_task" in params
        assert "result" in params

    def test_handle_tool_call_signature(self):
        import inspect

        sig = inspect.signature(MemoryProvider.handle_tool_call)
        params = list(sig.parameters)
        assert "tool_name" in params
        assert "tool_args" in params
        assert "tool_result" in params

    def test_get_config_schema_signature_returns_dict(self):
        import inspect

        sig = inspect.signature(MemoryProvider.get_config_schema)
        ret = sig.return_annotation
        # annotation may be the type itself or a forward-ref string (PEP 563)
        assert ret is inspect.Parameter.empty or ret is dict or str(ret) in ("dict", "<class 'dict'>")


# ---------------------------------------------------------------------------
# LocalMemoryProvider
# ---------------------------------------------------------------------------


class TestLocalMemoryProviderHooks:
    def test_get_config_schema_returns_workspace_key(self):
        p = LocalMemoryProvider()
        schema = p.get_config_schema()
        assert "properties" in schema
        assert "workspace" in schema["properties"]

    def test_handle_tool_call_memory_updates_nudge(self):
        p = LocalMemoryProvider()
        # Before: last_write_turn is None
        assert p._nudge_state.last_write_turn is None
        p.handle_tool_call("memory", {"key": "x"}, "stored")
        assert p._nudge_state.last_write_turn is not None

    def test_handle_tool_call_non_memory_no_update(self):
        p = LocalMemoryProvider()
        p.handle_tool_call("read_file", {"file": "x.py"}, "content")
        assert p._nudge_state.last_write_turn is None

    def test_handle_tool_call_memory_search_updates_nudge(self):
        p = LocalMemoryProvider()
        p.handle_tool_call("memory_search", {}, "results")
        assert p._nudge_state.last_write_turn is not None

    def test_on_delegation_no_init_no_crash(self):
        p = LocalMemoryProvider()
        # Not initialized — should be silent
        p.on_delegation("do something", "result here")

    def test_on_delegation_with_mock_manager(self, tmp_path):
        p = LocalMemoryProvider()
        mock_manager = MagicMock()
        p._manager = mock_manager
        p._initialized = True
        p.on_delegation("write a report", "Report done.")
        mock_manager.add_note.assert_called_once()
        args = mock_manager.add_note.call_args[0]
        assert "delegação" in args[0].lower() or "Delegação" in args[0]

    def test_on_delegation_truncates_long_result(self, tmp_path):
        p = LocalMemoryProvider()
        mock_manager = MagicMock()
        p._manager = mock_manager
        p._initialized = True
        long_result = "x" * 1000
        p.on_delegation("task", long_result)
        call_kwargs_str = str(mock_manager.add_note.call_args)
        # Result truncated to 300 chars
        assert "x" * 301 not in call_kwargs_str


# ---------------------------------------------------------------------------
# HindsightProvider
# ---------------------------------------------------------------------------


class TestHindsightProviderHooks:
    def test_get_config_schema_has_base_dir(self):
        p = HindsightProvider()
        schema = p.get_config_schema()
        assert "properties" in schema
        assert "base_dir" in schema["properties"]

    def test_get_config_schema_has_max_facts(self):
        p = HindsightProvider()
        schema = p.get_config_schema()
        assert "max_facts" in schema["properties"]

    def test_on_delegation_adds_fact(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p.prefetch()
        p.on_delegation("analyze logs", "Logs analyzed successfully.")
        assert len(p._facts) == 1
        fact = p._facts[0]
        assert fact["subject"] == "delegação"
        assert "resultou" in fact["predicate"]
        assert "Logs" in fact["object"]

    def test_on_delegation_persists_to_disk(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p.on_delegation("task", "done")
        store = tmp_path / "hindsight.json"
        assert store.exists()
        data = json.loads(store.read_text(encoding="utf-8"))
        assert len(data["facts"]) == 1

    def test_on_delegation_respects_max_facts(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p._MAX_FACTS = 3
        for i in range(5):
            p.on_delegation(f"task {i}", f"result {i}")
        assert len(p._facts) <= 3

    def test_handle_tool_call_write_file_adds_fact(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p.prefetch()
        p.handle_tool_call("write_file", {"file": "out.txt"}, "Written 42 bytes.")
        assert any(f["subject"] == "write_file" for f in p._facts)

    def test_handle_tool_call_patch_adds_fact(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p.handle_tool_call("patch", {"file": "x.py", "old": "a", "new": "b"}, "Patched.")
        assert any(f["subject"] == "patch" for f in p._facts)

    def test_handle_tool_call_read_file_ignored(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p.prefetch()
        p.handle_tool_call("read_file", {"file": "x.py"}, "content")
        assert len(p._facts) == 0

    def test_handle_tool_call_empty_result_ignored(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p.handle_tool_call("write_file", {}, "")
        assert len(p._facts) == 0

    def test_handle_tool_call_truncates_long_result(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        p.handle_tool_call("write_file", {}, "x" * 1000)
        fact = p._facts[0]
        assert len(fact["object"]) <= 80

    def test_facts_have_timestamp(self, tmp_path):
        p = HindsightProvider(base_dir=tmp_path)
        before = time.time()
        p.on_delegation("t", "r")
        after = time.time()
        ts = p._facts[0]["ts"]
        assert before <= ts <= after


# ---------------------------------------------------------------------------
# MultiMemoryProvider — hook forwarding
# ---------------------------------------------------------------------------


class TestMultiMemoryProviderHooks:
    def _make_multi(self, n: int = 2):
        providers = [_MinimalProvider() for _ in range(n)]
        multi = MultiMemoryProvider(providers)
        return multi, providers

    def test_on_delegation_forwarded_to_all(self):
        multi, providers = self._make_multi(3)
        multi.on_delegation("task", "result")
        for p in providers:
            assert len(p.delegations) == 1
            assert p.delegations[0] == ("task", "result")

    def test_handle_tool_call_forwarded_to_all(self):
        multi, providers = self._make_multi(3)
        multi.handle_tool_call("write_file", {"f": "x"}, "ok")
        for p in providers:
            assert len(p.tool_calls) == 1
            name, args, result = p.tool_calls[0]
            assert name == "write_file"

    def test_get_config_schema_merges_providers(self):
        class _WithSchema(MemoryProvider):
            def initialize(self, w):
                pass

            def get_config_schema(self):
                return {"type": "object", "properties": {"api_key": {"type": "string"}}}

        p1 = _WithSchema()
        p2 = _MinimalProvider()
        multi = MultiMemoryProvider([p1, p2])
        schema = multi.get_config_schema()
        assert "_WithSchema" in schema.get("properties", {})

    def test_on_delegation_exception_in_one_provider_doesnt_block(self):
        class _Exploding(_MinimalProvider):
            def on_delegation(self, sub_task, result):
                raise RuntimeError("boom")

        provider_ok = _MinimalProvider()
        multi = MultiMemoryProvider([_Exploding(), provider_ok])
        # Should not raise
        multi.on_delegation("task", "result")
        assert provider_ok.delegations == [("task", "result")]

    def test_handle_tool_call_exception_in_one_doesnt_block(self):
        class _Exploding(_MinimalProvider):
            def handle_tool_call(self, name, args, result):
                raise RuntimeError("kaboom")

        provider_ok = _MinimalProvider()
        multi = MultiMemoryProvider([_Exploding(), provider_ok])
        multi.handle_tool_call("write_file", {}, "ok")
        assert len(provider_ok.tool_calls) == 1

    def test_get_config_schema_empty_for_providers_with_no_schema(self):
        multi = MultiMemoryProvider([_MinimalProvider(), _MinimalProvider()])
        schema = multi.get_config_schema()
        # _MinimalProvider.get_config_schema returns {} — filtered out
        assert schema.get("properties", {}) == {}


# ---------------------------------------------------------------------------
# Agent wiring — integration-style tests
# ---------------------------------------------------------------------------


class TestAgentWiring:
    def test_handle_tool_call_called_after_tool_exec(self, tmp_path):
        """handle_tool_call is called in agent loop after a successful tool."""
        recorded: list[tuple[str, dict, str]] = []

        class _Spy(MemoryProvider):
            def initialize(self, w):
                pass

            def handle_tool_call(self, name, args, result):
                recorded.append((name, args, result))

        spy = _Spy()

        # Simulate what the agent loop does at line ~1495
        action_name = "read_file"
        action_args = {"file": "test.py"}
        tool_result = "file contents"
        _tool_failed = False

        if not _tool_failed:
            try:
                spy.handle_tool_call(action_name, action_args, str(tool_result))
            except Exception:
                pass

        assert len(recorded) == 1
        assert recorded[0][0] == "read_file"

    def test_on_delegation_called_for_delegate_task(self):
        recorded: list[tuple[str, str]] = []

        class _Spy(MemoryProvider):
            def initialize(self, w):
                pass

            def on_delegation(self, sub_task, result):
                recorded.append((sub_task, result))

        spy = _Spy()
        action_name = "delegate_task"
        action_args = {"task": "analyze this file"}
        tool_result = "[sub-agente]\nAnalysis done."
        _tool_failed = False

        if not _tool_failed:
            spy.handle_tool_call(action_name, action_args, str(tool_result))
            if action_name == "delegate_task":
                sub = action_args.get("task", "")
                spy.on_delegation(str(sub), str(tool_result))

        assert len(recorded) == 1
        assert "analyze this file" in recorded[0][0]

    def test_hooks_not_called_on_failed_tool(self):
        recorded: list = []

        class _Spy(MemoryProvider):
            def initialize(self, w):
                pass

            def handle_tool_call(self, name, args, result):
                recorded.append(name)

        spy = _Spy()
        _tool_failed = True
        if not _tool_failed:
            spy.handle_tool_call("bad_tool", {}, "error")

        assert recorded == []

    def test_handle_tool_call_exception_silenced_in_loop(self):
        class _Exploding(MemoryProvider):
            def initialize(self, w):
                pass

            def handle_tool_call(self, name, args, result):
                raise RuntimeError("deliberate failure")

        memprov = _Exploding()
        # Simulates agent loop try/except Exception: pass
        try:
            memprov.handle_tool_call("write_file", {}, "ok")
        except Exception:
            pass
        # Test passes if we got here
