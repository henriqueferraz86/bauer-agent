"""verify_app: builda/roda/testa o app gerado e reporta pass/fail.

Fecha o maior gargalo de autonomia (auto-verificação): em vez de considerar
"pronto" só pela presença de arquivos, esta camada DETECTA a stack do projeto,
roda um plano de verificação (install → build/test/smoke → serve) e devolve
um resultado estruturado que o agente (e o Delivery Score) podem usar para
confiar — ou para disparar correção.

Design:
- Detecção por markers de arquivo (package.json, pyproject.toml, go.mod, ...).
- Runner e `which` INJETÁVEIS → testável de forma determinística e CI-safe,
  sem depender de npm/pip/go realmente instalados.
- Para na primeira falha (build quebrado não adianta testar) e devolve a cauda
  do output para o agente diagnosticar.
- P1.2: passo "serve" inicia o app e sonda uma porta para confirmar que sobe.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

#: Limite de chars do output capturado por passo (cauda — onde o erro costuma estar).
MAX_OUTPUT_CHARS = 2000

#: Assinatura do runner: (cmd, cwd, timeout) -> (returncode, output_combinado).
Runner = Callable[[List[str], Path, int], Tuple[int, str]]

#: Assinatura do smoke checker: (cmd, cwd, ports, timeout) -> (ok, mensagem).
SmokeCheck = Callable[[List[str], Path, List[int], int], Tuple[bool, str]]

#: Portas padrão que apps web costumam usar.
DEFAULT_PROBE_PORTS: List[int] = [3000, 8000, 5000, 8080, 4000]

#: Tempo máximo (s) aguardando o app subir para receber conexão.
DEFAULT_SERVE_TIMEOUT: int = 10


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


def _probe_port(port: int, timeout: float = 1.0) -> bool:
    """Tenta conexão TCP a 127.0.0.1:port. True se algo responder."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _default_smoke_check(
    cmd: List[str],
    cwd: Path,
    ports: List[int],
    timeout: int,
) -> Tuple[bool, str]:
    """Inicia o processo, sonda as portas até timeout, mata o processo."""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        return False, f"Erro ao iniciar processo: {exc}"

    port_found: Optional[int] = None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            for port in ports:
                if _probe_port(port, timeout=0.5):
                    port_found = port
                    break
            if port_found is not None:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.5)

        if port_found is not None:
            return True, f"app respondeu na porta {port_found}"

        # captura saída parcial para diagnóstico
        out = ""
        if proc.stdout:
            try:
                proc.stdout.flush()
            except Exception:
                pass
        if proc.poll() is not None:
            out = (proc.stdout.read(1000) if proc.stdout else "")
            return False, f"processo terminou antes de responder (rc={proc.returncode})\n{out}"
        return False, f"app não respondeu em nenhuma porta ({ports}) em {timeout}s"
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _detect_serve_cmd(root: Path, stack: str, scripts: dict) -> Optional[List[str]]:
    """Detecta o comando de start para smoke de runtime. None = sem servidor detectado."""
    if stack == "node":
        start = str(scripts.get("start", ""))
        if start and "test" not in start and "jest" not in start and "vitest" not in start:
            return ["npm", "start"]
        dev = str(scripts.get("dev", ""))
        if dev:
            return ["npm", "run", "dev"]
        return None

    if stack == "python":
        # Detecta por arquivo de entrada comum
        for entry in ("app.py", "main.py", "server.py", "run.py", "api.py"):
            ep = root / entry
            if not ep.is_file():
                continue
            content = ep.read_text(encoding="utf-8", errors="replace").lower()
            # Só sugere serve se o arquivo parece iniciar um servidor
            if any(kw in content for kw in ("flask", "fastapi", "uvicorn", "aiohttp", "tornado", "starlette", "app.run")):
                return ["python", entry]
        # Detecta uvicorn/gunicorn nas dependências
        for dep_file in ("requirements.txt", "pyproject.toml"):
            dep_path = root / dep_file
            if dep_path.is_file():
                content = dep_path.read_text(encoding="utf-8", errors="replace").lower()
                if "uvicorn" in content or "gunicorn" in content:
                    # entry point genérico — tenta app:app
                    for ep in ("app.py", "main.py"):
                        if (root / ep).is_file():
                            return ["uvicorn", f"{ep[:-3]}:app", "--port", "8000", "--timeout-keep-alive", "1"]
        return None

    return None


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def plan_verification(project_dir: Path | str) -> Tuple[str, List[Tuple[str, List[str]]]]:
    """Detecta a stack e devolve ``(stack, [(passo, comando), ...])``.

    Stack 'unknown' com plano vazio = nada verificável automaticamente.
    O passo especial "serve" usa SmokeCheck em vez do Runner normal.
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
        # P1.2: smoke de runtime se houver comando de start
        serve_cmd = _detect_serve_cmd(root, "node", scripts)
        if serve_cmd:
            steps.append(("serve", serve_cmd))
        return "node", steps

    # ── Python ────────────────────────────────────────────────────────────
    pyproject = root / "pyproject.toml"
    reqs = root / "requirements.txt"
    if pyproject.is_file() or reqs.is_file():
        scripts: dict = {}
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
        # P1.2: smoke de runtime se detectar servidor
        serve_cmd = _detect_serve_cmd(root, "python", scripts)
        if serve_cmd:
            steps.append(("serve", serve_cmd))
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
    smoke_check: Optional[SmokeCheck] = None,
    probe_ports: Optional[List[int]] = None,
    serve_timeout: int = DEFAULT_SERVE_TIMEOUT,
) -> VerifyResult:
    """Verifica o projeto: detecta stack, roda build/test/smoke/serve, reporta.

    Para na PRIMEIRA falha (build quebrado → não testa). Tools ausentes no PATH
    viram passo 'pulado' com ok=False (não dá pra confirmar que roda).

    P1.2: passo "serve" inicia o app e sonda uma porta para confirmar startup.
    `smoke_check` é injetável para testes; `probe_ports` customiza as portas sondadas.
    """
    runner = runner or _default_runner
    _smoke = smoke_check or _default_smoke_check
    _ports = probe_ports or DEFAULT_PROBE_PORTS
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

        # ── passo "serve": inicia o app e sonda porta (P1.2) ──────────────
        if name == "serve":
            try:
                srv_ok, srv_out = _smoke(cmd, root, _ports, serve_timeout)
            except Exception as exc:  # noqa: BLE001
                srv_ok, srv_out = False, f"Erro no smoke check: {exc}"
            st = Step(name, cmd, rc=0 if srv_ok else 1, ok=srv_ok,
                      output=srv_out[-MAX_OUTPUT_CHARS:])
            steps.append(st)
            if not srv_ok:
                ok = False
            break  # serve é sempre o último passo

        # ── passos normais (runner bloqueante) ─────────────────────────────
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
