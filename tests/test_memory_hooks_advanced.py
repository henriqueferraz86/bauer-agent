"""Tests for G32 — on_turn_start + queue_prefetch hooks in MemoryProvider."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# TestMemoryProviderABC — on_turn_start + queue_prefetch default behavior
# ---------------------------------------------------------------------------

class TestMemoryProviderABCHooks:
    def _make_concrete(self):
        from bauer.memory_provider import MemoryProvider

        class ConcreteProvider(MemoryProvider):
            def __init__(self):
                self.prefetch_calls: list[str] = []
                self.turn_start_calls: list[tuple] = []

            def initialize(self, workspace):
                pass

            def prefetch(self):
                self.prefetch_calls.append("prefetch")

            def on_turn_start(self, turn_index, messages):
                self.turn_start_calls.append((turn_index, len(messages)))

        return ConcreteProvider()

    def test_on_turn_start_default_noop(self):
        from bauer.memory_provider import MemoryProvider

        class Minimal(MemoryProvider):
            def initialize(self, workspace):
                pass

        p = Minimal()
        p.on_turn_start(0, [])  # should not raise

    def test_on_turn_start_called_with_args(self):
        p = self._make_concrete()
        msgs = [{"role": "user", "content": "hello"}]
        p.on_turn_start(3, msgs)
        assert p.turn_start_calls == [(3, 1)]

    def test_queue_prefetch_calls_prefetch_in_background(self):
        p = self._make_concrete()
        p.queue_prefetch()
        # Wait for background thread
        time.sleep(0.1)
        assert "prefetch" in p.prefetch_calls

    def test_queue_prefetch_does_not_block(self):
        """queue_prefetch should return immediately even if prefetch is slow."""
        from bauer.memory_provider import MemoryProvider

        class SlowProvider(MemoryProvider):
            def initialize(self, workspace):
                pass

            def prefetch(self):
                time.sleep(2)

        p = SlowProvider()
        t0 = time.time()
        p.queue_prefetch()
        elapsed = time.time() - t0
        assert elapsed < 0.5

    def test_queue_prefetch_exception_in_prefetch_does_not_propagate(self):
        from bauer.memory_provider import MemoryProvider

        class FailingProvider(MemoryProvider):
            def initialize(self, workspace):
                pass

            def prefetch(self):
                raise RuntimeError("boom")

        p = FailingProvider()
        p.queue_prefetch()  # should not raise
        time.sleep(0.1)  # let thread finish

    def test_queue_prefetch_multiple_calls(self):
        p = self._make_concrete()
        p.queue_prefetch()
        p.queue_prefetch()
        time.sleep(0.2)
        assert len(p.prefetch_calls) >= 2

    def test_on_turn_start_multiple_turns(self):
        p = self._make_concrete()
        for i in range(5):
            p.on_turn_start(i, [])
        assert len(p.turn_start_calls) == 5
        assert p.turn_start_calls[4] == (4, 0)


# ---------------------------------------------------------------------------
# TestLocalMemoryProviderOnTurnStart
# ---------------------------------------------------------------------------

class TestLocalMemoryProviderOnTurnStart:
    def _provider(self, tmp_path: Path):
        from bauer.memory_provider import LocalMemoryProvider
        p = LocalMemoryProvider()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        with patch("bauer.memory_manager.MemoryManager") as MockMgr:
            mock_mgr = MagicMock()
            mock_mgr.read_file.return_value = ""
            MockMgr.return_value = mock_mgr
            p.initialize(workspace)
        return p

    def test_on_turn_start_no_crash(self, tmp_path):
        p = self._provider(tmp_path)
        p.on_turn_start(0, [])

    def test_on_turn_start_triggers_prefetch_at_interval(self, tmp_path):
        p = self._provider(tmp_path)
        prefetch_calls = []
        original_prefetch = p.prefetch

        def _track():
            prefetch_calls.append(1)
            try:
                original_prefetch()
            except Exception:
                pass

        p.prefetch = _track

        p.on_turn_start(5, [])   # turn 5 → múltiplo de 5 → deve chamar prefetch
        assert len(prefetch_calls) == 1

    def test_on_turn_start_no_prefetch_on_non_interval(self, tmp_path):
        p = self._provider(tmp_path)
        prefetch_calls = []
        p.prefetch = lambda: prefetch_calls.append(1)

        p.on_turn_start(3, [])  # não é múltiplo de 5
        assert len(prefetch_calls) == 0

    def test_on_turn_start_turn_zero_no_prefetch(self, tmp_path):
        p = self._provider(tmp_path)
        prefetch_calls = []
        p.prefetch = lambda: prefetch_calls.append(1)

        p.on_turn_start(0, [])  # turno 0 não deve disparar (é o initial)
        assert len(prefetch_calls) == 0


# ---------------------------------------------------------------------------
# TestMultiMemoryProviderFanOut
# ---------------------------------------------------------------------------

class TestMultiMemoryProviderFanOut:
    def _multi(self, *providers):
        from bauer.memory_provider import MultiMemoryProvider
        m = MultiMemoryProvider.__new__(MultiMemoryProvider)
        m._providers = list(providers)
        m._MAX_BLOCK_CHARS = 4000
        return m

    def _mock_provider(self):
        p = MagicMock()
        p.on_turn_start = MagicMock()
        p.queue_prefetch = MagicMock()
        p.prefetch = MagicMock()
        p.system_prompt_block = MagicMock(return_value="")
        p.get_tool_schemas = MagicMock(return_value=[])
        p.get_config_schema = MagicMock(return_value={})
        p.should_nudge = MagicMock(return_value=False)
        return p

    def test_on_turn_start_fans_out(self):
        p1, p2 = self._mock_provider(), self._mock_provider()
        m = self._multi(p1, p2)
        msgs = [{"role": "user", "content": "x"}]
        m.on_turn_start(7, msgs)
        p1.on_turn_start.assert_called_once_with(7, msgs)
        p2.on_turn_start.assert_called_once_with(7, msgs)

    def test_queue_prefetch_fans_out(self):
        p1, p2 = self._mock_provider(), self._mock_provider()
        m = self._multi(p1, p2)
        m.queue_prefetch()
        p1.queue_prefetch.assert_called_once()
        p2.queue_prefetch.assert_called_once()

    def test_on_turn_start_provider_exception_does_not_stop_others(self):
        p1, p2 = self._mock_provider(), self._mock_provider()
        p1.on_turn_start.side_effect = RuntimeError("boom")
        m = self._multi(p1, p2)
        m.on_turn_start(1, [])
        p2.on_turn_start.assert_called_once()

    def test_queue_prefetch_provider_exception_does_not_stop_others(self):
        p1, p2 = self._mock_provider(), self._mock_provider()
        p1.queue_prefetch.side_effect = RuntimeError("boom")
        m = self._multi(p1, p2)
        m.queue_prefetch()
        p2.queue_prefetch.assert_called_once()

    def test_on_turn_start_empty_providers(self):
        m = self._multi()
        m.on_turn_start(0, [])  # should not raise

    def test_queue_prefetch_empty_providers(self):
        m = self._multi()
        m.queue_prefetch()  # should not raise

    def test_on_turn_start_passes_turn_index(self):
        p = self._mock_provider()
        m = self._multi(p)
        m.on_turn_start(42, [])
        call_args = p.on_turn_start.call_args
        assert call_args[0][0] == 42

    def test_on_turn_start_three_providers(self):
        providers = [self._mock_provider() for _ in range(3)]
        m = self._multi(*providers)
        m.on_turn_start(1, [{"role": "user", "content": "x"}])
        for p in providers:
            p.on_turn_start.assert_called_once()


# ---------------------------------------------------------------------------
# TestAgentOnTurnStartWiring
# ---------------------------------------------------------------------------

class TestAgentOnTurnStartWiring:
    def test_on_turn_start_called_in_loop(self):
        """Verifica que on_turn_start está disponível e não quebra quando chamado."""
        from bauer.memory_provider import LocalMemoryProvider

        p = LocalMemoryProvider()
        # Não inicializado — on_turn_start deve ser no-op seguro
        p.on_turn_start(0, [])
        p.on_turn_start(5, [{"role": "user", "content": "x"}])

    def test_queue_prefetch_is_non_blocking(self):
        from bauer.memory_provider import LocalMemoryProvider

        p = LocalMemoryProvider()
        t0 = time.time()
        p.queue_prefetch()
        elapsed = time.time() - t0
        assert elapsed < 0.5  # retorna quase imediatamente

    def test_safe_prefetch_swallows_exceptions(self):
        from bauer.memory_provider import MemoryProvider

        class Exploder(MemoryProvider):
            def initialize(self, ws):
                pass
            def prefetch(self):
                raise ValueError("explode")

        p = Exploder()
        p._safe_prefetch()  # should not raise
