"""Append-only research trajectory store."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .secret_policy import sanitize_mapping, sanitize_text


@dataclass(frozen=True)
class TrajectoryRecord:
    trajectory_id: str
    kind: str
    objective: str
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


class TrajectoryStore:
    """JSONL store under workspace/.bauer_research."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.store_dir = self.workspace / ".bauer_research"
        self.path = self.store_dir / "trajectories.jsonl"

    def append(
        self,
        *,
        kind: str,
        objective: str,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        trajectory_id: str = "",
    ) -> TrajectoryRecord:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        record = TrajectoryRecord(
            trajectory_id=trajectory_id or f"traj-{uuid.uuid4().hex[:12]}",
            kind=kind.strip() or "generic",
            objective=sanitize_text(objective.strip()),
            input=sanitize_mapping(input or {}),
            output=sanitize_mapping(output or {}),
            metadata=sanitize_mapping(metadata or {}),
            created_at=_now_iso(),
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.__dict__, ensure_ascii=False, sort_keys=True) + "\n")
        return record

    def list(self, *, limit: int = 50, kind: str = "") -> list[TrajectoryRecord]:
        if not self.path.exists():
            return []
        rows: list[TrajectoryRecord] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kind and data.get("kind") != kind:
                continue
            rows.append(
                TrajectoryRecord(
                    trajectory_id=str(data.get("trajectory_id", "")),
                    kind=str(data.get("kind", "")),
                    objective=str(data.get("objective", "")),
                    input=data.get("input") if isinstance(data.get("input"), dict) else {},
                    output=data.get("output") if isinstance(data.get("output"), dict) else {},
                    metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
                    created_at=str(data.get("created_at", "")),
                )
            )
        return list(reversed(rows[-max(1, int(limit)):]))


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
