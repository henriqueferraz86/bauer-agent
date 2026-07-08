"""Testes da curadoria de ingestão do índice vetorial.

Medição da base real: 55% dos vetores eram role=tool + 4% system (59% de
ruído), e o re-index re-embedava a conversa inteira a cada save (O(n²)). Estes
testes fixam: (1) só user/assistant entram no índice, (2) store_if_absent não
re-embeda, (3) compact_vector_index limpa o acúmulo antigo.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bauer.vector_store import VectorStore
from bauer.sqlite_session_store import SqliteSessionStore, _INDEXED_VECTOR_ROLES


class _CountingEngine:
    """Engine de embedding falso que conta chamadas (p/ provar não-reembed)."""

    def __init__(self):
        self.calls = 0

    def embed(self, text: str):
        self.calls += 1
        # vetor determinístico trivial
        return [float(len(text) % 7), 1.0, 0.0]


# ─── store_if_absent ─────────────────────────────────────────────────────────

class TestStoreIfAbsent:
    def test_nao_reembeda_existente(self):
        eng = _CountingEngine()
        vs = VectorStore(db_path=":memory:", engine=eng)
        vs.store_if_absent("s:user:0", "session_msg", "olá mundo")
        assert eng.calls == 1
        # segunda chamada com mesmo source_id → NÃO chama embed de novo
        vs.store_if_absent("s:user:0", "session_msg", "olá mundo")
        assert eng.calls == 1
        assert vs.count("session_msg") == 1

    def test_embeda_novos(self):
        eng = _CountingEngine()
        vs = VectorStore(db_path=":memory:", engine=eng)
        vs.store_if_absent("s:user:0", "session_msg", "a")
        vs.store_if_absent("s:user:1", "session_msg", "b")
        assert eng.calls == 2
        assert vs.count("session_msg") == 2

    def test_reindex_repetido_nao_multiplica_embeds(self):
        # Simula o re-index a cada save: mesma conversa, 5 saves.
        eng = _CountingEngine()
        vs = VectorStore(db_path=":memory:", engine=eng)
        convo = [("s:user:0", "pergunta"), ("s:assistant:1", "resposta")]
        for _ in range(5):
            for sid, txt in convo:
                vs.store_if_absent(sid, "session_msg", txt)
        assert eng.calls == 2, "deveria embedar cada msg UMA vez, não a cada save"


# ─── filtro de role na ingestão de sessão ────────────────────────────────────

class TestSessionIndexRoleFilter:
    def test_so_user_e_assistant_no_indice(self):
        assert _INDEXED_VECTOR_ROLES == frozenset({"user", "assistant"})

    def test_tool_e_system_nao_entram_no_indice(self, tmp_path, monkeypatch):
        # Usa um VectorStore isolado como default global p/ inspecionar.
        eng = _CountingEngine()
        vs = VectorStore(db_path=":memory:", engine=eng)
        monkeypatch.setattr("bauer.vector_store.get_default_store", lambda *a, **k: vs)

        store = SqliteSessionStore(tmp_path / "sessions")

        # O index roda em daemon thread (import threading LOCAL no save) — patcha
        # o Thread global p/ rodar síncrono e tornar determinístico.
        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kw): self._t = target
            def start(self):
                if self._t: self._t()
        monkeypatch.setattr("threading.Thread", _SyncThread)

        store.save("sX", [
            {"role": "system", "content": "voce e um assistente " * 5},
            {"role": "user", "content": "qual a capital da franca?"},
            {"role": "assistant", "content": "paris, a capital da franca."},
            {"role": "tool", "content": '{"result": "output cru de tool " }' * 5},
        ])
        ids = vs.list_source_ids("session_msg")
        roles = {i.split(":")[-2] for i in ids}
        assert roles == {"user", "assistant"}, f"tool/system vazaram: {roles}"
        assert vs.count("session_msg") == 2


# ─── compact_vector_index (limpeza única do acúmulo antigo) ──────────────────

class TestCompactVectorIndex:
    def test_remove_tool_e_system_mantem_user_assistant(self, tmp_path, monkeypatch):
        eng = _CountingEngine()
        vs = VectorStore(db_path=":memory:", engine=eng)
        monkeypatch.setattr("bauer.vector_store.get_default_store", lambda *a, **k: vs)
        # simula acúmulo ANTIGO (antes do filtro): todas as roles no índice
        vs.store("s:system:0", "session_msg", "prompt fixo")
        vs.store("s:user:1", "session_msg", "pergunta real")
        vs.store("s:assistant:2", "session_msg", "resposta real")
        vs.store("s:tool:3", "session_msg", "output cru")
        vs.store("s:tool:4", "session_msg", "mais output")
        assert vs.count("session_msg") == 5

        store = SqliteSessionStore(tmp_path / "sessions")
        removed = store.compact_vector_index()
        assert removed == 3  # 1 system + 2 tool
        ids = vs.list_source_ids("session_msg")
        roles = {i.split(":")[-2] for i in ids}
        assert roles == {"user", "assistant"}

    def test_idempotente(self, tmp_path, monkeypatch):
        vs = VectorStore(db_path=":memory:", engine=_CountingEngine())
        monkeypatch.setattr("bauer.vector_store.get_default_store", lambda *a, **k: vs)
        vs.store("s:user:0", "session_msg", "x")
        vs.store("s:tool:1", "session_msg", "y")
        store = SqliteSessionStore(tmp_path / "sessions")
        assert store.compact_vector_index() == 1
        assert store.compact_vector_index() == 0  # nada mais a remover


# ─── list_source_ids ─────────────────────────────────────────────────────────

def test_list_source_ids():
    vs = VectorStore(db_path=":memory:", engine=_CountingEngine())
    vs.store("a", "session_msg", "1")
    vs.store("b", "session_msg", "2")
    vs.store("c", "decision", "3")
    ids = set(vs.list_source_ids("session_msg"))
    assert ids == {"a", "b"}
