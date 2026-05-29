"""Geração e leitura de .runtime_state.json.

Premortem item 2 e 11: "sem estado visível, não inicia". Este arquivo é
a fonte única da verdade sobre o que efetivamente foi aplicado em runtime.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNTIME_STATE_FILE = ".runtime_state.json"


@dataclass
class ContextState:
    requested: int
    modelfile_num_ctx: int | None
    env_OLLAMA_CONTEXT_LENGTH: int | None
    applied: int
    empirical_probe: int | None
    reason: str


@dataclass
class RuntimeState:
    configured_model: str
    configured_provider: str       # "ollama" | "openai" | "openrouter" | "opencode" | "custom"
    active_model: str | None
    model_available: bool
    ollama_alive: bool
    ollama_host: str
    context: ContextState
    tool_mode: str             # "bridge" | "native" | "none"
    profile: str               # "low" | "medium" | "high"
    ram_available_mb: int
    ram_total_mb: int
    machine_id: str
    status: str                # "ok" | "ok_with_adjustments" | "blocked"
    notes: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def write_state(state: RuntimeState, path: str | Path = RUNTIME_STATE_FILE) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    return p


def read_state(path: str | Path = RUNTIME_STATE_FILE) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
