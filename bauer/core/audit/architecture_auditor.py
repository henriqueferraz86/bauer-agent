"""Auditoria arquitetural estatica do Bauer (Fase 11 / Sprint 29).

Primeira versao: regras simples por padroes. Ela alerta riscos arquiteturais,
sem bloquear release nem executar codigo do runtime.
"""

from __future__ import annotations

import subprocess
import re
from pathlib import Path
from typing import Iterable

import yaml

from .schemas import ArchitectureAudit, ArchitectureFinding

_SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".yaml", ".yml"}
_SKIP_PARTS = {".git", ".venv", "__pycache__", "node_modules", "dist", "build", ".pytest_cache"}
_AGNO_ALLOWED_PARTS = {"docs", "tests", "experiments"}
_SENSITIVE_PATTERNS = ("subprocess.", "os.system(", "shutil.rmtree(", "Path.unlink(", ".unlink(")


def audit_architecture(
    project_root: str | Path = ".",
    *,
    since: str = "",
    changed_files_only: bool = False,
) -> ArchitectureAudit:
    """Executa checagens estaticas simples sobre o projeto."""
    root = Path(project_root).resolve()
    files = _changed_files(root, since) if (since or changed_files_only) else _all_source_files(root)
    report = ArchitectureAudit(scanned_files=[_rel(root, path) for path in files])

    for path in files:
        if not path.exists() or path.is_dir() or path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        rel = _rel(root, path)
        text = _read_text(path)
        if text is None:
            continue
        _check_agno_boundary(report, rel, text)
        _check_frontend_runtime_import(report, rel, text)
        _check_sensitive_policy_bypass(report, rel, text)
        _check_execution_emits_events(report, rel, text)
        _check_core_skill_dependency(report, rel, text)
        _check_skill_policy_mutation(report, rel, text)
        if since or changed_files_only:
            _check_new_module_has_test(report, root, path, rel)

    _check_skill_manifests(report, root)
    _finish(report)
    return report


def _all_source_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in _SOURCE_SUFFIXES
        and not any(part in _SKIP_PARTS for part in path.parts)
    ]


def _changed_files(root: Path, since: str) -> list[Path]:
    base = ["git", "-C", str(root), "diff", "--name-only"]
    if since:
        base.append(since)
    try:
        result = subprocess.run(base, capture_output=True, text=True, check=False, timeout=10)
    except Exception:  # noqa: BLE001
        return []
    if result.returncode != 0:
        return []
    return [root / line.strip() for line in result.stdout.splitlines() if line.strip()]


