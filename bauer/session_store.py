"""Persistência de sessão para o bauer serve (Fase A5).

Cada sessão é um arquivo JSONL em memory/sessions/<session_id>.jsonl.
Cada linha é uma mensagem do histórico de conversa.
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from weakref import WeakValueDictionary

from .unicode_utils import safe_json_dumps as _safe_json_dumps
from .privacy import public_messages, redact_data


_TURN_LOCKS: WeakValueDictionary = WeakValueDictionary()
_TURN_LOCKS_GUARD = threading.Lock()


class SessionStore:
    def __init__(self, sessions_dir: str | Path = "memory/sessions"):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return str(uuid.uuid4())[:8]

    @contextmanager
    def turn(self, session_id: str):
        """Serialize a complete load/execute/save cycle for one session.

        POSIX servers also coordinate separate processes using flock. On
        Windows this coordinates threads/instances in the current process;
        sharing a sessions directory between Windows processes is unsupported.
        """
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
        p = self.dir / f"{session_id}.jsonl"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=p.parent, suffix=".tmp", delete=False,
            ) as f:
                temporary = Path(f.name)
                for msg in messages:
                    # Persisted history remains the sanitized continuity copy.
                    f.write(_safe_json_dumps(redact_data(msg), ensure_ascii=False) + "\n")
            os.replace(temporary, p)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self, session_id: str) -> list[dict]:
        p = self.dir / f"{session_id}.jsonl"
        if not p.exists():
            return []
        messages = []
        for line in p.read_text(encoding="utf-8").splitlines():
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
        return sorted(p.stem for p in self.dir.glob("*.jsonl"))

    def delete(self, session_id: str) -> bool:
        p = self.dir / f"{session_id}.jsonl"
        if p.exists():
            p.unlink()
            return True
        return False

    def exists(self, session_id: str) -> bool:
        return (self.dir / f"{session_id}.jsonl").exists()
