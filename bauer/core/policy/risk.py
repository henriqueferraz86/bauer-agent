"""Risk classification for Bauer policy decisions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RiskClassifier:
    workspace: str | Path = "workspace"

    def classify(self, operation: str, payload: dict[str, Any] | None = None) -> str:
        payload = payload or {}
        if operation in {"shell.execute", "filesystem.delete", "social.publish", "os.ui_control"}:
            return "high"
        if operation == "agent.delegate":
            return "medium"
        if operation == "filesystem.write" and self._outside_workspace(payload.get("path")):
            return "high"
        if operation in {"filesystem.write", "network.request", "network.http"}:
            return "medium"
        if operation == "os.open_app":
            return "low"
        return "low"

    def _outside_workspace(self, raw_path: Any) -> bool:
        if not raw_path:
            return False
        workspace = Path(self.workspace).resolve()
        path = Path(str(raw_path))
        target = path.resolve() if path.is_absolute() else (workspace / path).resolve()
        try:
            target.relative_to(workspace)
            return False
        except ValueError:
            return True
