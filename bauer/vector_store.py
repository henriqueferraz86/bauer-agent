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
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Generator

from .embeddings import EmbeddingEngine, cosine_similarity, get_default_engine

log = logging.getLogger(__name__)


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


#: Intervalo mínimo entre disparos de auto-cura em segundo plano. Sem
#: cooldown, toda busca com linhas divergentes tentaria lançar uma thread
#: nova — desperdício quando a anterior ainda está rodando (rebuild paga uma
#: chamada de rede por linha) e ruído se algumas linhas nunca conseguem
#: reindexar (ex.: texto que sempre estoura o engine).
_AUTOHEAL_COOLDOWN_S = 300.0


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
        # Dimensão vigente do índice, resolvida sob demanda a partir da 1ª linha.
        # `-1` = ainda não resolvida; `0` = índice vazio (a 1ª escrita define).
        self._dim: int = -1
        # Auto-cura em segundo plano (linhas puladas em busca por dimensão
        # divergente): trava contra threads concorrentes + cooldown entre
        # tentativas. Ver `_autoheal_divergentes`.
        self._autoheal_lock = threading.Lock()
        self._autoheal_em_andamento = False
        self._last_autoheal_at: float = 0.0

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

        # Guarda de dimensão: `cosine_similarity` usa zip(), que TRUNCA no vetor
        # mais curto em vez de falhar — misturar dimensões não gera erro, gera
        # score sem significado. E misturar é fácil: `EmbeddingEngine` cai para
        # TF-IDF (4096d esparso) em SILÊNCIO quando o Ollama some, então uma
        # queda de rede no meio da indexação envenena o índice sem aviso.
        # Recusar a escrita mantém o índice coerente; o log é o aviso que o
        # fallback silencioso não dá.
        dim = len(embedding)
        vigente = self._store_dim()
        if vigente and dim != vigente:
            log.warning(
                "vector_store: escrita recusada para %s/%s — o embedding novo tem "
                "dimensão %d e o índice está em %d (backend atual=%r). Duas causas "
                "possíveis: (a) o engine caiu para o fallback TF-IDF porque o Ollama "
                "ficou inalcançável — ação: restaurar o acesso ao Ollama; ou (b) o "
                "índice é anterior à troca de engine — ação: rodar rebuild_index() "
                "para reindexar tudo com o engine atual.",
                source_type, source_id, dim, vigente, self._engine.backend,
            )
            return 0

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
        if not vigente:
            self._dim = dim  # índice estava vazio: esta escrita define a dimensão
        return cur.lastrowid or 0

    def store_if_absent(
        self,
        source_id: str,
        source_type: str,
        text: str,
        *,
        embedding: list[float] | None = None,
    ) -> int:
        """store() apenas se (source_id, source_type) ainda não existe.

        O re-index de sessão (SqliteSessionStore) itera TODAS as mensagens a
        cada save; sem este guard, cada save re-embedava a conversa inteira de
        novo (embed() é chamado dentro de store() sempre) — custo O(n²) de
        embedding por sessão. Como o source_id de mensagem é estável
        (``{session}:{role}:{idx}`` e mensagens são append-only), pular quando
        já existe é seguro e elimina o desperdício.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT rowid FROM vectors WHERE source_id = ? AND source_type = ?",
                (source_id, source_type),
            ).fetchone()
        if row is not None:
            return row[0]  # posicional: funciona p/ sqlite3.Row e tupla
        return self.store(source_id, source_type, text, embedding=embedding)

    def delete(self, source_id: str, source_type: str) -> int:
        """Delete the vector for (source_id, source_type).  Returns rows deleted."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM vectors WHERE source_id = ? AND source_type = ?",
                (source_id, source_type),
            )
        if cur.rowcount:
            self._dim = -1
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
        if cur.rowcount:
            self._dim = -1
        return cur.rowcount

    def list_source_ids(self, source_type: str) -> list[str]:
        """Todos os source_id de um source_type (para manutenção/limpeza)."""
        with self._connect() as conn:
            return [
                row[0] for row in conn.execute(
                    "SELECT source_id FROM vectors WHERE source_type = ?",
                    (source_type,),
                )
            ]

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
        scored = self._score_rows(query_vec, rows, min_score)
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
        scored = self._score_rows(query_vec, rows, min_score)
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
    # Health
    # ------------------------------------------------------------------

    def dimension_health(self) -> dict:
        """Resumo do estado de dimensão do índice, para `bauer doctor`.

        Lê `embedding` de toda linha só pra medir `len()` — aceitável aqui
        porque é um comando de diagnóstico rodado manualmente, não um
        caminho quente; a busca em si (`_score_rows`) já paga esse custo por
        query de qualquer forma.
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT embedding FROM vectors").fetchall()
        total = len(rows)
        dims: dict[int, int] = {}
        for (emb,) in rows:
            try:
                d = len(json.loads(emb))
            except Exception:
                d = -1
            dims[d] = dims.get(d, 0) + 1
        engine_dim = self._engine.dimension
        divergentes = total - dims.get(engine_dim, 0)
        return {
            "total": total,
            "engine_backend": self._engine.backend,
            "engine_dim": engine_dim,
            "dims": dims,
            "divergentes": divergentes,
        }

    # ------------------------------------------------------------------
    # Rebuild (re-embed with current engine)
    # ------------------------------------------------------------------

    def rebuild_index(
        self,
        source_type: str | None = None,
        *,
        progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Re-embed all rows (useful when switching from TF-IDF to Ollama).

        Em duas fases, de propósito:

        1. **Embeda tudo** sem tocar no banco. É a fase lenta (uma chamada de
           rede por linha) e a que pode falhar — falhar aqui não deixa rastro.
        2. **Grava numa transação só.** `_connect()` faz rollback em exceção,
           então um erro na fase 1 deixa o índice exatamente como estava.

        A versão anterior abria uma transação por linha e re-embedava em
        seguida: se o Ollama sumisse no meio, o `EmbeddingEngine` caía para
        TF-IDF em silêncio e a metade restante era gravada com outra dimensão —
        exatamente o índice misturado que a guarda de `store()` existe para
        impedir. Aqui, qualquer divergência de dimensão aborta antes de gravar.

        Args:
            source_type: Limita o rebuild a um namespace. ``None`` = tudo.
            progress: Callback opcional ``(feitos, total)`` para acompanhar.

        Returns:
            Número de linhas reindexadas.

        Raises:
            RuntimeError: Se o engine mudar de dimensão no meio do caminho
                (sinal de que caiu para o fallback) — nada é gravado.
        """
        rows = self._fetch_rows(source_type)
        total = len(rows)
        if not total:
            return 0

        pendentes: list[tuple[str, str, str]] = []
        dim_alvo = 0
        for i, row in enumerate(rows, 1):
            novo = self._engine.embed(row["text"])
            if not dim_alvo:
                dim_alvo = len(novo)
            elif len(novo) != dim_alvo:
                raise RuntimeError(
                    f"rebuild_index abortado na linha {i}/{total}: dimensão mudou "
                    f"de {dim_alvo} para {len(novo)} no meio do rebuild. Causa: o "
                    f"EmbeddingEngine caiu para o fallback (backend atual="
                    f"{self._engine.backend!r}) — provavelmente o Ollama ficou "
                    f"inalcançável. Nada foi gravado; o índice segue intacto. "
                    f"Ação: restaurar o acesso ao Ollama e rodar de novo."
                )
            pendentes.append((json.dumps(novo), row["source_id"], row["source_type"]))
            if progress is not None:
                progress(i, total)

        with self._connect() as conn:
            conn.executemany(
                "UPDATE vectors SET embedding = ? WHERE source_id = ? AND source_type = ?",
                pendentes,
            )
        self._dim = dim_alvo  # a dimensão do índice acabou de mudar
        return len(pendentes)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _store_dim(self) -> int:
        """Dimensão dos vetores já gravados (0 se o índice estiver vazio).

        Resolvida uma única vez e memorizada: `store()` roda a cada mensagem
        indexada, e uma consulta por escrita seria desperdício.
        """
        if self._dim >= 0:
            return self._dim
        self._dim = 0
        with self._connect() as conn:
            row = conn.execute("SELECT embedding FROM vectors LIMIT 1").fetchone()
        if row is not None:
            try:
                self._dim = len(json.loads(row[0]))
            except Exception:
                self._dim = 0
        return self._dim

    def _score_rows(
        self,
        query_vec: list[float],
        rows: list[sqlite3.Row],
        min_score: float,
    ) -> list[tuple[float, sqlite3.Row]]:
        """Pontua linhas contra o vetor de consulta, pulando dimensão divergente.

        Um índice legado pode já conter dimensões misturadas (gravadas antes da
        guarda em `store()`, ou enquanto o engine estava degradado). Comparar
        essas linhas produziria score truncado pelo zip() — pior que não
        retornar nada, porque entra no ranking como se fosse resultado válido.
        Pular e avisar uma vez por busca — e, se o engine estiver saudável
        agora, disparar auto-cura em segundo plano (ver `_talvez_autocurar`).
        """
        dim = len(query_vec)
        scored: list[tuple[float, sqlite3.Row]] = []
        divergentes: list[sqlite3.Row] = []
        for row in rows:
            try:
                emb = json.loads(row["embedding"])
            except Exception:
                continue
            if len(emb) != dim:
                divergentes.append(row)
                continue
            score = cosine_similarity(query_vec, emb)
            if score >= min_score:
                scored.append((score, row))
        if divergentes:
            log.warning(
                "vector_store: %d de %d linhas ignoradas na busca — dimensão "
                "divergente da consulta (%d). Causa: essas linhas foram gravadas "
                "com outro engine de embedding (provavelmente TF-IDF, enquanto "
                "o Ollama estava fora do ar). Ação: rode `bauer doctor` para "
                "diagnóstico e comandos de correção — se o Ollama já estiver "
                "acessível agora (%r), a reindexação dessas linhas já foi "
                "agendada automaticamente em segundo plano.",
                len(divergentes), len(rows), dim, self._engine.backend,
            )
            self._talvez_autocurar(divergentes)
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _talvez_autocurar(self, divergentes: list[sqlite3.Row]) -> None:
        """Agenda `_autoheal_divergentes` em thread própria, se elegível.

        Elegível = engine saudável agora (senão reindexaria com o MESMO
        engine degradado que causou o problema — reescreveria TF-IDF em cima
        de TF-IDF), nenhuma auto-cura já rodando, e cooldown vencido. Existe
        para o usuário nunca precisar saber que `rebuild_index()` existe —
        era o "sem precisar rodar nada" que faltava depois do re-probe do
        `EmbeddingEngine` (que só recupera a DETECÇÃO, não reindexa sozinho).
        """
        if self._engine.backend != "ollama":
            return
        agora = time.monotonic()
        with self._autoheal_lock:
            if self._autoheal_em_andamento:
                return
            if agora - self._last_autoheal_at < _AUTOHEAL_COOLDOWN_S:
                return
            self._autoheal_em_andamento = True
            self._last_autoheal_at = agora
        t = threading.Thread(
            target=self._autoheal_divergentes,
            args=(divergentes,),
            name="vector_store-autoheal",
            daemon=True,
        )
        t.start()

    def _autoheal_divergentes(self, rows: list[sqlite3.Row]) -> None:
        """Reindexa em segundo plano só as linhas puladas (não a base inteira).

        Mesmo padrão de segurança do `rebuild_index`: embeda tudo primeiro,
        grava numa transação só, e ABORTA sem gravar nada se o engine
        degradar no meio (sinal de que a recuperação foi só um blip — melhor
        tentar de novo depois do que gravar uma mistura nova)."""
        try:
            dim_alvo = self._engine.dimension
            pendentes: list[tuple[str, str, str]] = []
            for row in rows:
                novo = self._engine.embed(row["text"])
                if len(novo) != dim_alvo:
                    log.warning(
                        "vector_store: auto-cura abortada — engine degradou no "
                        "meio do reprocessamento (esperava %d dims, veio %d). "
                        "Nada foi gravado; tentará de novo na próxima busca com "
                        "linhas divergentes.",
                        dim_alvo, len(novo),
                    )
                    return
                pendentes.append((json.dumps(novo), row["source_id"], row["source_type"]))
            with self._connect() as conn:
                conn.executemany(
                    "UPDATE vectors SET embedding = ? WHERE source_id = ? AND source_type = ?",
                    pendentes,
                )
            log.warning(
                "vector_store: %d linha(s) reindexada(s) automaticamente em "
                "segundo plano (estavam com dimensão divergente, agora em %d "
                "dims) — nenhuma ação manual foi necessária.",
                len(pendentes), dim_alvo,
            )
        finally:
            with self._autoheal_lock:
                self._autoheal_em_andamento = False

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
