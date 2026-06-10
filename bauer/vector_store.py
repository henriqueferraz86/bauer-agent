"""Vector store — SQLite-backed semantic similarity search.

Stores text chunks alongside their embedding vectors and enables cosine
similarity search with optional source-type filtering.

Embeddings are stored as JSON-encoded float arrays in a BLOB column so
there are zero additional dependencies beyond the stdlib ``sqlite3`` and
the project's own :mod:`bauer.embeddings` module.

Usage::

    from bauer.vector_store import VectorStore
    from bauer.embeddings import EmbeddingEngine

    engine = EmbeddingEngine()
    store  = VectorStore(":memory:", engine=engine)

    store.store("msg_001", "session", "the kubernetes pod kept crashing")
    store.store("msg_002", "session", "pod loop restart back-off error")
    store.store("dec_001", "decision", "use Helm for k8s deployments")

    results = store.search("deploy failure", top_k=3)
    for r in results:
        print(r.source_id, r.score, r.text[:60])

The ``rebuild_index`` method re-embeds all rows in a given source_type,
useful when you switch from TF-IDF to Ollama embeddings.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from .embeddings import EmbeddingEngine, cosine_similarity, get_default_engine


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT    NOT NULL,
    source_type TEXT    NOT NULL DEFAULT 'generic',
    text        TEXT    NOT NULL,
    embedding   TEXT    NOT NULL,   -- JSON-encoded list[float]
    created_at  REAL    NOT NULL,
    UNIQUE(source_id, source_type)
);
CREATE INDEX IF NOT EXISTS vec_source ON vectors(source_type);
CREATE INDEX IF NOT EXISTS vec_time   ON vectors(created_at DESC);
"""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """One similarity search result."""

    source_id: str
    source_type: str
    text: str
    score: float
    created_at: float

    def __repr__(self) -> str:
        return (
            f"SearchResult(source_id={self.source_id!r}, "
            f"score={self.score:.3f}, text={self.text[:50]!r})"
        )


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------


