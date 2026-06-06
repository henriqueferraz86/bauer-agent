"""SQLite FTS index for Markdown memory files."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryHit:
    file: str
    title: str
    snippet: str
    score: float


class MemoryIndex:
    """Deterministic index over memory/*.md while preserving Markdown as source."""

    def __init__(self, memory_dir: str | Path = "memory"):
        self.memory_dir = Path(memory_dir).resolve()
        self.db_path = self.memory_dir / ".bauer_memory_index.sqlite3"

    def rebuild(self) -> int:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        blocks = list(self._blocks())
        with self._connection() as conn:
            conn.execute("DELETE FROM memory_blocks")
            try:
                conn.execute("DELETE FROM memory_blocks_fts")
            except sqlite3.DatabaseError:
                pass
            for idx, block in enumerate(blocks, start=1):
                conn.execute(
                    """
                    INSERT INTO memory_blocks (id, file, title, body)
                    VALUES (?, ?, ?, ?)
                    """,
                    (idx, block["file"], block["title"], block["body"]),
                )
                try:
                    conn.execute(
                        """
                        INSERT INTO memory_blocks_fts (rowid, file, title, body)
                        VALUES (?, ?, ?, ?)
                        """,
                        (idx, block["file"], block["title"], block["body"]),
                    )
                except sqlite3.DatabaseError:
                    pass
        return len(blocks)

    def search(self, query: str, *, limit: int = 10) -> list[MemoryHit]:
        clean_query = query.strip()
        if not clean_query:
            return []
        with self._connection() as conn:
            if self._fts_available(conn):
                rows = conn.execute(
                    """
                    SELECT file, title, snippet(memory_blocks_fts, 2, '[', ']', '...', 18) AS snippet,
                           bm25(memory_blocks_fts) AS score
                    FROM memory_blocks_fts
                    WHERE memory_blocks_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (_fts_query(clean_query), max(1, int(limit))),
                ).fetchall()
                return [
                    MemoryHit(row["file"], row["title"], row["snippet"], float(row["score"]))
                    for row in rows
                ]
            like = f"%{clean_query}%"
            rows = conn.execute(
                """
                SELECT file, title, body
                FROM memory_blocks
                WHERE title LIKE ? OR body LIKE ?
                LIMIT ?
                """,
                (like, like, max(1, int(limit))),
            ).fetchall()
        return [
            MemoryHit(row["file"], row["title"], _snippet(row["body"], clean_query), 0.0)
            for row in rows
        ]

    def _blocks(self):
        for path in sorted(self.memory_dir.glob("*.md")):
            content = path.read_text(encoding="utf-8", errors="replace")
            parts = re.split(r"(?=^## )", content, flags=re.MULTILINE)
            for part in parts:
                body = part.strip()
                if not body or not body.startswith("## "):
                    continue
                lines = body.splitlines()
                title = lines[0].lstrip("#").strip() if lines else path.name
                yield {"file": path.name, "title": title, "body": body}

    def _fts_available(self, conn: sqlite3.Connection) -> bool:
        try:
            conn.execute("SELECT 1 FROM memory_blocks_fts LIMIT 1")
            return True
        except sqlite3.DatabaseError:
            return False

    def _connect(self) -> sqlite3.Connection:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_blocks (
                id INTEGER PRIMARY KEY,
                file TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL
            );
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_blocks_fts
                USING fts5(file, title, body)
                """
            )
        except sqlite3.DatabaseError:
            pass


def _fts_query(query: str) -> str:
    terms = re.findall(r"[\w\-]+", query, flags=re.UNICODE)
    return " OR ".join(terms) if terms else query


def _snippet(text: str, query: str) -> str:
    idx = text.lower().find(query.lower())
    if idx < 0:
        return text[:240]
    start = max(0, idx - 80)
    end = min(len(text), idx + len(query) + 160)
    return text[start:end]
