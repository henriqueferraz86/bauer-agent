"""Dataclasses da camada de performance (Fase 12 — Sprint 33, baseline).

Estruturas puras. `profiler.py` as preenche lendo dados JÁ persistidos (runs +
eventos de tool com duration_ms). Sem instrumentação nova, sem LLM.

Nota honesta: model latency / TTFT / formatting NÃO são medidos por fase ainda
(precisariam de instrumentação no loop do turno). O que dá para medir hoje é o
wall-clock da run e a duração POR TOOL (que já vem nos eventos). A parcela fora
das tools (`non_tool_ms`) é um proxy de "modelo + overhead"."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolTiming:
    tool: str
    calls: int = 0
    total_ms: float = 0.0


@dataclass
class RunPerf:
    run_id: str
    status: str = ""
    wall_ms: float | None = None          # started_at → finished_at
    tool_ms: float = 0.0                   # soma das durações de tool
    non_tool_ms: float | None = None       # wall - tool (proxy: modelo + overhead)
    tool_calls: int = 0
    tools: list[ToolTiming] = field(default_factory=list)  # por tool, desc por total_ms


@dataclass
class PerfReport:
    window: str = "all"
    runs_total: int = 0
    avg_wall_ms: float | None = None
    p50_wall_ms: float | None = None
    p95_wall_ms: float | None = None
    total_wall_ms: float = 0.0
    total_tool_ms: float = 0.0
    tool_time_share: float | None = None   # total_tool_ms / total_wall_ms (0..1)
    slowest: list[tuple[str, float]] = field(default_factory=list)     # (run_id, wall_ms)
    top_tools: list[ToolTiming] = field(default_factory=list)          # agregado, desc
    # Fases ainda NÃO instrumentadas — explicitadas para o relatório ser honesto.
    unmeasured_phases: list[str] = field(
        default_factory=lambda: ["model_latency", "time_to_first_token", "formatting"]
    )
