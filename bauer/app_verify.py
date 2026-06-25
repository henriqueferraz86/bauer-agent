"""P1.1 — verify_app: builda/roda/testa o app gerado e reporta pass/fail.

Fecha o maior gargalo de autonomia (auto-verificação): em vez de considerar
"pronto" só pela presença de arquivos, esta camada DETECTA a stack do projeto,
roda um plano de verificação (install → build/test/smoke) e devolve um resultado
estruturado que o agente (e o Delivery Score) podem usar para confiar — ou para
disparar correção.

Design:
- Detecção por markers de arquivo (package.json, pyproject.toml, go.mod, ...).
- Runner e `which` INJETÁVEIS → testável de forma determinística e CI-safe,
  sem depender de npm/pip/go realmente instalados.
- Para na primeira falha (build quebrado não adianta testar) e devolve a cauda
  do output para o agente diagnosticar.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

#: Limite de chars do output capturado por passo (cauda — onde o erro costuma estar).
MAX_OUTPUT_CHARS = 2000

#: Assinatura do runner: (cmd, cwd, timeout) -> (returncode, output_combinado).
Runner = Callable[[List[str], Path, int], Tuple[int, str]]


@dataclass
class Step:
    name: str                 # install | build | test | smoke
    cmd: List[str]
    rc: int = 0
    ok: bool = False
    output: str = ""
    skipped: bool = False
    reason: str = ""


@dataclass
class VerifyResult:
    project: str
    stack: str
    ok: bool
    steps: List[Step] = field(default_factory=list)
    summary: str = ""


def _default_runner(cmd: List[str], cwd: Path, timeout: int) -> Tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    out = ((proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode, out


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def plan_verification(project_dir: Path | str) -> Tuple[str, List[Tuple[str, List[str]]]]:
    """Detecta a stack e devolve ``(stack, [(passo, comando), ...])``.

    Stack 'unknown' com plano vazio = nada verificável automaticamente.
    """
    root = Path(project_dir)

    # ── Node / npm ────────────────────────────────────────────────────────
    pkg = root / "package.json"
    if pkg.is_file():
        scripts = (_read_json(pkg).get("scripts") or {})
        steps: List[Tuple[str, List[str]]] = []
        if (root / "package-lock.json").is_file():
            steps.append(("install", ["npm", "ci", "--no-audit", "--no-fund"]))
        else:
            steps.append(("install", ["npm", "install", "--no-audit", "--no-fund"]))
        if "build" in scripts:
            steps.append(("build", ["npm", "run", "build"]))
        _test = str(scripts.get("test", ""))
        if _test and "no test specified" not in _test:
            steps.append(("test", ["npm", "test"]))
        return "node", steps

    # ── Python ────────────────────────────────────────────────────────────
    pyproject = root / "pyproject.toml"
    reqs = root / "requirements.txt"
    if pyproject.is_file() or reqs.is_file():
        steps = []
        if pyproject.is_file():
            steps.append(("install", ["pip", "install", "-e", "."]))
        else:
            steps.append(("install", ["pip", "install", "-r", "requirements.txt"]))
        has_tests = (
            (root / "tests").is_dir()
            or any(root.glob("test_*.py"))
            or any(root.glob("*/test_*.py"))
        )
        if has_tests:
            steps.append(("test", ["pytest", "-q"]))
        else:
            # smoke mínimo: compila tudo (pega SyntaxError sem rodar nada).
            steps.append(("smoke", ["python", "-m", "compileall", "-q", "."]))
        return "python", steps

    # ── Go ──────────────────────────────────────────────────────────────────
    if (root / "go.mod").is_file():
        return "go", [("build", ["go", "build", "./..."])]

    # ── Rust ──────────────────────────────────────────────────────────────────
    if (root / "Cargo.toml").is_file():
        return "rust", [("build", ["cargo", "build"])]

    return "unknown", []


# ---------------------------------------------------------------------------
# Diagnóstico de falha acionável (P1.3)
# ---------------------------------------------------------------------------

_DIAGNOSE_RULES: List[Tuple[str, str]] = [
    # Python
    ("ModuleNotFoundError: No module named", "Dependência ausente — rode: pip install <modulo>"),
    ("ImportError: cannot import name",      "Import inexistente — verifique o nome/versão do pacote"),
    ("SyntaxError",                          "Erro de sintaxe Python — verifique o código"),
    ("IndentationError",                     "Erro de indentação Python — verifique espaçamentos"),
    # Node / npm
    ("Cannot find module",                   "Dependência npm ausente — rode: npm install"),
    ("Module not found: Error",              "Dependência npm ausente — rode: npm install"),
    ("ERR_MODULE_NOT_FOUND",                 "Dependência npm ausente — rode: npm install"),
    ("ENOENT",                               "Arquivo não encontrado — verifique caminhos de import"),
    ("TS",                                   "Erro TypeScript — verifique tipos e imports"),
    # Go
    ("cannot find package",                  "Pacote Go não encontrado — rode: go mod tidy"),
    ("no required module",                   "Módulo Go ausente — rode: go mod tidy"),
    # Rust
    ("error[E",                              "Erro de compilação Rust — verifique o código"),
    # Generic
    ("permission denied",                    "Permissão negada — verifique permissões do arquivo"),
    ("connection refused",                   "Conexão recusada — verifique se o serviço está rodando"),
]


def _diagnose_failure(output: str, rc: int) -> str:
    """Detecta causa provável da falha a partir da saída do passo."""
    if rc == 127:
        return "Comando não encontrado no PATH — verifique se a ferramenta está instalada"
    out_lower = output.lower()
    for pattern, hint in _DIAGNOSE_RULES:
        if pattern.lower() in out_lower:
            return hint
    return ""


def _summarize(stack: str, steps: List[Step], ok: bool) -> str:
    parts = []
    for s in steps:
        if s.skipped:
            parts.append(f"{s.name}: pulado ({s.reason})")
        else:
            part = f"{s.name}: {'ok' if s.ok else f'FALHOU (rc={s.rc})'}"
            if not s.ok and not s.skipped:
                hint = _diagnose_failure(s.output, s.rc)
                if hint:
                    part += f" [{hint}]"
            parts.append(part)
    head = "✓ app verificado — roda" if ok else "✗ verificação falhou"
    return f"{head} [{stack}] — " + ("; ".join(parts) if parts else "sem passos")


def verify_project(
    project_dir: Path | str,
    *,
    runner: Optional[Runner] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    timeout: int = 300,
    install: bool = True,
) -> VerifyResult:
    """Verifica o projeto: detecta stack, roda build/test/smoke, reporta.

    Para na PRIMEIRA falha (build quebrado → não testa). Tools ausentes no PATH
    viram passo 'pulado' com ok=False (não dá pra confirmar que roda).
    """
    runner = runner or _default_runner
    root = Path(project_dir)
    rel = root.name or str(root)

    if not root.is_dir():
        return VerifyResult(rel, "unknown", False, [], f"Projeto não encontrado: {root}")

    stack, plan = plan_verification(root)
    if stack == "unknown" or not plan:
        return VerifyResult(
            rel, stack, False, [],
            "Stack não detectada — nenhum passo de build/test verificável. "
            "Adicione package.json/pyproject.toml/go.mod, ou verifique manualmente.",
        )

    steps: List[Step] = []
    ok = True
    for name, cmd in plan:
        if name == "install" and not install:
            steps.append(Step(name, cmd, ok=True, skipped=True, reason="install desativado"))
            continue
        exe = cmd[0]
        if which(exe) is None:
            steps.append(Step(name, cmd, ok=False, skipped=True,
                              reason=f"'{exe}' não encontrado no PATH"))
            ok = False
            break
        try:
            rc, out = runner(cmd, root, timeout)
        except subprocess.TimeoutExpired:
            steps.append(Step(name, cmd, rc=-1, ok=False,
                              output=f"timeout após {timeout}s"))
            ok = False
            break
        except Exception as exc:  # noqa: BLE001 — runner nunca derruba a verificação
            steps.append(Step(name, cmd, rc=-1, ok=False, output=str(exc)[:MAX_OUTPUT_CHARS]))
            ok = False
            break
        st = Step(name, cmd, rc=rc, ok=(rc == 0), output=(out or "")[-MAX_OUTPUT_CHARS:])
        steps.append(st)
        if not st.ok:
            ok = False
            break

    return VerifyResult(rel, stack, ok, steps, _summarize(stack, steps, ok))
