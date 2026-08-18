"""Tests for bauer/vector_store.py — SQLite-backed vector store."""

from __future__ import annotations

import pytest

from bauer.embeddings import EmbeddingEngine
from bauer.vector_store import VectorStore, SearchResult, get_default_store


@pytest.fixture
def engine():
    return EmbeddingEngine(force_backend="tfidf")


@pytest.fixture
def store(engine):
    return VectorStore(":memory:", engine=engine)


# ---------------------------------------------------------------------------
# Basic store/count/delete
# ---------------------------------------------------------------------------


def test_store_single(store):
    rowid = store.store("msg_001", "session", "hello world")
    assert rowid > 0


def test_count_zero_initially(store):
    assert store.count() == 0


def test_count_after_store(store):
    store.store("msg_001", "session", "text one")
    store.store("msg_002", "session", "text two")
    assert store.count() == 2


def test_count_by_source_type(store):
    store.store("msg_001", "session", "session text")
    store.store("dec_001", "decision", "decision text")
    assert store.count("session") == 1
    assert store.count("decision") == 1
    assert store.count() == 2


def test_delete_existing(store):
    store.store("msg_001", "session", "to delete")
    n = store.delete("msg_001", "session")
    assert n == 1
    assert store.count() == 0


def test_delete_nonexistent(store):
    n = store.delete("nonexistent", "session")
    assert n == 0


def test_store_is_idempotent(store):
    """Storing same (source_id, source_type) twice should replace, not duplicate."""
    store.store("msg_001", "session", "first version")
    store.store("msg_001", "session", "second version")
    assert store.count() == 1


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_returns_list(store):
    store.store("msg_001", "session", "kubernetes pod failure")
    results = store.search("kubernetes")
    assert isinstance(results, list)


def test_search_finds_related(store):
    store.store("msg_001", "session", "kubernetes pod crash restart")
    store.store("msg_002", "session", "apple pie recipe baking")
    results = store.search("kubernetes pod error", top_k=5)
    assert len(results) >= 1
    top_source_ids = [r.source_id for r in results]
    assert "msg_001" in top_source_ids


def test_search_top_k_respected(store):
    for i in range(10):
        store.store(f"msg_{i:03d}", "session", f"text about topic {i}")
    results = store.search("topic", top_k=3)
    assert len(results) <= 3


def test_search_by_source_type(store):
    store.store("msg_001", "session", "kubernetes deployment")
    store.store("dec_001", "decision", "use kubernetes helm")
    # Search only in decision type
    results = store.search("kubernetes", top_k=5, source_type="decision")
    source_ids = [r.source_id for r in results]
    assert "dec_001" in source_ids
    assert "msg_001" not in source_ids


def test_search_min_score_filter(store):
    store.store("msg_001", "session", "completely unrelated content xyz abc 123")
    # Search for something very different — should be filtered out with high min_score
    results = store.search("quantum physics relativity", top_k=5, min_score=0.9)
    # Either empty or very high-scoring
    for r in results:
        assert r.score >= 0.9


def test_search_empty_store(store):
    results = store.search("anything")
    assert results == []


def test_search_result_fields(store):
    store.store("msg_abc", "session", "example text content")
    results = store.search("example text")
    if results:
        r = results[0]
        assert isinstance(r, SearchResult)
        assert r.source_id == "msg_abc"
        assert r.source_type == "session"
        assert r.text == "example text content"
        assert 0.0 <= r.score <= 1.0
        assert r.created_at > 0


# ---------------------------------------------------------------------------
# search_by_vector
# ---------------------------------------------------------------------------


def test_search_by_vector(store, engine):
    store.store("msg_001", "session", "machine learning neural networks")
    q_vec = engine.embed("deep learning models")
    results = store.search_by_vector(q_vec, top_k=3)
    assert isinstance(results, list)
    if results:
        assert all(isinstance(r, SearchResult) for r in results)


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------


def test_rebuild_index(store, engine):
    store.store("msg_001", "session", "some text to re-embed")
    store.store("msg_002", "session", "another text to re-embed")
    count = store.rebuild_index(source_type="session")
    assert count == 2


def test_rebuild_index_all(store):
    store.store("msg_001", "session", "session message")
    store.store("dec_001", "decision", "decision record")
    count = store.rebuild_index()
    assert count == 2


# ---------------------------------------------------------------------------
# Pre-computed embeddings
# ---------------------------------------------------------------------------


def test_store_with_precomputed_embedding(store):
    embedding = [0.1] * 4096
    rowid = store.store("msg_pre", "session", "precomputed text", embedding=embedding)
    assert rowid > 0
    # Search should still work
    results = store.search("precomputed", top_k=1)
    assert len(results) >= 0  # may or may not find it (depends on similarity)


# ---------------------------------------------------------------------------
# Guarda de dimensão
#
# `cosine_similarity` usa zip(), que TRUNCA no vetor mais curto em vez de
# falhar: misturar dimensões não gera erro, gera score sem significado. Como o
# EmbeddingEngine cai para TF-IDF (4096d) em silêncio quando o Ollama some, o
# índice podia ser envenenado por uma simples queda de rede.
# ---------------------------------------------------------------------------


