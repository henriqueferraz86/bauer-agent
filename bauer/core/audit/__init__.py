"""Camada de auditoria do Bauer (Fase 11 — MVP).

Read-only sobre dados já persistidos pelo runtime (runs, eventos, approvals,
custo). Sem LLM, sem mutação de estado.

  build_report(runtime_root)      → AuditReport (visão geral)
  audit_run(runtime_root, run_id) → RunAudit (detalhe de uma run)
  score_run(audit) / score_run_by_id(...) → RunScore (nota 0–5)
"""

from __future__ import annotations

from .architecture_auditor import audit_architecture
from .benchmark import list_benchmark_reports, load_benchmark_scenarios, run_benchmark_suite
from .report import build_report
from .run_auditor import audit_run
from .schemas import (
    ArchitectureAudit,
    ArchitectureFinding,
    AuditReport,
    BenchmarkResult,
    BenchmarkScenario,
    BenchmarkSuiteReport,
    PolicyDecision,
    RepeatedToolSequence,
    RunAudit,
    RunScore,
    SkillInsights,
    SkillMetric,
    SkillSuggestion,
)
from .score import score_run, score_run_by_id
from .skill_insights import build_skill_insights
from .weekly import build_weekly_report

__all__ = [
    "ArchitectureAudit",
    "ArchitectureFinding",
    "AuditReport",
    "BenchmarkResult",
    "BenchmarkScenario",
    "BenchmarkSuiteReport",
    "PolicyDecision",
    "RunAudit",
    "RunScore",
    "RepeatedToolSequence",
    "SkillInsights",
    "SkillMetric",
    "SkillSuggestion",
    "audit_architecture",
    "build_report",
    "build_skill_insights",
    "build_weekly_report",
    "audit_run",
    "score_run",
    "score_run_by_id",
    "load_benchmark_scenarios",
    "run_benchmark_suite",
    "list_benchmark_reports",
]
