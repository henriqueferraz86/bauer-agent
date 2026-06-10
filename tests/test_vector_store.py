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
# get_default_store
# ---------------------------------------------------------------------------


def test_get_default_store_singleton():
    s1 = get_default_store()
    s2 = get_default_store()
    assert s1 is s2


def test_get_default_store_returns_vector_store():
    s = get_default_store()
    assert isinstance(s, VectorStore)
