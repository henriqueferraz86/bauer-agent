"""Persistência de sessão para o bauer serve (Fase A5).

Cada sessão é um arquivo JSONL em memory/sessions/<session_id>.jsonl.
Cada linha é uma mensagem do histórico de conversa.
"""

from __future__ import annotations

import json
import hashlib
import os
import threading
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from weakref import WeakValueDictionary

from . import confined_files
from .confined_files import ConfinementError
from .session_ids import InvalidSessionIdError, is_valid_session_id, validate_session_id
from .unicode_utils import safe_json_dumps as _safe_json_dumps
from .privacy import public_messages, redact_data

__all__ = ["SessionStore", "InvalidSessionIdError", "validate_session_id"]


_TURN_LOCKS: WeakValueDictionary = WeakValueDictionary()
_TURN_LOCKS_GUARD = threading.Lock()


class SessionStore:
    def __init__(self, sessions_dir: str | Path = "memory/sessions"):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return str(uuid.uuid4())[:8]

    def _path(self, session_id: str) -> Path:
        """Único mapeamento ID → arquivo; valida o ID antes de tocar no disco.

        No Windows ``:`` (``tg:123``) viraria drive relativo/stream NTFS, por
        isso é codificado como ``%3A`` — ``%`` nunca aparece num ID válido.
        """
        validate_session_id(session_id)
        name = session_id.replace(":", "%3A") if os.name == "nt" else session_id
        return self.dir / f"{name}.jsonl"

    @contextmanager
    def turn(self, session_id: str):
        """Serialize a complete load/execute/save cycle for one session.

        POSIX servers also coordinate separate processes using flock. On
        Windows this coordinates threads/instances in the current process;
        sharing a sessions directory between Windows processes is unsupported.
        """
        validate_session_id(session_id)
        key = (str(self.dir.resolve()), session_id)
        with _TURN_LOCKS_GUARD:
            lock = _TURN_LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _TURN_LOCKS[key] = lock
        with lock:
            if os.name != "posix":
                yield
                return
            import fcntl

            lock_dir = self.dir / ".turn-locks"
            lock_dir.mkdir(exist_ok=True)
            filename = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
            with (lock_dir / filename).open("a") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def serialized(self, session_id: str):
        """Wrap a background worker so it owns the session until persistence."""
        def decorate(worker):
            @wraps(worker)
            def wrapped(*args, **kwargs):
                with self.turn(session_id):
                    return worker(*args, **kwargs)
            return wrapped
        return decorate

    def save(self, session_id: str, messages: list[dict]) -> None:
        # Persisted history remains the sanitized continuity copy.
        content = "".join(
            _safe_json_dumps(redact_data(msg), ensure_ascii=False) + "\n" for msg in messages
        )
        try:
            confined_files.write_text_atomic(self._path(session_id), content, self.dir)
        except ConfinementError as exc:
            raise InvalidSessionIdError(str(exc)) from exc

    def load(self, session_id: str) -> list[dict]:
        try:
            raw = confined_files.read_text(self._path(session_id), self.dir)
        except ConfinementError as exc:
            raise InvalidSessionIdError(str(exc)) from exc
        if raw is None:
            return []
        messages = []
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return messages

    def load_public(self, session_id: str) -> list[dict]:
        """Return only conversational messages suitable for a chat client."""
        return public_messages(self.load(session_id))

    def list_sessions(self) -> list[str]:
        ids = []
        for p in self.dir.glob("*.jsonl"):
            sid = p.stem.replace("%3A", ":") if os.name == "nt" else p.stem
            if is_valid_session_id(sid) and not p.is_symlink():
                ids.append(sid)
        return sorted(ids)

    def delete(self, session_id: str) -> bool:
        return confined_files.unlink(self._path(session_id), self.dir)

    def exists(self, session_id: str) -> bool:
        return confined_files.exists(self._path(session_id), self.dir)