class VectorStore:
    """SQLite-backed vector store.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Use ``:memory:`` for tests.
    engine:
        :class:`~bauer.embeddings.EmbeddingEngine` to use for new embeddings.
        Defaults to the shared :func:`~bauer.embeddings.get_default_engine` instance.
    """

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        *,
        engine: EmbeddingEngine | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._engine = engine or get_default_engine()
        self._mem_conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._init_schema()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store(
        self,
        source_id: str,
        source_type: str,
        text: str,
        *,
        embedding: list[float] | None = None,
    ) -> int:
        """Insert or replace a vector for (source_id, source_type).

        Parameters
        ----------
        source_id:
            Unique identifier within the source_type (e.g. message id).
        source_type:
            Namespace for filtering (``"session"``, ``"decision"``, etc.).
        text:
            Raw text used to generate the embedding.
        embedding:
            Pre-computed embedding.  If ``None``, computed from *text*.

        Returns the rowid of the inserted/replaced row.
        """
        if embedding is None:
            embedding = self._engine.embed(text)
        emb_json = json.dumps(embedding)
        now = time.time()

        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO vectors
                    (source_id, source_type, text, embedding, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source_id, source_type, text, emb_json, now),
            )
        return cur.lastrowid or 0

    def delete(self, source_id: str, source_type: str) -> int:
        """Delete the vector for (source_id, source_type).  Returns rows deleted."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM vectors WHERE source_id = ? AND source_type = ?",
                (source_id, source_type),
            )
        return cur.rowcount

    def delete_prefix(self, source_id_prefix: str, source_type: str) -> int:
        """Delete all vectors whose source_id starts with the prefix.

        Sessões indexam mensagens como "{session_id}:{role}:{idx}" — deletar
        a sessão exige remover todas. Sem isto, a busca semântica continuava
        retornando sessões já deletadas (bug real pego por
        test_search_after_delete em 2026-06-10).
        """
        like = source_id_prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
        with self._connect() as conn:
            cur = conn.execute(
                r"DELETE FROM vectors WHERE source_id LIKE ? ESCAPE '\' AND source_type = ?",
                (like, source_type),
            )
        return cur.rowcount

    def count(self, source_type: str | None = None) -> int:
        """Return total number of stored vectors, optionally filtered by source_type."""
        with self._connect() as conn:
            if source_type:
                return conn.execute(
                    "SELECT COUNT(*) FROM vectors WHERE source_type = ?",
                    (source_type,),
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_type: str | None = None,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """Return up to *top_k* results most similar to *query*.

        Parameters
        ----------
        query:
            Natural-language query text.
        top_k:
            Maximum number of results to return.
        source_type:
            If given, only search within this namespace.
        min_score:
            Minimum cosine similarity threshold (0–1).  ``0.0`` = no filter.
        """
        query_vec = self._engine.embed(query)
        rows = self._fetch_rows(source_type)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            try:
                emb = json.loads(row["embedding"])
            except Exception:
                continue
            score = cosine_similarity(query_vec, emb)
            if score >= min_score:
                scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, row in scored[:top_k]:
            results.append(SearchResult(
                source_id=row["source_id"],
                source_type=row["source_type"],
                text=row["text"],
                score=score,
                created_at=row["created_at"],
            ))
        return results

    def search_by_vector(
        self,
        query_vec: list[float],
        *,
        top_k: int = 5,
        source_type: str | None = None,
        min_score: float = 0.0,
    ) -> list[SearchResult]:
        """Same as :meth:`search` but accepts a pre-computed query vector."""
        rows = self._fetch_rows(source_type)
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            try:
                emb = json.loads(row["embedding"])
            except Exception:
                continue
            score = cosine_similarity(query_vec, emb)
            if score >= min_score:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            SearchResult(
                source_id=row["source_id"],
                source_type=row["source_type"],
                text=row["text"],
                score=score,
                created_at=row["created_at"],
            )
            for score, row in scored[:top_k]
        ]

    # ------------------------------------------------------------------
    # Rebuild (re-embed with current engine)
    # ------------------------------------------------------------------

    def rebuild_index(self, source_type: str | None = None) -> int:
        """Re-embed all rows (useful when switching from TF-IDF to Ollama).

        Returns the number of rows updated.
        """
        rows = self._fetch_rows(source_type)
        count = 0
        for row in rows:
            new_emb = self._engine.embed(row["text"])
            with self._connect() as conn:
                conn.execute(
                    "UPDATE vectors SET embedding = ? WHERE source_id = ? AND source_type = ?",
                    (json.dumps(new_emb), row["source_id"], row["source_type"]),
                )
            count += 1
        return count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_rows(self, source_type: str | None) -> list[sqlite3.Row]:
        with self._connect() as conn:
            if source_type:
                return conn.execute(
                    "SELECT * FROM vectors WHERE source_type = ?",
                    (source_type,),
                ).fetchall()
            return conn.execute("SELECT * FROM vectors").fetchall()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        if self._mem_conn is not None:
            with self._lock:
                try:
                    yield self._mem_conn
                    self._mem_conn.commit()
                except Exception:
                    self._mem_conn.rollback()
                    raise
            return
        conn = sqlite3.connect(self._db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Module-level default store
# ---------------------------------------------------------------------------

_default_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_default_store(db_path: str | None = None) -> VectorStore:
    """Return the shared :class:`VectorStore`, creating it on first call.

    The default path is ``~/.bauer/vector_store.db``.
    """
    global _default_store
    if _default_store is None:
        with _store_lock:
            if _default_store is None:
                if db_path is None:
                    import os
                    base = Path(
                        os.environ.get("BAUER_HOME", str(Path.home() / ".bauer"))
                    )
                    db_path = str(base / "vector_store.db")
                _default_store = VectorStore(db_path)
    return _default_store
