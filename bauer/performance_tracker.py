"""Rastreia tempo de resposta e tokens por sessão.

Registra automaticamente em MODEL_EXPERIENCE.md ao fim de cada sessão de agent/serve.
Resultado inferido: ok | slow | oom baseado em tempo e erros capturados.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path


_SLOW_THRESHOLD_SECONDS = 60  # sessão > 60s sem resposta = slow


@dataclass
class SessionStats:
    model: str
    context_tokens: int
    machine_id: str = ""
    start_time: float = field(default_factory=time.monotonic)
    total_turns: int = 0
    total_chars: int = 0
    errors: list[str] = field(default_factory=list)
    _turn_start: float = field(default=0.0, repr=False)

    def start_turn(self) -> None:
        self._turn_start = time.monotonic()
        self.total_turns += 1

    def end_turn(self, response_chars: int) -> float:
        elapsed = time.monotonic() - self._turn_start
        self.total_chars += response_chars
        return elapsed

    def record_error(self, error: str) -> None:
        self.errors.append(error)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def result(self) -> str:
        if any("oom" in e.lower() or "out of memory" in e.lower() for e in self.errors):
            return "oom"
        if self.errors:
            return "error"
        if self.total_turns > 0:
            avg_per_turn = self.elapsed_seconds / self.total_turns
            if avg_per_turn > _SLOW_THRESHOLD_SECONDS:
                return "slow"
        return "ok"

    def save(self, memory_dir: str | Path = "memory", lesson: str = "") -> None:
        """Registra sessão em MODEL_EXPERIENCE.md."""
        try:
            from .memory_manager import MemoryManager
            mm = MemoryManager(memory_dir)
            mm.add_model_experience(
                model=self.model,
                context_tokens=self.context_tokens,
                result=self.result,
                ram_used_mb=_ram_used_mb(),
                machine_id=self.machine_id,
                lesson=lesson or _auto_lesson(self),
            )
        except Exception:
            pass  # nunca travar a sessão por falha de tracking


def _ram_used_mb() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().used / 1024 / 1024)
    except Exception:
        return 0


def _auto_lesson(stats: SessionStats) -> str:
    if stats.result == "oom":
        return f"OOM com {stats.context_tokens} tokens — reduzir contexto"
    if stats.result == "slow":
        return f"Lento com {stats.total_turns} turnos — considerar modelo menor"
    if stats.result == "ok" and stats.total_turns > 0:
        return f"{stats.total_turns} turnos concluidos com sucesso"
    return ""
