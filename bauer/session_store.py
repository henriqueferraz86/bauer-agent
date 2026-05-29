"""Persistência de sessão para o bauer serve (Fase A5).

Cada sessão é um arquivo JSONL em memory/sessions/<session_id>.jsonl.
Cada linha é uma mensagem do histórico de conversa.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path


class SessionStore:
    def __init__(self, sessions_dir: str | Path = "memory/sessions"):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return str(uuid.uuid4())[:8]

    def save(self, session_id: str, messages: list[dict]) -> None:
        p = self.dir / f"{session_id}.jsonl"
        with p.open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

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
