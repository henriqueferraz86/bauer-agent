"""Dataclasses da camada de auditoria (Fase 11 — MVP).

Estruturas puras, sem I/O. `report.py`/`run_auditor.py`/`score.py` preenchem-nas
lendo dados JÁ persistidos pelo runtime (runs, eventos, approvals, custo). Nada
aqui muta estado nem chama LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AuditReport:
    """Resumo agregado de um conjunto de runs (janela de tempo opcional)."""

    window: str = "all"
    runs_total: int = 0
    runs_completed: int = 0
    runs_failed: int = 0
    runs_cancelled: int = 0
    runs_waiting_approval: int = 0
    success_rate: float = 0.0            # completed / (terminais)
    average_duration_ms: float | None = None
    approvals_pending: int = 0
    policy_allow: int = 0
    policy_ask: int = 0
    policy_deny: int = 0
    estimated_cost_usd: float = 0.0
    most_used_skills: list[tuple[str, int]] = field(default_factory=list)
    most_failed_skills: list[tuple[str, int]] = field(default_factory=list)
    most_used_agents: list[tuple[str, int]] = field(default_factory=list)
    most_used_tools: list[tuple[str, int]] = field(default_factory=list)
    runtime_adapters: list[tuple[str, int]] = field(default_factory=list)
    top_errors: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class PolicyDecision:
    operation: str = ""
    action: str = ""       # allow | ask | deny
    risk_level: str = ""
    tool_name: str = ""


@dataclass
class RunAudit:
    """Auditoria estruturada de UMA run: o que foi pedido, o que rodou, o que mudou."""

    run_id: str
    status: str = ""
    agent_id: str = ""
    runtime_adapter: str = ""
    started_at: str = ""
    finished_at: str | None = None
    duration_ms: float | None = None
    prompt: str = ""
    final_answer: str = ""
    error: str | None = None
    cost_estimate: float | None = None
    events_total: int = 0
    skills_used: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    commands_executed: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    policy_decisions: list[PolicyDecision] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    event_details: list[dict[str, Any]] = field(default_factory=list)
    tool_call_details: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RunScore:
    """Nota heurística 0–5 de uma run (sem LLM)."""

    run_id: str
    score: int = 0
    max_score: int = 5
    reasons: list[str] = field(default_factory=list)   # pontos conquistados
    warnings: list[str] = field(default_factory=list)  # pontos perdidos / ressalvas


@dataclass
class ArchitectureFinding:
    """Achado estatico da auditoria arquitetural."""

    rule: str
    severity: str
    message: str
    file: str = ""
    line: int | None = None
    recommendation: str = ""


@dataclass
class ArchitectureAudit:
    """Relatorio da auditoria arquitetural estatica."""

    status: str = "approved"
    scanned_files: list[str] = field(default_factory=list)
    warnings: list[ArchitectureFinding] = field(default_factory=list)
    critical: list[ArchitectureFinding] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class BenchmarkScenario:
    """Declarative benchmark loaded from YAML."""

    id: str
    name: str
    prompt: str
    expected_files: list[str] = field(default_factory=list)
    expected_commands: list[str] = field(default_factory=list)
    expected_events: list[str] = field(default_factory=list)
    min_score: int = 4
    platforms: list[str] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    scenario_id: str
    scenario_name: str
    run_id: str = ""
    score: int = 0
    min_score: int = 4
    passed: bool = False
    duration_ms: float | None = None
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    error: str = ""


@dataclass
class BenchmarkSuiteReport:
    id: str
    started_at: str
    finished_at: str = ""
    passed: int = 0
    failed: int = 0
    total: int = 0
    results: list[BenchmarkResult] = field(default_factory=list)


@dataclass
class SkillMetric:
    skill_id: str
    uses: int = 0
    failures: int = 0
    failure_rate: float = 0.0
    average_duration_ms: float | None = None


@dataclass
class RepeatedToolSequence:
    tools: list[str]
    occurrences: int
    run_ids: list[str] = field(default_factory=list)


@dataclass
class SkillSuggestion:
    suggested_id: str
    reason: str
    tools: list[str] = field(default_factory=list)
    occurrences: int = 0
    requires_human_approval: bool = True


@dataclass
class SkillInsights:
    window: str = "all"
    generated_at: str = ""
    most_used: list[SkillMetric] = field(default_factory=list)
    highest_failure_rate: list[SkillMetric] = field(default_factory=list)
    slowest: list[SkillMetric] = field(default_factory=list)
    never_used: list[str] = field(default_factory=list)
    repeated_sequences: list[RepeatedToolSequence] = field(default_factory=list)
    suggestions: list[SkillSuggestion] = field(default_factory=list)