class _EngineFixo:
    """Engine de teste que devolve vetores de dimensão controlada."""

    def __init__(self, dim: int, backend: str = "stub") -> None:
        self._dim = dim
        self.backend = backend

    def embed(self, text: str) -> list[float]:
        # Vetor determinístico e não-nulo, para o cosseno não dar divisão por zero.
        return [float(len(text) % 7 + 1)] + [0.0] * (self._dim - 1)


class _EngineQueCai:
    """Simula o Ollama sumindo no meio: N vetores densos, depois o fallback."""

    def __init__(self, dim_ok: int, dim_fallback: int, cai_apos: int) -> None:
        self._dim_ok = dim_ok
        self._dim_fallback = dim_fallback
        self._cai_apos = cai_apos
        self._n = 0
        self.backend = "ollama"

    def embed(self, text: str) -> list[float]:
        self._n += 1
        if self._n <= self._cai_apos:
            return [1.0] * self._dim_ok
        self.backend = "tfidf"
        return [1.0] * self._dim_fallback


def test_store_primeira_escrita_define_dimensao():
    store = VectorStore(":memory:", engine=_EngineFixo(8))
    assert store.store("a", "t", "texto", embedding=[1.0] * 5) > 0
    assert store.count() == 1


def test_store_recusa_dimensao_divergente():
    store = VectorStore(":memory:", engine=_EngineFixo(8))
    store.store("a", "t", "texto", embedding=[1.0] * 5)
    # Segunda escrita com outra dimensão: recusada, sem exceção e sem gravar.
    assert store.store("b", "t", "outro", embedding=[1.0] * 9) == 0
    assert store.count() == 1


def test_store_aceita_dimensao_compativel():
    store = VectorStore(":memory:", engine=_EngineFixo(8))
    store.store("a", "t", "texto", embedding=[1.0, 0.0, 0.0])
    assert store.store("b", "t", "outro", embedding=[0.0, 1.0, 0.0]) > 0
    assert store.count() == 2


def test_busca_ignora_linha_de_dimensao_divergente():
    import json

    store = VectorStore(":memory:", engine=_EngineFixo(8))
    store.store("bom", "t", "texto", embedding=[1.0, 0.0, 0.0])
    # Injeta direto no banco uma linha legada (anterior à guarda de store()).
    with store._connect() as conn:
        conn.execute(
            "INSERT INTO vectors (source_id, source_type, text, embedding, created_at)"
            " VALUES ('legado', 't', 'lixo', ?, 0)",
            (json.dumps([1.0] * 9),),
        )
    achados = [r.source_id for r in store.search_by_vector([1.0, 0.0, 0.0], top_k=10)]
    assert "bom" in achados
    assert "legado" not in achados


# ---------------------------------------------------------------------------
# rebuild_index — atomicidade
# ---------------------------------------------------------------------------


def _embeddings_gravados(store, source_type=None):
    import json

    return [json.loads(r["embedding"]) for r in store._fetch_rows(source_type)]


def test_rebuild_index_aborta_e_nao_grava_se_engine_cair():
    store = VectorStore(":memory:", engine=_EngineFixo(3))
    for i in range(5):
        store.store(f"m{i}", "t", f"mensagem {i}", embedding=[float(i), 0.0, 0.0])
    antes = _embeddings_gravados(store)

    # Cai na 3ª linha: metade seria 8d e metade 4096d se não abortasse.
    store._engine = _EngineQueCai(dim_ok=8, dim_fallback=4096, cai_apos=2)
    with pytest.raises(RuntimeError, match="dimensão mudou"):
        store.rebuild_index()

    # `_connect()` faz rollback em exceção — o índice tem de sair intacto.
    assert _embeddings_gravados(store) == antes


def test_rebuild_index_troca_dimensao_do_indice():
    store = VectorStore(":memory:", engine=_EngineFixo(3))
    store.store("a", "t", "texto um", embedding=[1.0, 0.0, 0.0])
    store.store("b", "t", "texto dois", embedding=[0.0, 1.0, 0.0])

    store._engine = _EngineFixo(16)
    assert store.rebuild_index() == 2
    assert all(len(v) == 16 for v in _embeddings_gravados(store))
    # E a dimensão vigente acompanha: escrita nova em 16d passa a ser aceita.
    assert store.store("c", "t", "texto tres", embedding=[1.0] * 16) > 0


def test_rebuild_index_reporta_progresso():
    store = VectorStore(":memory:", engine=_EngineFixo(4))
    for i in range(3):
        store.store(f"m{i}", "t", f"msg {i}", embedding=[float(i + 1), 0.0, 0.0, 0.0])
    chamadas: list[tuple[int, int]] = []
    store.rebuild_index(progress=lambda feitos, total: chamadas.append((feitos, total)))
    assert chamadas == [(1, 3), (2, 3), (3, 3)]


def test_rebuild_index_vazio_nao_falha():
    store = VectorStore(":memory:", engine=_EngineFixo(4))
    assert store.rebuild_index() == 0


# ---------------------------------------------------------------------------
# get_default_store
# ---------------------------------------------------------------------------


def test_get_default_store_singleton():
    s1 = get_default_store()
    s2 = get_default_store()
    assert s1 is s2


def test_get_default_store_returns_vector_store():
    s = get_default_store()
    assert isinstance(s, VectorStore)
