"""Decision memory — persistent record of agent decisions for learning.

The agent accumulates *decisions* as it operates: tool choices, plan paths,
responses to errors, approval outcomes.  This module persists those records
and provides simple similarity-based lookup so the agent can reference past
experience before making a new decision.

Similarity
----------
Uses token-overlap TF-IDF (no external dependencies) to rank decisions by
textual similarity to a query.  Exact match is scored 1.0; unrelated entries
score near 0.  Adequate for thousands of records; replace the scorer with a
vector DB for million-record scale.

Schema
------
``decisions`` table::

    id          TEXT PRIMARY KEY   (dec_<hex12>)
    context     TEXT               (query/situation that prompted the decision)
    decision    TEXT               (what was decided)
    outcome     TEXT               (good|bad|neutral)
    tags        TEXT               (JSON list of strings)
    score       REAL               (0-1 quality score; 1.0 = very good)
    created_at  REAL
    session_id  TEXT

Usage::

    from bauer.decision_memory import DecisionMemory

    mem = DecisionMemory()
    mem.record(
        context="User asked to delete all logs",
        decision="Requested confirmation before executing rm",
        outcome="good",
        tags=["safety", "approval"],
    )

    similar = mem.search("delete log files", top_k=5)
    for d in similar:
        print(d.decision, d.score)
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class DecisionRecord:
    """One persisted decision with metadata."""

    id: str
    context: str
    decision: str
    outcome: str          # "good" | "bad" | "neutral"
    tags: list[str]
    score: float          # quality score 0.0-1.0; higher = better
    created_at: float
    session_id: str | None = None

    # Set by search queries
    similarity: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "context": self.context,
            "decision": self.decision,
            "outcome": self.outcome,
            "tags": self.tags,
            "score": self.score,
            "created_at": self.created_at,
            "session_id": self.session_id,
            "similarity": self.similarity,
        }


# ---------------------------------------------------------------------------
# TF-IDF similarity scorer
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens, strip punctuation."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _tf(tokens: list[str]) -> dict[str, float]:
    counts = Counter(tokens)
    total = len(tokens) or 1
    return {t: c / total for t, c in counts.items()}


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity between two TF vectors (no IDF weighting)."""
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _similarity(query: str, text: str) -> float:
    qt = _tf(_tokenize(query))
    tt = _tf(_tokenize(text))
    return _cosine_similarity(qt, tt)


