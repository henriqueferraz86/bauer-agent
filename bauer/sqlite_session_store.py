"""SQLite-backed session store com FTS5 para busca full-text (MEM-1).

Substitui o SessionStore baseado em JSONL por SQLite com:
- sessions  : metadados por sessão (id, created_at, updated_at, message_count, summary)
- messages  : mensagens indexadas por sessão e posição (rowid, session_id, idx, role, content, ts)
- messages_fts : FTS5 virtual table para busca full-text (triggers mantêm sincronizado)

Migration automática: na primeira abertura, importa arquivos .jsonl existentes.
Interface compatível com SessionStore (save/load/list_sessions/delete/exists).
Método extra: search_sessions(query, top_k, role_filter).

SQLite WAL mode + foreign keys habilitados.
FTS5 degradado graciosamente para LIKE se a compilação não suportar.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fts5_available(conn: sqlite3.Connection) -> bool:
    """Testa se FTS5 está compilado neste SQLite. Limpa após o teste."""
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _content_to_str(content: Any) -> str:
    """Converte content de mensagem para str.

    Suporta:
    - str puro (OpenAI / Ollama)
    - list de blocks [{type, text}] (Anthropic)
    - None → ''
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# DDL helpers — separado para facilitar testes unitários
# ---------------------------------------------------------------------------

_DDL_BASE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    summary      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS messages (
    rowid      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    ts         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, idx);
"""

_DDL_FTS5 = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    session_id UNINDEXED,
    role       UNINDEXED,
    content    = 'messages',
    content_rowid = 'rowid',
    tokenize  = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS messages_ai
AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, session_id, role)
    VALUES (new.rowid, new.content, new.session_id, new.role);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad
AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, session_id, role)
    VALUES ('delete', old.rowid, old.content, old.session_id, old.role);
END;

CREATE TRIGGER IF NOT EXISTS messages_au
AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, session_id, role)
    VALUES ('delete', old.rowid, old.content, old.session_id, old.role);
    INSERT INTO messages_fts(rowid, content, session_id, role)
    VALUES (new.rowid, new.content, new.session_id, new.role);
END;
"""


# ---------------------------------------------------------------------------
# SqliteSessionStore
# ---------------------------------------------------------------------------

