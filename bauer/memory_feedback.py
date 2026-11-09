"""Feedback estruturado por turno, com escopo de workspace (plano 065, sprint 65).

Antes, o ``/thumbsup``/``/thumbsdown`` só anexava a pergunta/resposta
truncadas (e cruas) ao ``FEEDBACK.md`` — sem sessão, turno, digest, escopo
ou decisão de redação, insuficiente para minerar "padrão que desagrada".

Cada sinal agora vira um registro em ``<workspace>/memory/consolidation/
feedback.sqlite3`` com:

* ``workspace_id`` do escopo (decisão ``workspace_only``) — o store só lê e
  grava registros do próprio workspace, mesmo se o arquivo for copiado;
* ``session_id`` validado + ``turn_index`` + nomes das tools do turno;
* trechos **redigidos** (``privacy.redact_text``) e digests SHA-256 do texto
  já redigido (nunca do texto cru);
* ``redaction`` = ``clean`` | ``redacted`` | ``dropped`` (trecho descartado
  porque ainda parecia sensível depois da redação);
* ``machine_id`` só como metadado (não particiona).

O fingerprint de padrão é calculado na sprint 68 a partir destes campos.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .memory_scope import (
    SCOPE_WORKSPACE,
    ScopeViolationError,
    WorkspaceScope,
    resolve_workspace_scope,
    sanitize_text,
    text_digest,
)
from .session_ids import is_valid_session_id

_log = logging.getLogger("bauer.memory_feedback")

SCHEMA_VERSION = 1
RATINGS = frozenset({"positive", "negative"})
_LEGACY_RATINGS = {"positivo": "positive", "negativo": "negative"}

USER_EXCERPT_LIMIT = 300
ASSISTANT_EXCERPT_LIMIT = 500
REASON_LIMIT = 200
_MAX_TOOL_NAMES = 12

_DDL = """
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id      TEXT PRIMARY KEY,
    schema_version   INTEGER NOT NULL,
    workspace_id     TEXT NOT NULL,
    scope            TEXT NOT NULL,
    rating           TEXT NOT NULL,
    session_id       TEXT NOT NULL DEFAULT '',
    turn_index       INTEGER NOT NULL DEFAULT 0,
    user_digest      TEXT NOT NULL DEFAULT '',
    assistant_digest TEXT NOT NULL DEFAULT '',
    user_excerpt     TEXT NOT NULL DEFAULT '',
    assistant_excerpt TEXT NOT NULL DEFAULT '',
    reason           TEXT NOT NULL DEFAULT '',
    tool_names       TEXT NOT NULL DEFAULT '[]',
    model            TEXT NOT NULL DEFAULT '',
    machine_id       TEXT NOT NULL DEFAULT '',
    redaction        TEXT NOT NULL DEFAULT 'clean',
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_ws_created ON feedback(workspace_id, created_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class FeedbackRecord:
    feedback_id: str
    workspace_id: str
    rating: str
    session_id: str
    turn_index: int
    user_digest: str
    assistant_digest: str
    user_excerpt: str
    assistant_excerpt: str
    reason: str
    tool_names: list[str] = field(default_factory=list)
    model: str = ""
    machine_id: str = ""
    redaction: str = "clean"
    created_at: str = ""
    scope: str = SCOPE_WORKSPACE
    schema_version: int = SCHEMA_VERSION


def normalize_rating(rating: str) -> str:
    value = _LEGACY_RATINGS.get((rating or "").strip().lower(), (rating or "").strip().lower())
    if value not in RATINGS:
        raise ValueError(f"rating inválido: {rating!r}")
    return value


class FeedbackStore:
    """SQLite de feedback de UM workspace."""

    FILENAME = "feedback.sqlite3"

    def __init__(self, scope: WorkspaceScope) -> None:
        if not isinstance(scope, WorkspaceScope):
            raise TypeError("FeedbackStore exige WorkspaceScope")
        self.scope = scope
        self.db_path = scope.state_dir / self.FILENAME

    @classmethod
    def for_workspace(cls, workspace: object) -> "FeedbackStore | None":
        scope = resolve_workspace_scope(workspace)
        return cls(scope) if scope is not None else None

    def _connect(self) -> sqlite3.Connection:
        self.scope.ensure_state_dir()
        if self.db_path.is_symlink():
            raise ScopeViolationError(f"store de feedback é symlink: {self.db_path}")
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(_DDL)
        return conn

    def record(
        self,
        *,
        rating: str,
        user_text: str = "",
        assistant_text: str = "",
        session_id: str | None = None,
        turn_index: int = 0,
        tool_names: Iterable[str] = (),
        model: str = "",
        machine_id: str = "",
        reason: str = "",
        created_at: str | None = None,
    ) -> FeedbackRecord:
        normalized = normalize_rating(rating)
        user = sanitize_text(user_text, limit=USER_EXCERPT_LIMIT)
        assistant = sanitize_text(assistant_text, limit=ASSISTANT_EXCERPT_LIMIT)
        why = sanitize_text(reason, limit=REASON_LIMIT)
        parts = (user, assistant, why)
        if any(p.sensitive for p in parts):
            redaction = "dropped"
        elif any(p.redacted for p in parts):
            redaction = "redacted"
        else:
            redaction = "clean"
        tools: list[str] = []
        for name in tool_names:
            clean = sanitize_text(str(name), limit=64)
            if clean.text and not clean.sensitive and clean.text not in tools:
                tools.append(clean.text)
            if len(tools) >= _MAX_TOOL_NAMES:
                break
        sid = session_id if is_valid_session_id(session_id) else ""
        rec = FeedbackRecord(
            feedback_id=f"fb-{uuid.uuid4().hex[:12]}",
            workspace_id=self.scope.workspace_id,
            rating=normalized,
            session_id=str(sid or ""),
            turn_index=max(0, int(turn_index or 0)),
            user_digest=text_digest(user.text) if user.text else "",
            assistant_digest=text_digest(assistant.text) if assistant.text else "",
            user_excerpt=user.text,
            assistant_excerpt=assistant.text,
            reason=why.text,
            tool_names=tools,
            model=sanitize_text(model, limit=120).text,
            machine_id=sanitize_text(machine_id, limit=120).text,
            redaction=redaction,
            created_at=created_at or _now_iso(),
        )
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO feedback (feedback_id, schema_version, workspace_id, scope, rating,"
                    " session_id, turn_index, user_digest, assistant_digest, user_excerpt,"
                    " assistant_excerpt, reason, tool_names, model, machine_id, redaction, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rec.feedback_id, rec.schema_version, rec.workspace_id, rec.scope,
                        rec.rating, rec.session_id, rec.turn_index, rec.user_digest,
                        rec.assistant_digest, rec.user_excerpt, rec.assistant_excerpt,
                        rec.reason, json.dumps(rec.tool_names), rec.model, rec.machine_id,
                        rec.redaction, rec.created_at,
                    ),
                )
        finally:
            conn.close()
        return rec

    def list(
        self,
        *,
        rating: str | None = None,
        since: str | None = None,
        limit: int = 1000,
    ) -> list[FeedbackRecord]:
        """Registros DESTE workspace (linhas de outro ``workspace_id`` são ignoradas)."""
        if not self.db_path.exists():
            return []
        sql = "SELECT * FROM feedback WHERE workspace_id=? AND scope=?"
        params: list[Any] = [self.scope.workspace_id, SCOPE_WORKSPACE]
        if rating:
            sql += " AND rating=?"
            params.append(normalize_rating(rating))
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY created_at ASC, feedback_id ASC LIMIT ?"
        params.append(max(1, int(limit)))
        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return [_from_row(row) for row in rows]

    def get(self, feedback_id: str) -> FeedbackRecord | None:
        if not self.db_path.exists():
            return None
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM feedback WHERE feedback_id=? AND workspace_id=?",
                (feedback_id, self.scope.workspace_id),
            ).fetchone()
        finally:
            conn.close()
        return _from_row(row) if row else None


