"""Testes do SqliteSessionStore (MEM-1).

Cobre:
- Schema criado automaticamente (sessions + messages + FTS5 triggers)
- CRUD: save, load, list_sessions, delete, exists
- Conteúdo Anthropic-style (lista de blocks) convertido para str
- Migração automática de arquivos .jsonl legados
- Busca FTS5 (quando disponível) e fallback LIKE
- Deduplicação de resultados por sessão
- Interface compatível com SessionStore (drop-in replacement)
- update_summary e get_metadata
- Isolamento: cada teste usa tmp_path próprio
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bauer.sqlite_session_store import SqliteSessionStore, _fts5_available, _content_to_str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path: Path) -> SqliteSessionStore:
    """Store limpo em diretório temporário."""
    return SqliteSessionStore(tmp_path / "sessions")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FTS5_AVAILABLE = _fts5_available(sqlite3.connect(":memory:"))


# ---------------------------------------------------------------------------
# _content_to_str
# ---------------------------------------------------------------------------

class TestContentToStr:
    def test_str_passthrough(self):
        assert _content_to_str("hello") == "hello"

    def test_none_returns_empty(self):
        assert _content_to_str(None) == ""

    def test_anthropic_blocks(self):
        blocks = [{"type": "text", "text": "oi"}, {"type": "text", "text": " mundo"}]
        assert _content_to_str(blocks) == "oi  mundo"

    def test_mixed_blocks(self):
        blocks = [{"type": "text", "text": "hello"}, {"type": "image", "data": "base64"}]
        result = _content_to_str(blocks)
        assert "hello" in result

    def test_list_of_strings(self):
        result = _content_to_str(["a", "b"])
        assert "a" in result and "b" in result

    def test_int_converts(self):
        assert _content_to_str(42) == "42"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestSchema:
    def test_db_file_created(self, store: SqliteSessionStore, tmp_path: Path):
        assert (tmp_path / "sessions" / "sessions.db").exists()

    def test_sessions_table_exists(self, store: SqliteSessionStore):
        conn = store._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchall()
        conn.close()
        assert rows

    def test_messages_table_exists(self, store: SqliteSessionStore):
        conn = store._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages'"
        ).fetchall()
        conn.close()
        assert rows

    @pytest.mark.skipif(not FTS5_AVAILABLE, reason="FTS5 não disponível neste SQLite")
    def test_fts5_table_exists(self, store: SqliteSessionStore):
        conn = store._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'"
        ).fetchall()
        conn.close()
        assert rows

    def test_wal_mode(self, store: SqliteSessionStore):
        conn = store._connect()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestCRUD:
    def test_new_id_is_string(self, store: SqliteSessionStore):
        sid = store.new_id()
        assert isinstance(sid, str)
        assert len(sid) == 8

    def test_new_id_unique(self, store: SqliteSessionStore):
        ids = {store.new_id() for _ in range(20)}
        assert len(ids) == 20

    def test_save_and_load(self, store: SqliteSessionStore):
        msgs = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]
        store.save("sid1", msgs)
        loaded = store.load("sid1")
        assert len(loaded) == 2
        assert loaded[0]["role"] == "user"
        assert loaded[0]["content"] == "oi"
        assert loaded[1]["role"] == "assistant"

    def test_load_empty_session(self, store: SqliteSessionStore):
        assert store.load("nao_existe") == []

    def test_save_overwrites(self, store: SqliteSessionStore):
        store.save("sid1", [{"role": "user", "content": "a"}])
        store.save("sid1", [{"role": "user", "content": "b"}, {"role": "assistant", "content": "c"}])
        loaded = store.load("sid1")
        assert len(loaded) == 2
        assert loaded[0]["content"] == "b"

    def test_list_sessions_empty(self, store: SqliteSessionStore):
        assert store.list_sessions() == []

    def test_list_sessions_returns_saved(self, store: SqliteSessionStore):
        store.save("s1", [{"role": "user", "content": "a"}])
        store.save("s2", [{"role": "user", "content": "b"}])
        sessions = store.list_sessions()
        assert "s1" in sessions
        assert "s2" in sessions

    def test_delete_existing(self, store: SqliteSessionStore):
        store.save("s1", [{"role": "user", "content": "hi"}])
        assert store.delete("s1") is True
        assert store.load("s1") == []

    def test_delete_nonexistent(self, store: SqliteSessionStore):
        assert store.delete("nao_existe") is False

    def test_exists_true(self, store: SqliteSessionStore):
        store.save("sid", [{"role": "user", "content": "x"}])
        assert store.exists("sid") is True

    def test_exists_false(self, store: SqliteSessionStore):
        assert store.exists("nao_existe") is False

    def test_save_anthropic_style_content(self, store: SqliteSessionStore):
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hello blocks"}]}]
        store.save("anthropic_sid", msgs)
        loaded = store.load("anthropic_sid")
        assert "hello blocks" in loaded[0]["content"]


# ---------------------------------------------------------------------------
# Metadados
# ---------------------------------------------------------------------------

class TestMetadata:
    def test_get_metadata_returns_dict(self, store: SqliteSessionStore):
        store.save("sid", [{"role": "user", "content": "x"}])
        meta = store.get_metadata("sid")
        assert meta is not None
        assert meta["session_id"] == "sid"
        assert meta["message_count"] == 1

    def test_get_metadata_nonexistent(self, store: SqliteSessionStore):
        assert store.get_metadata("nao_existe") is None

    def test_update_summary(self, store: SqliteSessionStore):
        store.save("sid", [{"role": "user", "content": "x"}])
        store.update_summary("sid", "resumo da sessão")
        meta = store.get_metadata("sid")
        assert meta["summary"] == "resumo da sessão"


# ---------------------------------------------------------------------------
# Migração de JSONL
# ---------------------------------------------------------------------------

class TestJsonlMigration:
    def test_migrates_existing_jsonl(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        # Cria arquivo JSONL antes do store
        jsonl = sessions_dir / "legacy_sess.jsonl"
        jsonl.write_text(
            json.dumps({"role": "user", "content": "from jsonl"}) + "\n"
            + json.dumps({"role": "assistant", "content": "migrated"}) + "\n",
            encoding="utf-8",
        )
        store = SqliteSessionStore(sessions_dir)
        loaded = store.load("legacy_sess")
        assert len(loaded) == 2
        assert loaded[0]["content"] == "from jsonl"

    def test_migration_idempotent(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        jsonl = sessions_dir / "sess.jsonl"
        jsonl.write_text(
            json.dumps({"role": "user", "content": "once"}) + "\n",
            encoding="utf-8",
        )
        # Cria store duas vezes — segunda migração não duplica
        SqliteSessionStore(sessions_dir)
        store2 = SqliteSessionStore(sessions_dir)
        loaded = store2.load("sess")
        assert len(loaded) == 1

    def test_malformed_jsonl_lines_skipped(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        jsonl = sessions_dir / "broken.jsonl"
        jsonl.write_text(
            "not json\n"
            + json.dumps({"role": "user", "content": "ok"}) + "\n",
            encoding="utf-8",
        )
        store = SqliteSessionStore(sessions_dir)
        loaded = store.load("broken")
        assert len(loaded) == 1
        assert loaded[0]["content"] == "ok"

    def test_empty_jsonl_not_migrated(self, tmp_path: Path):
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        (sessions_dir / "empty.jsonl").write_text("", encoding="utf-8")
        store = SqliteSessionStore(sessions_dir)
        # Sessão vazia não deve ser importada
        assert "empty" not in store.list_sessions()


# ---------------------------------------------------------------------------
# Busca (FTS5 / LIKE fallback)
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.fixture
    def populated_store(self, tmp_path: Path) -> SqliteSessionStore:
        store = SqliteSessionStore(tmp_path / "sessions")
        store.save("s1", [
            {"role": "user", "content": "python é incrível"},
            {"role": "assistant", "content": "concordo plenamente"},
        ])
        store.save("s2", [
            {"role": "user", "content": "java é verboso"},
            {"role": "assistant", "content": "mas robusto"},
        ])
        store.save("s3", [
            {"role": "user", "content": "rust tem borrow checker"},
        ])
        return store

    def test_search_finds_match(self, populated_store: SqliteSessionStore):
        results = populated_store.search_sessions("python")
        assert any(r["session_id"] == "s1" for r in results)

    def test_search_no_match(self, populated_store: SqliteSessionStore):
        results = populated_store.search_sessions("cobol")
        assert results == []

    def test_search_empty_query(self, populated_store: SqliteSessionStore):
        results = populated_store.search_sessions("")
        assert results == []

    def test_search_top_k(self, populated_store: SqliteSessionStore):
        results = populated_store.search_sessions("é", top_k=1)
        assert len(results) <= 1

    def test_search_deduplicates_sessions(self, populated_store: SqliteSessionStore):
        # Sessão s1 tem 2 mensagens com "python" implicitamente via "incrível" — não deve duplicar
        results = populated_store.search_sessions("concordo")
        session_ids = [r["session_id"] for r in results]
        assert len(session_ids) == len(set(session_ids))

    def test_search_returns_snippet(self, populated_store: SqliteSessionStore):
        results = populated_store.search_sessions("python")
        assert results
        assert "snippet" in results[0]
        assert isinstance(results[0]["snippet"], str)

    def test_search_returns_metadata_keys(self, populated_store: SqliteSessionStore):
        results = populated_store.search_sessions("python")
        assert results
        r = results[0]
        for key in ("session_id", "role", "snippet", "rank", "updated_at"):
            assert key in r, f"Chave ausente: {key}"

    def test_search_role_filter_user(self, populated_store: SqliteSessionStore):
        # "concordo" está apenas na role=assistant; use_vectors=False para
        # isolar de sessions reais na global VectorStore (~/.bauer/vector_store.db)
        results = populated_store.search_sessions("concordo", role_filter="user", use_vectors=False)
        assert results == []

    def test_search_role_filter_assistant(self, populated_store: SqliteSessionStore):
        results = populated_store.search_sessions("concordo", role_filter="assistant", use_vectors=False)
        assert any(r["session_id"] == "s1" for r in results)

    def test_search_after_delete(self, populated_store: SqliteSessionStore):
        populated_store.delete("s1")
        results = populated_store.search_sessions("python")
        assert not any(r["session_id"] == "s1" for r in results)


# ---------------------------------------------------------------------------
# Drop-in compatibility com SessionStore
# ---------------------------------------------------------------------------

class TestCompatibility:
    def test_has_new_id(self, store: SqliteSessionStore):
        assert callable(store.new_id)

    def test_has_save(self, store: SqliteSessionStore):
        assert callable(store.save)

    def test_has_load(self, store: SqliteSessionStore):
        assert callable(store.load)

    def test_has_list_sessions(self, store: SqliteSessionStore):
        assert callable(store.list_sessions)

    def test_has_delete(self, store: SqliteSessionStore):
        assert callable(store.delete)

    def test_has_exists(self, store: SqliteSessionStore):
        assert callable(store.exists)

    def test_has_search_sessions(self, store: SqliteSessionStore):
        assert callable(store.search_sessions)

    def test_multiple_saves_and_loads_independent(self, store: SqliteSessionStore):
        """Sessões diferentes não se interferem."""
        store.save("a", [{"role": "user", "content": "session A"}])
        store.save("b", [{"role": "user", "content": "session B"}])
        a = store.load("a")
        b = store.load("b")
        assert a[0]["content"] == "session A"
        assert b[0]["content"] == "session B"
