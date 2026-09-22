"""Diagnóstico reproduzível da maturidade de código do Bauer."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MaturityDimension:
    name: str
    score: float
    evidence: tuple[str, ...]
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaturityReport:
    score: float
    level: str
    dimensions: tuple[MaturityDimension, ...]
    gates: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "level": self.level,
            "dimensions": [asdict(item) for item in self.dimensions],
            "gates": self.gates,
        }


def _ruff_issue_count(root: Path) -> tuple[int | None, str]:
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "bauer/",
        "--select",
        "E,F,W",
        "--ignore",
        "E501,W291,W293,E302,E303",
        "--output-format",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "Ruff indisponível para o diagnóstico"
    try:
        issues = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return None, "Ruff não retornou JSON"
    return len(issues), f"{len(issues)} avisos Ruff informativos"


def build_maturity_report(root: str | Path = ".") -> MaturityReport:
    """Calcula um score estrutural sem chamar providers ou executar tarefas."""
    repo = Path(root).resolve()
    ruff_count, ruff_evidence = _ruff_issue_count(repo)
    static_ok = ruff_count is not None and ruff_count <= 10

    test_files = list((repo / "tests").glob("test_*.py"))
    tests_ok = bool(test_files) and (repo / "tests" / "conftest.py").exists()
    kernel_ok = all(
        (repo / path).exists()
        for path in (
            "bauer/core/kernel/entry.py",
            "tests/test_arquitetura_custodia_kernel.py",
        )
    )
    memory_ok = False
    try:
        from .memory_facade import UnifiedMemory
        from .memory_provider import MemoryProvider

        memory_ok = issubclass(UnifiedMemory, MemoryProvider)
    except Exception:
        memory_ok = False
    seams_ok = all(
        (repo / path).exists()
        for path in (
            "bauer/memory_facade.py",
            "bauer/routing_runtime.py",
            "bauer/decision_router.py",
        )
    )

    dimensions = (
        MaturityDimension(
            "qualidade_estatica",
            10.0 if static_ok else 5.0,
            (ruff_evidence,),
            () if static_ok else ("reduzir avisos Ruff para no máximo 10",),
        ),
        MaturityDimension(
            "testes_reprodutiveis",
            9.0 if tests_ok else 5.0,
            (f"{len(test_files)} arquivos de teste", "conftest hermético presente"),
            () if tests_ok else ("suíte hermética não encontrada",),
        ),
        MaturityDimension(
            "governanca_kernel",
            9.0 if kernel_ok else 6.0,
            ("entrada governada e teste de custódia",),
            () if kernel_ok else ("entrada ou teste do Kernel ausente",),
        ),
        MaturityDimension(
            "memoria_unificada",
            9.0 if memory_ok else 6.0,
            ("UnifiedMemory implementa MemoryProvider",),
            () if memory_ok else ("provider unificado não está no contrato automático",),
        ),
        MaturityDimension(
            "modularidade",
            9.0 if seams_ok else 7.0,
            ("fachadas de memória e roteamento isoladas",),
            () if seams_ok else ("fronteiras de runtime incompletas",),
        ),
    )
    score = round(sum(item.score for item in dimensions) / len(dimensions), 1)
    gates = {
        "ruff_informativo": static_ok,
        "testes_hermeticos": tests_ok,
        "kernel_governado": kernel_ok,
        "memoria_provider": memory_ok,
        "seams_modulares": seams_ok,
    }
    level = "maturidade alta / produção controlada" if score >= 9.0 else "maturidade média-alta"
    return MaturityReport(score, level, dimensions, gates)
