"""Camada de performance do Bauer (Fase 12 — Sprint 33, baseline).

Read-only sobre dados já persistidos. Mede antes de otimizar.

  build_perf_report(runtime_root)  → PerfReport (visão geral)
  run_perf(runtime_root, run_id)   → RunPerf (breakdown de uma run)
"""

from __future__ import annotations

from .profiler import build_perf_report, run_perf
from .schemas import PerfReport, RunPerf, ToolTiming

__all__ = [
    "PerfReport",
    "RunPerf",
    "ToolTiming",
    "build_perf_report",
    "run_perf",
]