class SqliteSessionStore:
    """SQLite session store com FTS5 full-text search.

    Interface compatível com SessionStore (drop-in replacement).

    Exemplo:
        store = SqliteSessionStore("memory/sessions")
        sid = store.new_id()
        store.save(sid, [{"role": "user", "content": "oi"}])
        results = store.search_sessions("oi", top_k=5)
    """

    def __init__(self, sessions_dir: str | Path = "memory/sessions") -> None:
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "sessions.db"
        self._has_fts5: bool = False
        self._init_db()
        self._migrate_jsonl()

    # ------------------------------------------------------------------
    # Conexão (nova por chamada — thread-safe sem lock global)
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Inicialização do schema
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            with conn:
                for stmt in _DDL_BASE.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)

            has_fts5 = _fts5_available(conn)
            self._has_fts5 = has_fts5
            if has_fts5:
                # executescript usa commit automático — não pode estar dentro de 'with conn'
                conn.executescript(_DDL_FTS5)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Migração de JSONL legados
    # ------------------------------------------------------------------

    def _migrate_jsonl(self) -> int:
        """Importa arquivos .jsonl existentes para o SQLite.

        Idempotente: sessões já presentes no banco são ignoradas.
        Retorna o número de sessões migradas.
        """
        migrated = 0
        conn = self._connect()
        try:
            existing = {
                row[0]
                for row in conn.execute("SELECT session_id FROM sessions")
            }
            for jsonl_path in sorted(self.dir.glob("*.jsonl")):
                sid = jsonl_path.stem
                if sid in existing:
                    continue
                messages: list[dict] = []
                for line in jsonl_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines():
                    line = line.strip()
                    if line:
                        try:
                            messages.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                if messages:
                    self._save_to_conn(conn, sid, messages)
                    migrated += 1
        finally:
            conn.close()
        return migrated

    # ------------------------------------------------------------------
    # Interface pública — compatível com SessionStore
    # ------------------------------------------------------------------

    def new_id(self) -> str:
        return str(uuid.uuid4())[:8]

    def save(self, session_id: str, messages: list[dict]) -> None:
        conn = self._connect()
        try:
            self._save_to_conn(conn, session_id, messages)
        finally:
            conn.close()

    def load(self, session_id: str) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id=? ORDER BY idx",
                (session_id,),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]
        finally:
            conn.close()

    def list_sessions(self) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_id FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def delete(self, session_id: str) -> bool:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE session_id=?", (session_id,)
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    def exists(self, session_id: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Metadados e resumo
    # ------------------------------------------------------------------

    def get_metadata(self, session_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_summary(self, session_id: str, summary: str) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE sessions SET summary=?, updated_at=? WHERE session_id=?",
                    (summary, _now_iso(), session_id),
                )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Busca full-text (FTS5 com fallback LIKE)
    # ------------------------------------------------------------------

    def search_sessions(
        self,
        query: str,
        top_k: int = 5,
        role_filter: str | None = None,
        *,
        use_vectors: bool = True,
    ) -> list[dict]:
        """Busca mensagens usando vector similarity (RAG), FTS5, ou LIKE como fallback.

        Ordem de preferência:
        1. VectorStore (embedding semântico) — retorna resultados ranqueados por cosseno.
        2. FTS5 full-text search (se disponível na compilação SQLite).
        3. LIKE simples (fallback universal).

        Args:
            query: Termos de busca.
                   FTS5 suporta: AND, OR, NOT, prefix*, "frase exata".
                   Termos simples são envolvidos em aspas automaticamente.
            top_k: Número máximo de sessões únicas retornadas.
            role_filter: Filtra por role ('user', 'assistant', 'tool').
            use_vectors: Se False, pula VectorStore e vai direto para FTS5/LIKE.

        Returns:
            Lista de dicts com keys: session_id, role, snippet, rank, updated_at.
            Ordenado do mais relevante ao menos.
        """
        if not query or not query.strip():
            return []

        # 1. Tenta busca semântica via VectorStore
        if use_vectors:
            try:
                vector_results = self._vector_search(query, top_k, role_filter)
                if vector_results:
                    return vector_results
            except Exception:
                pass  # Fallback silencioso para FTS5/LIKE

        conn = self._connect()
        try:
            if self._has_fts5:
                return self._fts5_search(conn, query, top_k, role_filter)
            return self._like_search(conn, query, top_k, role_filter)
        except sqlite3.OperationalError:
            # Fallback se FTS5 falhar inesperadamente em runtime
            return self._like_search(conn, query, top_k, role_filter)
        finally:
            conn.close()

    def _vector_search(
        self,
        query: str,
        top_k: int,
        role_filter: str | None,
    ) -> list[dict]:
        """Busca semântica via VectorStore (EmbeddingEngine + cosine similarity).

        Retorna lista vazia se VectorStore não estiver disponível ou não tiver
        entradas suficientes para produzir resultado confiável.
        """
        from .vector_store import get_default_store as _get_store
        store = _get_store()

        # Fonte dos vetores: "session_msg" com source_id = "{session_id}:{role}:{idx}"
        source_type = "session_msg"
        if store.count(source_type) == 0:
            return []  # Ainda sem vetores indexados

        results = store.search(
            query,
            top_k=top_k * 4,  # busca mais para poder filtrar + deduplicar
            source_type=source_type,
            min_score=0.15,    # threshold mínimo para evitar noise
        )

        # Extrai session_id e filtra por role se necessário
        conn = self._connect()
        try:
            seen: dict[str, dict] = {}
            for r in results:
                # source_id format: "{session_id}:{role}:{idx}"
                parts = r.source_id.split(":", 2)
                if len(parts) < 3:
                    continue
                sid, role, _ = parts
                if role_filter and role != role_filter:
                    continue
                if sid in seen:
                    continue
                # Pega updated_at da tabela sessions
                row = conn.execute(
                    "SELECT updated_at FROM sessions WHERE session_id=?", (sid,)
                ).fetchone()
                updated_at = row["updated_at"] if row else ""
                seen[sid] = {
                    "session_id": sid,
                    "role": role,
                    "snippet": r.text[:200].replace("\n", " "),
                    "rank": float(r.score),   # higher = better (reversed from FTS5)
                    "updated_at": updated_at,
                }
                if len(seen) >= top_k:
                    break
        finally:
            conn.close()

        # Ordena por score desc (melhor similaridade primeiro)
        return sorted(seen.values(), key=lambda x: x["rank"], reverse=True)

    def _fts5_search(
        self,
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
        role_filter: str | None,
    ) -> list[dict]:
        # Envolve em aspas se não for sintaxe FTS5 explícita
        fts5_ops = {"AND", "OR", "NOT"}
        is_explicit = (
            any(op in query for op in fts5_ops)
            or '"' in query
            or "*" in query
        )
        safe_query = query if is_explicit else f'"{query}"'

        role_clause = "AND m.role = ?" if role_filter else ""
        sql = f"""
            SELECT
                m.session_id,
                m.role,
                m.content,
                s.updated_at,
                fts.rank AS fts_rank
            FROM messages_fts fts
            JOIN messages m  ON m.rowid = fts.rowid
            JOIN sessions s  ON s.session_id = m.session_id
            WHERE messages_fts MATCH ?
            {role_clause}
            ORDER BY fts.rank
            LIMIT ?
        """
        params: list[Any] = [safe_query]
        if role_filter:
            params.append(role_filter)
        # Busca mais linhas para poder deduplicar por sessão
        params.append(top_k * 5)

        rows = conn.execute(sql, params).fetchall()

        # Deduplica — mantém primeiro match (melhor rank) por sessão
        seen: dict[str, dict] = {}
        for row in rows:
            sid = row["session_id"]
            if sid not in seen:
                seen[sid] = {
                    "session_id": sid,
                    "role": row["role"],
                    "snippet": row["content"][:200].replace("\n", " "),
                    "rank": float(row["fts_rank"]),
                    "updated_at": row["updated_at"],
                }

        # Já vem ordenado por rank do FTS5 — preserva ordem
        return list(seen.values())[:top_k]

    def _like_search(
        self,
        conn: sqlite3.Connection,
        query: str,
        top_k: int,
        role_filter: str | None,
    ) -> list[dict]:
        role_clause = "AND m.role = ?" if role_filter else ""
        sql = f"""
            SELECT DISTINCT
                m.session_id,
                m.role,
                m.content,
                s.updated_at
            FROM messages m
            JOIN sessions s ON s.session_id = m.session_id
            WHERE m.content LIKE ?
            {role_clause}
            ORDER BY s.updated_at DESC
            LIMIT ?
        """
        params: list[Any] = [f"%{query}%"]
        if role_filter:
            params.append(role_filter)
        params.append(top_k)

        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "session_id": r["session_id"],
                "role": r["role"],
                "snippet": r["content"][:200].replace("\n", " "),
                "rank": 0.0,
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Interno — escrita com deduplicação via DELETE + INSERT
    # ------------------------------------------------------------------

    def _save_to_conn(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        messages: list[dict],
    ) -> None:
        now = _now_iso()
        with conn:
            conn.execute(
                """
                INSERT INTO sessions(session_id, created_at, updated_at, message_count)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at    = excluded.updated_at,
                    message_count = excluded.message_count
                """,
                (session_id, now, now, len(messages)),
            )
            # Apaga mensagens antigas (triggers cuidam do FTS5)
            conn.execute(
                "DELETE FROM messages WHERE session_id=?", (session_id,)
            )
            for idx, msg in enumerate(messages):
                role = msg.get("role", "")
                content = _content_to_str(msg.get("content", ""))
                # Sanitiza surrogates antes do bind ao SQLite — evita
                # UnicodeEncodeError quando o C-binding tenta codificar UTF-8.
                if isinstance(content, str):
                    content = content.encode("utf-8", errors="replace").decode("utf-8")
                if isinstance(role, str):
                    role = role.encode("utf-8", errors="replace").decode("utf-8")
                conn.execute(
                    "INSERT INTO messages(session_id, idx, role, content, ts) "
                    "VALUES (?,?,?,?,?)",
                    (session_id, idx, role, content, now),
                )

        # Asynchronously index new messages in VectorStore for semantic search
        import threading as _threading

        def _index_in_background() -> None:
            try:
                from .vector_store import get_default_store as _get_store
                _store = _get_store()
                for _idx, _msg in enumerate(messages):
                    _role = _msg.get("role", "")
                    _content = _content_to_str(_msg.get("content", ""))
                    if not _content.strip():
                        continue
                    _source_id = f"{session_id}:{_role}:{_idx}"
                    _store.store(_source_id, "session_msg", _content)
            except Exception:
                pass  # Never raise in background thread

        _t = _threading.Thread(target=_index_in_background, daemon=True)
        _t.start()
