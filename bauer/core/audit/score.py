"""Score heurístico 0–5 de uma run — determinístico, sem LLM (Fase 11 / Sprint 27).

Adapta os 5 critérios do doc de governança aos SINAIS que o runtime realmente
persiste hoje. Onde um critério não é avaliável com os dados atuais (ex.: "plano
registrado" — runs de chat não gravam plano), usamos um proxy explícito e
documentamos no motivo, em vez de fingir que temos o dado."""

from __future__ import annotations

from pathlib import Path

from .schemas import RunAudit, RunScore

# Extensões que indicam código → validação (teste/build) passa a ser "aplicável".
_CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".php", ".c", ".cpp", ".cs")
_TEST_MARKERS = ("pytest", "test", "npm test", "npm run test", "go test", "cargo test", "unittest", "vitest", "jest")
_MIN_SUMMARY_CHARS = 40


def _touches_code(files: list[str]) -> bool:
    return any(str(f).lower().endswith(_CODE_EXTS) for f in files)


def _ran_validation(commands: list[str]) -> bool:
    joined = " \n ".join(commands).lower()
    return any(marker in joined for marker in _TEST_MARKERS)


def score_run(audit: RunAudit) -> RunScore:
    """Calcula RunScore a partir de um RunAudit já montado."""
    result = RunScore(run_id=audit.run_id)

    # 1) Objetivo concluído — status terminal de sucesso.
    if audit.status == "completed":
        result.score += 1
        result.reasons.append("Objetivo concluído (run completed).")
    else:
        result.warnings.append(f"Run não concluída (status: {audit.status or '?'}).")

    # 2) "Plano/execução estruturada" — proxy: a run de fato AGIU (>=1 tool) ou
    #    produziu resposta substantiva. Runs de chat não gravam plano explícito.
    if audit.tools_used or len(audit.final_answer) >= _MIN_SUMMARY_CHARS:
        result.score += 1
        result.reasons.append("Execução com ação real (tools) ou resposta substantiva.")
    else:
        result.warnings.append("Sem tool calls nem resposta substantiva (proxy de 'plano').")

    # 3) Sem erro crítico.
    if audit.status != "failed" and not audit.error:
        result.score += 1
        result.reasons.append("Execução sem erro crítico.")
    else:
        result.warnings.append(f"Erro na run: {audit.error or 'status failed'}.")

    # 4) Validação/testes QUANDO APLICÁVEL. Aplicável = mexeu em código.
    if _touches_code(audit.files_changed):
        if _ran_validation(audit.commands_executed):
            result.score += 1
            result.reasons.append("Código alterado e validação (teste/build) executada.")
        else:
            result.warnings.append("Código alterado mas sem evidência de teste/build.")
    else:
        # Nada de código para validar → critério não penaliza.
        result.score += 1
        result.reasons.append("Sem alteração de código — validação não aplicável.")

    # 5) Resumo final claro.
    if len(audit.final_answer.strip()) >= _MIN_SUMMARY_CHARS:
        result.score += 1
        result.reasons.append("Resumo final presente e substantivo.")
    else:
        result.warnings.append("Resumo final ausente ou muito curto.")

    return result


def score_run_by_id(runtime_root: str | Path, run_id: str) -> RunScore | None:
    """Conveniência: audita a run e pontua. None se a run não existe."""
    from .run_auditor import audit_run

    audit = audit_run(runtime_root, run_id)
    if audit is None:
        return None
    return score_run(audit)
