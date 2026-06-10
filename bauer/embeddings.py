"""Embeddings engine — semantic vector representations with graceful fallback.

Tries Ollama ``/api/embeddings`` first (requires a local embedding model such
as ``nomic-embed-text`` or ``mxbai-embed-large``).  Falls back to a TF-IDF
sparse-vector representation when Ollama is unavailable or has no embedding
model loaded.  The fallback is silent — callers always get a list[float] back.

Usage::

    from bauer.embeddings import EmbeddingEngine

    engine = EmbeddingEngine()
    v1 = engine.embed("deploy failed on kubernetes")
    v2 = engine.embed("pod crash loop back-off")

    score = EmbeddingEngine.cosine(v1, v2)
    # → 0.82 (high similarity even though words differ)

The engine auto-detects the best available backend::

    engine.backend   # "ollama" | "tfidf"
"""

from __future__ import annotations

import math
import re
import threading
from collections import Counter
from typing import Sequence

# ---------------------------------------------------------------------------
# Pure-Python cosine similarity (no numpy dependency)
# ---------------------------------------------------------------------------


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(sum(x * x for x in a))


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length float vectors.

    Returns a value in ``[-1, 1]`` (dense) or ``[0, 1]`` (TF-IDF, non-negative).
    Returns ``0.0`` for zero vectors.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    na, nb = _norm(a), _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return _dot(a, b) / (na * nb)


# ---------------------------------------------------------------------------
# TF-IDF backend (zero dependencies, pure Python)
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset(
    "a an the is are was were be been being have has had do does did "
    "will would could should may might must shall can of in on at to "
    "for with from by about and or not no nor so yet both either "
    "neither nor as if while although because since though unless until "
    "o a as de da do e em para por se um uma".split()
)

_VOCAB_SIZE = 4096  # fixed vocabulary size for TF-IDF sparse vectors


def _tokenize(text: str) -> list[str]:
    text = text.lower()
    tokens = re.findall(r"\b[a-záàâãéèêíìîóòôõúùûçñü_]+\b", text)
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _tfidf_vector(text: str) -> list[float]:
    """Return a fixed-length TF-IDF vector using FNV-32 hashing."""
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * _VOCAB_SIZE
    tf = Counter(tokens)
    total = len(tokens)
    vec = [0.0] * _VOCAB_SIZE
    for tok, count in tf.items():
        # FNV-32 hash for vocabulary bucketing (deterministic, no collisions needed)
        h = 2166136261
        for ch in tok.encode():
            h = ((h ^ ch) * 16777619) & 0xFFFFFFFF
        idx = h % _VOCAB_SIZE
        # TF × log(1 + 1/(count/total)) — simple IDF approximation
        vec[idx] += (count / total) * math.log1p(total / count)
    # L2 normalise
    n = _norm(vec)
    if n > 0:
        vec = [v / n for v in vec]
    return vec


# ---------------------------------------------------------------------------
# Ollama backend probe
# ---------------------------------------------------------------------------

_OLLAMA_EMBED_MODELS = [
    "nomic-embed-text",
    "mxbai-embed-large",
    "all-minilm",
    "snowflake-arctic-embed",
    "bge-m3",
    "nomic-embed-text:latest",
]


def _probe_ollama_embeddings(base_url: str = "http://localhost:11434") -> str | None:
    """Return the name of the first available Ollama embedding model, or None."""
    try:
        import httpx
        resp = httpx.get(f"{base_url}/api/tags", timeout=3.0)
        if resp.status_code != 200:
            return None
        tags = resp.json()
        names = {m["name"].split(":")[0] for m in tags.get("models", [])}
        for candidate in _OLLAMA_EMBED_MODELS:
            base = candidate.split(":")[0]
            if base in names:
                return candidate
    except Exception:
        pass
    return None


def _ollama_embed(text: str, model: str, base_url: str) -> list[float] | None:
    """Call Ollama /api/embeddings and return the vector, or None on error."""
    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=15.0,
        )
        if resp.status_code != 200:
            return None
        return resp.json().get("embedding")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# EmbeddingEngine
