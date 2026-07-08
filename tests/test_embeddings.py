"""Tests for bauer/embeddings.py — EmbeddingEngine + TF-IDF fallback."""

from __future__ import annotations

import math
import pytest

from bauer.embeddings import (
    EmbeddingEngine,
    cosine_similarity,
    _tfidf_vector,
    _tokenize,
    _VOCAB_SIZE,
    get_default_engine,
)


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-6)


def test_cosine_orthogonal_vectors():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)


def test_cosine_zero_vector():
    a = [0.0, 0.0]
    b = [1.0, 2.0]
    assert cosine_similarity(a, b) == 0.0


def test_cosine_empty_vectors():
    assert cosine_similarity([], []) == 0.0


def test_cosine_mismatched_lengths():
    # Should return 0.0 not raise
    assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_cosine_range():
    import random
    rng = random.Random(42)
    for _ in range(20):
        a = [rng.uniform(-1, 1) for _ in range(10)]
        b = [rng.uniform(-1, 1) for _ in range(10)]
        score = cosine_similarity(a, b)
        assert -1.001 <= score <= 1.001, f"Out of range: {score}"


# ---------------------------------------------------------------------------
# _tokenize
# ---------------------------------------------------------------------------


def test_tokenize_basic():
    tokens = _tokenize("The quick brown fox")
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens
    # "The" should be filtered (stop word) or lowercased
    assert "The" not in tokens
    assert "the" not in tokens  # stop word


def test_tokenize_punctuation_stripped():
    tokens = _tokenize("hello, world! python3.11")
    assert "hello" in tokens
    assert "world" in tokens


def test_tokenize_empty():
    assert _tokenize("") == []


def test_tokenize_only_stopwords():
    tokens = _tokenize("the is a an of")
    # All are stop words — result should be empty or near-empty
    for tok in tokens:
        assert len(tok) > 1  # short stop words removed


# ---------------------------------------------------------------------------
# _tfidf_vector
# ---------------------------------------------------------------------------


def test_tfidf_vector_length():
    v = _tfidf_vector("hello world python")
    assert len(v) == _VOCAB_SIZE


def test_tfidf_vector_normalized():
    v = _tfidf_vector("machine learning models train test evaluate")
    norm = math.sqrt(sum(x * x for x in v))
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_tfidf_vector_empty_text():
    v = _tfidf_vector("")
    assert len(v) == _VOCAB_SIZE
    assert all(x == 0.0 for x in v)


def test_tfidf_similar_texts_closer_than_different():
    v1 = _tfidf_vector("kubernetes pod crash restart error")
    v2 = _tfidf_vector("kubernetes container restart failure")
    v3 = _tfidf_vector("apple pie recipe baking flour")
    sim_related = cosine_similarity(v1, v2)
    sim_unrelated = cosine_similarity(v1, v3)
    assert sim_related > sim_unrelated


def test_tfidf_deterministic():
    text = "deploy kubernetes pod failure restart"
    v1 = _tfidf_vector(text)
    v2 = _tfidf_vector(text)
    assert v1 == v2


# ---------------------------------------------------------------------------
# EmbeddingEngine — TF-IDF backend
# ---------------------------------------------------------------------------


@pytest.fixture
def tfidf_engine():
    """EmbeddingEngine forced to TF-IDF (no Ollama probe)."""
    return EmbeddingEngine(force_backend="tfidf")


def test_engine_backend_tfidf(tfidf_engine):
    assert tfidf_engine.backend == "tfidf"


def test_engine_dimension_tfidf(tfidf_engine):
    assert tfidf_engine.dimension == _VOCAB_SIZE


def test_engine_embed_returns_list_of_floats(tfidf_engine):
    v = tfidf_engine.embed("hello world")
    assert isinstance(v, list)
    assert all(isinstance(x, float) for x in v)
    assert len(v) == _VOCAB_SIZE


def test_engine_embed_never_raises(tfidf_engine):
    # Even with empty text, should return zero vector, not raise
    v = tfidf_engine.embed("")
    assert len(v) == _VOCAB_SIZE


def test_engine_embed_batch(tfidf_engine):
    texts = ["first text", "second text", "third"]
    vecs = tfidf_engine.embed_batch(texts)
    assert len(vecs) == 3
    assert all(len(v) == _VOCAB_SIZE for v in vecs)


def test_engine_cosine_static(tfidf_engine):
    a = tfidf_engine.embed("dog cat animal")
    b = tfidf_engine.embed("feline canine pets")
    score = EmbeddingEngine.cosine(a, b)
    assert 0.0 <= score <= 1.0


def test_engine_rank(tfidf_engine):
    query = "kubernetes pod failure"
    candidates = [
        "apple pie recipe",
        "kubernetes container crash restart",
        "kubernetes pod error",
        "baking bread flour yeast",
    ]
    ranked = tfidf_engine.rank(query, candidates, top_k=2)
    assert len(ranked) == 2
    # The kubernetes-related items should rank first
    top_idx = ranked[0][0]
    assert top_idx in (1, 2)  # "crash restart" or "pod error"


def test_engine_rank_empty_candidates(tfidf_engine):
    ranked = tfidf_engine.rank("anything", [])
    assert ranked == []


def test_engine_rank_respects_top_k(tfidf_engine):
    candidates = ["a", "b", "c", "d", "e"]
    ranked = tfidf_engine.rank("test", candidates, top_k=3)
    assert len(ranked) <= 3


# ---------------------------------------------------------------------------
# EmbeddingEngine — thread safety
# ---------------------------------------------------------------------------


def test_engine_thread_safe_embed():
    import threading
    engine = EmbeddingEngine(force_backend="tfidf")
    results: list[list[float]] = []
    errors: list[Exception] = []

    def _embed():
        try:
            v = engine.embed("concurrent embedding test text")
            results.append(v)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_embed) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors
    assert len(results) == 10
    # All embeddings should be identical (same text)
    assert all(results[i] == results[0] for i in range(1, 10))


# ---------------------------------------------------------------------------
# get_default_engine
# ---------------------------------------------------------------------------


def test_get_default_engine_returns_engine():
    engine = get_default_engine()
    assert isinstance(engine, EmbeddingEngine)


def test_get_default_engine_singleton():
    e1 = get_default_engine()
    e2 = get_default_engine()
    assert e1 is e2
