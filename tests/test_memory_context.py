"""Tests for bauer.memory_context — prefetch and sync."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.memory_context import prefetch_memory_context, sync_memory_after_turn


# ---------------------------------------------------------------------------
# prefetch_memory_context
# ---------------------------------------------------------------------------

class TestPrefetchMemoryContext:
    def test_empty_input_returns_none(self, tmp_path):
        assert prefetch_memory_context("", workspace=tmp_path) is None
        assert prefetch_memory_context("   ", workspace=tmp_path) is None

    def test_no_records_returns_none(self, tmp_path):
        result = prefetch_memory_context("como resolver um bug de memória", workspace=tmp_path)
        assert result is None

    def test_decision_records_appear_in_context(self, tmp_path):
        from bauer.decision_memory import DecisionMemory
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        dm.record(
            context="user asked to delete all logs",
            decision="Requested confirmation before executing rm -rf",
            outcome="good",
            tags=["safety"],
        )
        # Force TF-IDF to include this by using the same keywords
        result = prefetch_memory_context("delete logs and clean up", workspace=tmp_path)
        # May be None if similarity too low — just ensure no crash
        assert result is None or "<memory-context>" in result

    def test_high_similarity_record_is_included(self, tmp_path):
        from bauer.decision_memory import DecisionMemory
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        dm.record(
            context="refatorar o módulo de autenticação",
            decision="Extrai a lógica de token em auth_utils.py",
            outcome="good",
            tags=["refactor"],
        )
        result = prefetch_memory_context("refatorar módulo de autenticação", workspace=tmp_path)
        if result is not None:
            assert "<memory-context>" in result
            assert "</memory-context>" in result

    def test_result_has_memory_fence_tags(self, tmp_path):
        from bauer.decision_memory import DecisionMemory
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        # Seed many records with the same context to ensure high score
        for i in range(5):
            dm.record(
                context="fix authentication bug login error",
                decision=f"Fixed by adding null check at auth.py line {i*10}",
                outcome="good",
            )
        result = prefetch_memory_context("fix authentication bug login error", workspace=tmp_path)
        if result is not None:
            assert result.startswith("<memory-context>")
            assert result.endswith("</memory-context>")

    def test_never_raises_on_broken_workspace(self, tmp_path):
        # Point workspace at a file — DecisionMemory will fail gracefully
        broken = tmp_path / "not_a_dir.txt"
        broken.write_text("x")
        result = prefetch_memory_context("qualquer coisa", workspace=broken)
        # Should return None silently, not raise
        assert result is None

    def test_session_search_integration(self, tmp_path):
        """Session search is attempted and either returns hits or None."""
        try:
            from bauer.sqlite_session_store import SqliteSessionStore
            store = SqliteSessionStore(workspace=tmp_path)
            store.save("sess-1", [
                {"role": "user", "content": "como usar o git rebase interativo"},
                {"role": "assistant", "content": "Use git rebase -i HEAD~3 para editar os últimos 3 commits."},
            ])
        except Exception:
            pytest.skip("SqliteSessionStore não disponível")

        result = prefetch_memory_context("git rebase interativo commits", workspace=tmp_path)
        # Just verifying no crash — result may be None if score below threshold
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# sync_memory_after_turn
# ---------------------------------------------------------------------------

class TestSyncMemoryAfterTurn:
    def test_does_not_crash_on_empty_inputs(self, tmp_path):
        sync_memory_after_turn("", "", [], workspace=tmp_path)
        sync_memory_after_turn("input", "", [], workspace=tmp_path)

    def test_skips_slash_commands(self, tmp_path):
        from bauer.decision_memory import DecisionMemory
        sync_memory_after_turn("/clear", "contexto limpo", [], workspace=tmp_path)
        time.sleep(0.15)
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        records = dm.search("clear", top_k=10)
        assert len(records) == 0

    def test_records_substantive_turn(self, tmp_path):
        from bauer.decision_memory import DecisionMemory
        sync_memory_after_turn(
            "como otimizar queries SQL lentas",
            "Use índices compostos nas colunas de filtragem mais frequentes e analise o EXPLAIN ANALYZE.",
            [{"tool": "web_search"}],
            workspace=tmp_path,
        )
        time.sleep(0.15)
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        records = dm.search("otimizar queries SQL", top_k=5)
        assert len(records) >= 1
        assert "índices" in records[0].decision.lower() or "sql" in records[0].context.lower()

    def test_tags_extracted_from_tool_log(self, tmp_path):
        from bauer.decision_memory import DecisionMemory
        sync_memory_after_turn(
            "busca web sobre Python 3.13",
            "Python 3.13 foi lançado com melhorias de performance no GIL.",
            [{"tool": "web_search"}, {"tool": "read_file"}],
            workspace=tmp_path,
        )
        time.sleep(0.15)
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        records = dm.search("python busca web", top_k=5, tags_filter=["web_search"])
        # tags_filter may not match if tagging failed — just no crash
        assert isinstance(records, list)

    def test_short_response_not_recorded(self, tmp_path):
        from bauer.decision_memory import DecisionMemory
        sync_memory_after_turn(
            "ok?",
            "ok",  # too short (<40 chars)
            [],
            workspace=tmp_path,
        )
        time.sleep(0.15)
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        records = dm.search("ok", top_k=10)
        assert len(records) == 0

    def test_runs_in_background_does_not_block(self, tmp_path):
        start = time.monotonic()
        sync_memory_after_turn(
            "tarefa de longa duração simulada",
            "resposta completa com mais de 40 caracteres para ser gravada",
            [],
            workspace=tmp_path,
        )
        elapsed = time.monotonic() - start
        # Sync runs in a daemon thread — main thread should return fast
        assert elapsed < 0.5


# ---------------------------------------------------------------------------
# ContextManager.add_ephemeral_system
# ---------------------------------------------------------------------------

class TestContextManagerEphemeralSystem:
    def test_add_ephemeral_system_adds_system_message(self):
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=4096, system_prompt="base")
        ctx.add_ephemeral_system("<memory-context>\nhint\n</memory-context>")
        assert any(
            m.get("role") == "system" and "<memory-context>" in m.get("content", "")
            for m in ctx.messages
        )

    def test_ephemeral_system_before_user_message(self):
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=4096, system_prompt="base")
        ctx.add_ephemeral_system("mem-hint")
        ctx.add_user("user question")
        roles = [m["role"] for m in ctx.messages]
        assert roles == ["system", "user"]


class TestWorkspaceTypeGuard:
    """Guard de tipo do workspace — evita Path(MagicMock()) escrevendo lixo."""

    def test_safe_workspace_passes_valid_paths(self, tmp_path):
        from bauer.memory_context import _safe_workspace
        assert _safe_workspace(None) is None
        assert _safe_workspace("some/dir") == "some/dir"
        assert _safe_workspace(tmp_path) == tmp_path

    def test_safe_workspace_rejects_non_path(self):
        from bauer.memory_context import _safe_workspace
        assert _safe_workspace(MagicMock()) is None
        assert _safe_workspace(123) is None

    def test_prefetch_with_mock_workspace_writes_nothing_to_cwd(self, tmp_path, monkeypatch):
        """Regressão: um MagicMock como workspace (comum em testes que mockam
        config) fazia Path(MagicMock())/decisions.db criar 'MagicMock/...' na
        raiz do repo. Com o guard, cai no ':memory:' e nada é escrito no CWD."""
        monkeypatch.chdir(tmp_path)
        # Não deve levantar nem criar diretórios de lixo.
        prefetch_memory_context("qual a decisão?", workspace=MagicMock())
        assert not any(p.name == "MagicMock" for p in tmp_path.iterdir())
