"""Rastreia tempo de resposta e tokens por sessão.

Registra automaticamente em MODEL_EXPERIENCE.md ao fim de cada sessão de agent/serve.
Resultado inferido: ok | slow | oom baseado em tempo e erros capturados.

Wave 1 (Hermes parity): integrates `bauer.account_usage` to track REAL token
counts (parsed from `response.usage`) instead of char-based estimates, plus
USD cost via `bauer.usage_pricing`. Char counting is kept for back-compat —
callers that don't surface usage still get char-based metrics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


_SLOW_THRESHOLD_SECONDS = 60  # sessão > 60s sem resposta = slow


@dataclass
class SessionStats:
    model: str
    context_tokens: int
    machine_id: str = ""
    provider: str = ""              # used for cost lookup; empty = no costing
    start_time: float = field(default_factory=time.monotonic)
    total_turns: int = 0
    total_chars: int = 0
    # Wave 1 — real token counts (canonical 5-key dict from account_usage).
    usage_total: dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    })
    cost_usd_total: float = 0.0      # accumulated cost across all turns
    errors: list[str] = field(default_factory=list)
    _turn_start: float = field(default=0.0, repr=False)

    def start_turn(self) -> None:
        self._turn_start = time.monotonic()
        self.total_turns += 1

    def end_turn(self, response_chars: int) -> float:
        elapsed = time.monotonic() - self._turn_start
        self.total_chars += response_chars
        return elapsed

    def record_turn_usage(self, raw_usage: Mapping) -> dict[str, int]:
        """Accumulate one turn's `response.usage` dict + add USD cost.

        Accepts any provider's raw usage shape — normalises via
        `bauer.account_usage.normalize_usage()` first. Updates both
        `usage_total` (per-key sums) and `cost_usd_total`.

        Returns the *normalised* per-turn usage dict so callers can display it
        immediately (e.g. CLI prints "1.5k in / 300 out (cache 80%) = $0.0023").
        Silent no-op if the provider didn't surface usage.
        """
        try:
            from .account_usage import merge_usage, normalize_usage
            from .usage_pricing import estimate_cost_usd
        except Exception:
            # Hard fail of the cost stack is fine — never break the session.
            return {}

        normalised = normalize_usage(raw_usage)
        # Accumulate per-key (merge_usage handles missing keys defensively).
        self.usage_total = merge_usage(self.usage_total, normalised)
        # Cost: provider-aware; falls back to the conservative pricing table if
        # the (provider, model) pair is unknown.
        if self.provider:
            self.cost_usd_total += estimate_cost_usd(
                self.provider, self.model, normalised
            )
        return normalised

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