def _from_row(row: sqlite3.Row) -> FeedbackRecord:
    try:
        tools = [str(t) for t in json.loads(row["tool_names"] or "[]")]
    except (TypeError, ValueError):
        tools = []
    return FeedbackRecord(
        feedback_id=row["feedback_id"],
        workspace_id=row["workspace_id"],
        rating=row["rating"],
        session_id=row["session_id"],
        turn_index=int(row["turn_index"] or 0),
        user_digest=row["user_digest"],
        assistant_digest=row["assistant_digest"],
        user_excerpt=row["user_excerpt"],
        assistant_excerpt=row["assistant_excerpt"],
        reason=row["reason"],
        tool_names=tools,
        model=row["model"],
        machine_id=row["machine_id"],
        redaction=row["redaction"],
        created_at=row["created_at"],
        scope=row["scope"],
        schema_version=int(row["schema_version"] or SCHEMA_VERSION),
    )


# ---------------------------------------------------------------------------
# Extração do turno a partir das mensagens do agente
# ---------------------------------------------------------------------------

def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return "" if content is None else str(content)


@dataclass(frozen=True)
class TurnSnapshot:
    user_text: str
    assistant_text: str
    turn_index: int
    tool_names: list[str]


def snapshot_last_turn(messages: list[dict]) -> TurnSnapshot:
    """Último turno: pergunta, resposta final, índice do turno e tools usadas."""
    last_user_idx = -1
    turn_index = 0
    for idx, message in enumerate(messages or []):
        if isinstance(message, dict) and message.get("role") == "user":
            last_user_idx = idx
            turn_index += 1
    if last_user_idx < 0:
        return TurnSnapshot("", "", 0, [])
    user_text = _content_text(messages[last_user_idx].get("content"))
    assistant_text = ""
    tools: list[str] = []
    for message in messages[last_user_idx + 1:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "assistant":
            text = _content_text(message.get("content"))
            if text.strip():
                assistant_text = text
            for call in message.get("tool_calls") or []:
                fn = call.get("function") if isinstance(call, dict) else None
                name = (fn or {}).get("name") if isinstance(fn, dict) else None
                if isinstance(name, str) and name and name not in tools:
                    tools.append(name)
        elif role == "tool":
            name = message.get("name")
            if isinstance(name, str) and name and name not in tools:
                tools.append(name)
    return TurnSnapshot(user_text, assistant_text, turn_index, tools)


def record_turn_feedback(
    workspace: object,
    *,
    rating: str,
    messages: list[dict],
    session_id: str | None,
    model: str = "",
    machine_id: str = "",
    reason: str = "",
) -> FeedbackRecord | None:
    """Ponto único usado pelo ``/thumbsup``/``/thumbsdown``. Best-effort."""
    try:
        store = FeedbackStore.for_workspace(workspace)
        if store is None:
            return None
        turn = snapshot_last_turn(messages)
        return store.record(
            rating=rating,
            user_text=turn.user_text,
            assistant_text=turn.assistant_text,
            session_id=session_id,
            turn_index=turn.turn_index,
            tool_names=turn.tool_names,
            model=model,
            machine_id=machine_id,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 — feedback é acessório
        _log.debug("record_turn_feedback failed: %s", exc)
        return None