# ---------------------------------------------------------------------------
# DecisionMemory
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT PRIMARY KEY,
    context     TEXT NOT NULL DEFAULT '',
    decision    TEXT NOT NULL DEFAULT '',
    outcome     TEXT NOT NULL DEFAULT 'neutral',
    tags_json   TEXT NOT NULL DEFAULT '[]',
    score       REAL NOT NULL DEFAULT 0.5,
    created_at  REAL NOT NULL,
    session_id  TEXT
);
CREATE INDEX IF NOT EXISTS dec_outcome ON decisions(outcome);
CREATE INDEX IF NOT EXISTS dec_session ON decisions(session_id);
CREATE INDEX IF NOT EXISTS dec_score   ON decisions(score DESC);
"""


class DecisionMemory:
    """Store and retrieve agent decisions for learning.

    Parameters
    ----------
    db_path:
        SQLite database path.  Use ``:memory:`` for ephemeral storage.
    session_id:
        Optional session identifier attached to new records.
    max_records:
        Soft cap on total records.  When exceeded, the lowest-scored
        records are pruned on the next :meth:`record` call.  Default 5000.
    """

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        *,
        session_id: str | None = None,
        max_records: int = 5000,
    ) -> None:
        self._db_path = str(db_path)
        self._session_id = session_id
        self._max_records = max_records
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(
        self,
        context: str,
        decision: str,
        *,
        outcome: str = "neutral",
        tags: list[str] | None = None,
        score: float = 0.5,
        session_id: str | None = None,
    ) -> str:
        """Persist a decision and return its ID.

        Parameters
        ----------
        context:
            The situation/query that prompted the decision.
        decision:
            What was decided (action taken, response given, etc.).
        outcome:
            Quality indicator: ``"good"``, ``"bad"``, or ``"neutral"``.
        tags:
            Optional list of category tags (e.g. ``["safety", "retry"]``).
        score:
            Numeric quality [0.0, 1.0].  Used for ranking and pruning.
        session_id:
            Override the instance-level session_id.
        """
        dec_id = f"dec_{uuid.uuid4().hex[:12]}"
        now = time.time()
        outcome = outcome if outcome in ("good", "bad", "neutral") else "neutral"
        score = max(0.0, min(1.0, score))

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO decisions
                    (id, context, decision, outcome, tags_json,
                     score, created_at, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dec_id,
                    context[:2000],
                    decision[:2000],
                    outcome,
                    json.dumps(tags or []),
                    score,
                    now,
                    session_id or self._session_id,
                ),
            )

        # Prune if over cap
        if self.count() > self._max_records:
            self._prune()

        return dec_id

    def update_outcome(
        self,
        dec_id: str,
        outcome: str,
        *,
        score: float | None = None,
    ) -> bool:
        """Update the outcome (and optionally score) of a recorded decision."""
        outcome = outcome if outcome in ("good", "bad", "neutral") else "neutral"
        params: list[Any] = [outcome]
        extra = ""
        if score is not None:
            extra = ", score = ?"
            params.append(max(0.0, min(1.0, score)))
        params.append(dec_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE decisions SET outcome = ?{extra} WHERE id = ?",
                params,
            )
        return cur.rowcount > 0

    def delete(self, dec_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM decisions WHERE id = ?", (dec_id,))
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        outcome_filter: str | None = None,
        min_score: float = 0.0,
        tags_filter: list[str] | None = None,
    ) -> list[DecisionRecord]:
        """Return the top-K most similar past decisions.

        Similarity is computed by cosine similarity on TF vectors of
        ``context + decision`` text.

        Parameters
        ----------
        query:
            Free-text description of the current situation.
        top_k:
            Maximum number of results.  Default 5.
        outcome_filter:
            If set, only return decisions with this outcome.
        min_score:
            Minimum quality score filter.
        tags_filter:
            If set, only return decisions that have ALL these tags.
        """
        records = self._load_all(outcome_filter=outcome_filter, min_score=min_score)

        # Tag filter
        if tags_filter:
            required = set(tags_filter)
            records = [r for r in records if required.issubset(set(r.tags))]

        if not records:
            return []

        # Try semantic search via EmbeddingEngine first; fall back to TF-IDF
        try:
            from .embeddings import get_default_engine as _get_engine
            _engine = _get_engine()
            _q_vec = _engine.embed(query)
            for rec in records:
                combined = f"{rec.context} {rec.decision}"
                _c_vec = _engine.embed(combined)
                from .embeddings import cosine_similarity as _cos
                rec.similarity = _cos(_q_vec, _c_vec)
        except Exception:
            # Fall back to TF-IDF scoring
            for rec in records:
                combined = f"{rec.context} {rec.decision}"
                rec.similarity = _similarity(query, combined)

        # Sort by similarity desc, then by quality score desc
        records.sort(key=lambda r: (r.similarity, r.score), reverse=True)
        return records[:top_k]

    def get(self, dec_id: str) -> DecisionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decisions WHERE id = ?", (dec_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_recent(self, *, limit: int = 20) -> list[DecisionRecord]:
        """Return the most recently recorded decisions."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_by_outcome(self, outcome: str, *, limit: int = 50) -> list[DecisionRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE outcome = ? ORDER BY created_at DESC LIMIT ?",
                (outcome, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self, *, outcome: str | None = None) -> int:
        with self._connect() as conn:
            if outcome:
                return conn.execute(
                    "SELECT COUNT(*) FROM decisions WHERE outcome = ?", (outcome,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            good = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE outcome='good'"
            ).fetchone()[0]
            bad = conn.execute(
                "SELECT COUNT(*) FROM decisions WHERE outcome='bad'"
            ).fetchone()[0]
            avg_score = conn.execute(
                "SELECT AVG(score) FROM decisions"
            ).fetchone()[0] or 0.0
        return {
            "total": total,
            "good": good,
            "bad": bad,
            "neutral": total - good - bad,
            "avg_score": round(avg_score, 3),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _load_all(
        self,
        *,
        outcome_filter: str | None = None,
        min_score: float = 0.0,
    ) -> list[DecisionRecord]:
        with self._connect() as conn:
            if outcome_filter:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE outcome = ? AND score >= ?",
                    (outcome_filter, min_score),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE score >= ?",
                    (min_score,),
                ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def _prune(self) -> None:
        """Delete lowest-scored records down to max_records * 0.9."""
        target = int(self._max_records * 0.9)
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM decisions WHERE id IN (
                    SELECT id FROM decisions ORDER BY score ASC, created_at ASC
                    LIMIT (SELECT COUNT(*) - ? FROM decisions)
                )
                """,
                (target,),
            )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        if self._mem_conn is not None:
            try:
                yield self._mem_conn
                self._mem_conn.commit()
            except Exception:
                self._mem_conn.rollback()
                raise
            return

        conn = sqlite3.connect(self._db_path, timeout=10.0)
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

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DecisionRecord:
        d = dict(row)
        return DecisionRecord(
            id=d["id"],
            context=d.get("context") or "",
            decision=d.get("decision") or "",
            outcome=d.get("outcome") or "neutral",
            tags=json.loads(d.get("tags_json") or "[]"),
            score=d.get("score", 0.5),
            created_at=d.get("created_at") or 0.0,
            session_id=d.get("session_id"),
        )