# ---------------------------------------------------------------------------


class EmbeddingEngine:
    """Embedding engine with Ollama (dense) → TF-IDF (sparse) fallback.

    Thread-safe.  The backend is auto-detected on first ``embed()`` call.

    Parameters
    ----------
    ollama_base_url:
        Base URL for the Ollama server.  Defaults to ``http://localhost:11434``.
    force_backend:
        ``"ollama"`` or ``"tfidf"`` to skip auto-detection.
    """

    def __init__(
        self,
        ollama_base_url: str = "http://localhost:11434",
        force_backend: str | None = None,
    ) -> None:
        self._base_url = ollama_base_url.rstrip("/")
        self._force_backend = force_backend
        self._backend: str | None = force_backend  # "ollama" | "tfidf" | None (not yet detected)
        self._ollama_model: str | None = None
        self._dim: int = 0
        self._lock = threading.Lock()
        self._detected = force_backend is not None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def backend(self) -> str:
        """``'ollama'`` or ``'tfidf'`` — resolved after first embed()."""
        self._ensure_detected()
        return self._backend or "tfidf"

    @property
    def dimension(self) -> int:
        """Vector dimension: 768-1536 for Ollama, :data:`_VOCAB_SIZE` for TF-IDF."""
        self._ensure_detected()
        return self._dim or _VOCAB_SIZE

    def embed(self, text: str) -> list[float]:
        """Return a normalized float vector for *text*.

        Never raises — falls back to TF-IDF on any Ollama error.
        """
        self._ensure_detected()
        if self._backend == "ollama" and self._ollama_model:
            vec = _ollama_embed(text, self._ollama_model, self._base_url)
            if vec is not None:
                return vec
            # Runtime error — silently downgrade
            with self._lock:
                self._backend = "tfidf"
        return _tfidf_vector(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts.  Calls ``embed()`` for each (no batching API)."""
        return [self.embed(t) for t in texts]

    @staticmethod
    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        """Cosine similarity between two vectors. Alias for :func:`cosine_similarity`."""
        return cosine_similarity(a, b)

    def rank(
        self,
        query: str,
        candidates: list[str],
        *,
        top_k: int = 5,
    ) -> list[tuple[int, float]]:
        """Return the top-k (index, score) pairs from *candidates* by similarity to *query*.

        Example::

            engine = EmbeddingEngine()
            ranked = engine.rank("deploy failure", ["build ok", "pod crash", "lint pass"])
            # [(1, 0.87), (0, 0.23), (2, 0.11)]
        """
        if not candidates:
            return []
        q_vec = self.embed(query)
        scored = [
            (i, cosine_similarity(q_vec, self.embed(c)))
            for i, c in enumerate(candidates)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def _ensure_detected(self) -> None:
        if self._detected:
            return
        with self._lock:
            if self._detected:
                return
            self._detected = True
            if self._force_backend == "tfidf":
                self._backend = "tfidf"
                self._dim = _VOCAB_SIZE
                return
            model = _probe_ollama_embeddings(self._base_url)
            if model:
                # Verify it actually works with a short probe text
                vec = _ollama_embed("test", model, self._base_url)
                if vec and len(vec) > 0:
                    self._backend = "ollama"
                    self._ollama_model = model
                    self._dim = len(vec)
                    return
            self._backend = "tfidf"
            self._dim = _VOCAB_SIZE


# ---------------------------------------------------------------------------
# Module-level default instance
# ---------------------------------------------------------------------------

#: Shared EmbeddingEngine for the whole process.
#: Import and call directly::
#:
#:     from bauer.embeddings import default_engine
#:     vec = default_engine.embed("some text")
default_engine: EmbeddingEngine | None = None
_engine_lock = threading.Lock()


def get_default_engine(ollama_base_url: str = "http://localhost:11434") -> EmbeddingEngine:
    """Return the shared :class:`EmbeddingEngine`, creating it on first call."""
    global default_engine
    if default_engine is None:
        with _engine_lock:
            if default_engine is None:
                default_engine = EmbeddingEngine(ollama_base_url=ollama_base_url)
    return default_engine