def _check_agno_boundary(report: ArchitectureAudit, rel: str, text: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    if not lowered.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
        return
    if "agno" not in text.lower():
        return
    if "bauer/core/runtime/adapters/agno_adapter.py" in lowered:
        return
    if any(f"/{part}/" in f"/{lowered}/" or lowered.startswith(f"{part}/") for part in _AGNO_ALLOWED_PARTS):
        return
    _add(
        report,
        "agno-runtime-boundary",
        "warning",
        "Referencia a Agno fora do RuntimeAdapter dedicado.",
        rel,
        _line_of(text, "agno"),
        "Mover chamadas diretas ao Agno para bauer/core/runtime/adapters/agno_adapter.py.",
    )


def _check_frontend_runtime_import(report: ArchitectureAudit, rel: str, text: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    if not lowered.startswith("desktop/src/"):
        return
    needles = ("bauer.core.runtime", "bauer/core/runtime", "../bauer/core/runtime", "runtime/run_manager")
    if not any(needle in text for needle in needles):
        return
    _add(
        report,
        "frontend-runtime-boundary",
        "warning",
        "Frontend parece importar logica de runtime diretamente.",
        rel,
        _first_line_of_any(text, needles),
        "Consumir dados por endpoints/API do serve em vez de importar core runtime no frontend.",
    )


def _check_sensitive_policy_bypass(report: ArchitectureAudit, rel: str, text: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    if lowered.startswith(("tests/", "docs/")):
        return
    if not (
        lowered.startswith("bauer/tools/")
        or lowered.startswith("bauer/core/skills/")
        or lowered in {"bauer/tool_router.py", "bauer/shell_runner.py"}
    ):
        return
    if not any(pattern in text for pattern in _SENSITIVE_PATTERNS):
        return
    if "PolicyEngine" in text or "policy_engine" in text or "ApprovalManager" in text:
        return
    _add(
        report,
        "sensitive-tools-policy",
        "critical",
        "Possivel acao sensivel sem evidencia local de PolicyEngine/ApprovalManager.",
        rel,
        _first_line_of_any(text, _SENSITIVE_PATTERNS),
        "Encaminhar a acao sensivel pelo PolicyEngine antes de executar.",
    )


def _check_execution_emits_events(report: ArchitectureAudit, rel: str, text: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    if not lowered.endswith(".py") or lowered.startswith(("tests/", "bauer/core/runtime/adapters/")):
        return
    if not any(marker in text for marker in (".run_agent(", "run_one_turn(")):
        return
    if any(marker in text for marker in ("RunManager", "EventBus", "set_runtime_ids", "run_manager.")):
        return
    _add(
        report,
        "execution-events",
        "warning",
        "Runtime execution has no local evidence of run/event correlation.",
        rel,
        _first_line_of_any(text, (".run_agent(", "run_one_turn(")),
        "Create a RunManager record and correlate tool events with the run_id.",
    )


def _check_core_skill_dependency(report: ArchitectureAudit, rel: str, text: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    if not lowered.startswith("bauer/core/") or lowered.startswith(("bauer/core/skills/", "bauer/core/audit/")):
        return
    match = re.search(r"\b(?:skill_id|skill)\s*=\s*['\"]([a-z0-9_.-]+)['\"]", text, re.IGNORECASE)
    if not match:
        return
    _add(
        report,
        "core-skill-coupling",
        "warning",
        f"Core module appears coupled to the specific skill '{match.group(1)}'.",
        rel,
        _line_of(text, match.group(0)),
        "Resolve skills through capabilities or the SkillRegistry at the boundary.",
    )


def _check_skill_policy_mutation(report: ArchitectureAudit, rel: str, text: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    if not lowered.startswith("bauer/core/skills/") or not lowered.endswith(".py"):
        return
    markers = ("PolicyEngine", "ApprovalManager", "policy_engine.", "approval_manager.")
    if not any(marker in text for marker in markers):
        return
    _add(
        report,
        "skill-policy-boundary",
        "critical",
        "Skill implementation appears to evaluate or mutate policy directly.",
        rel,
        _first_line_of_any(text, markers),
        "Keep policy ownership in the ToolRouter/runtime boundary and pass only decisions to skills.",
    )


def _check_new_module_has_test(report: ArchitectureAudit, root: Path, path: Path, rel: str) -> None:
    lowered = rel.replace("\\", "/").lower()
    if not lowered.startswith("bauer/") or path.suffix != ".py" or path.name == "__init__.py":
        return
    test_name = f"test_{path.stem}.py"
    tests_root = root / "tests"
    if not tests_root.exists():
        return
    if any(candidate.name == test_name for candidate in tests_root.rglob("test_*.py")):
        return
    _add(
        report,
        "new-module-tests",
        "warning",
        "Modulo alterado sem teste dedicado com nome correspondente.",
        rel,
        None,
        f"Adicionar ou atualizar cobertura em tests/{test_name} ou teste equivalente.",
    )


def _check_skill_manifests(report: ArchitectureAudit, root: Path) -> None:
    manifests_root = root / "bauer" / "data" / "skill_manifests"
    if not manifests_root.exists():
        return
    for skill_dir in manifests_root.iterdir():
        if not skill_dir.is_dir():
            continue
        manifest = skill_dir / "skill.yaml"
        if not manifest.exists():
            _add(
                report,
                "skill-manifest",
                "warning",
                "Skill manifest directory sem skill.yaml.",
                _rel(root, skill_dir),
                None,
                "Criar skill.yaml com id, name, permissions e risk.",
            )
            continue
        rel = _rel(root, manifest)
        try:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            _add(report, "skill-manifest", "warning", f"Manifesto invalido: {exc}", rel)
            continue
        missing = [field for field in ("id", "name", "permissions", "risk") if not data.get(field)]
        if missing:
            _add(
                report,
                "skill-manifest",
                "warning",
                f"Manifesto incompleto; campos ausentes: {', '.join(missing)}.",
                rel,
                None,
                "Completar o manifesto antes de publicar/usar a skill.",
            )


def _add(
    report: ArchitectureAudit,
    rule: str,
    severity: str,
    message: str,
    file: str = "",
    line: int | None = None,
    recommendation: str = "",
) -> None:
    finding = ArchitectureFinding(
        rule=rule,
        severity=severity,
        message=message,
        file=file,
        line=line,
        recommendation=recommendation,
    )
    if severity == "critical":
        report.critical.append(finding)
    else:
        report.warnings.append(finding)
    if recommendation and recommendation not in report.recommendations:
        report.recommendations.append(recommendation)


def _finish(report: ArchitectureAudit) -> None:
    report.status = "approved_with_warnings" if report.warnings or report.critical else "approved"


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None


def _line_of(text: str, needle: str) -> int | None:
    lowered = needle.lower()
    for idx, line in enumerate(text.splitlines(), 1):
        if lowered in line.lower():
            return idx
    return None


def _first_line_of_any(text: str, needles: Iterable[str]) -> int | None:
    for needle in needles:
        line = _line_of(text, needle)
        if line is not None:
            return line
    return None


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except Exception:  # noqa: BLE001
        return path.as_posix()
