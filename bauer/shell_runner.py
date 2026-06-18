"""Shell Runner do Bauer Agent (Fase 5).

Execução controlada de comandos:
- Allowlist de comandos seguros (base command name)
- Denylist de padrões perigosos (sempre bloqueados, independente de flags)
- safe_mode: risco médio requer confirm=True explícito
- Timeout e limite de output
- Nunca usa shell=True
- Sempre executa dentro do workspace (cwd=workspace)

Premortem item 4: segurança antes de funcionalidade.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


class ShellError(Exception):
    """Erro de execução de shell."""


class BlockedCommandError(ShellError):
    """Comando bloqueado pela denylist ou pela allowlist — nunca executa."""


class SafeModeError(ShellError):
    """Comando de risco médio bloqueado pelo safe_mode."""


class CommandTimeoutError(ShellError):
    """Comando excedeu o tempo limite."""


_MAX_OUTPUT_BYTES = 50_000

# Sempre bloqueados, independente de safe_mode ou confirm.
_DENYLIST: list[re.Pattern] = [
    # rm com flags rf/fr apontando para / ou ~ (limpa root ou home)
    re.compile(r"\brm\b.*-[a-z]*[rf][a-z]*[rf].*\s+[/~]", re.IGNORECASE),
    # mkfs — formatar disco
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    # dd if= — gravar diretamente em dispositivo
    re.compile(r"\bdd\b.*\bif=", re.IGNORECASE),
    # desligar/reiniciar o sistema
    re.compile(r"\b(shutdown|reboot|halt|poweroff)\b", re.IGNORECASE),
    # chmod recursivo 777 — permissões amplas
    re.compile(r"\bchmod\b.*-[Rr].*777", re.IGNORECASE),
    # chown recursivo — transferência em lote
    re.compile(r"\bchown\b.*-[Rr]\b", re.IGNORECASE),
    # pipe para shell — injeção de código
    re.compile(r"\|\s*(ba)?sh\b", re.IGNORECASE),
    # curl/wget piped — download + execução
    re.compile(r"\b(curl|wget)\b.*\|", re.IGNORECASE),
]

# Comandos permitidos (stem do executável, sem path e sem extensão .exe).
_ALLOWLIST: frozenset[str] = frozenset({
    # Leitura e navegação
    "ls", "dir", "cat", "head", "tail", "wc", "grep", "find",
    "pwd", "where", "which", "file", "stat", "du",
    # Criação e manipulação básica
    "mkdir", "touch", "echo",
    "rm",    # risco médio em safe_mode — denylist cobre rm -rf /
    "cp", "mv", "copy", "move",
    # Python
    "python", "python3", "py", "pip", "pip3",
    # Git
    "git",
    # Ferramentas de dev
    "pytest", "ruff", "black", "mypy", "isort", "uv", "uvx",
    # Node/JS
    "npm", "node", "npx", "yarn",
    # Bauer Agent CLI (permite agents chamarem subcomandos do bauer)
    "bauer",
    # Outros
    "type",
})

# Risco médio — requerem confirm=True quando safe_mode=True.
_MEDIUM_RISK: list[re.Pattern] = [
    re.compile(r"\brm\b", re.IGNORECASE),
    re.compile(r"\bpip\b.*\binstall\b", re.IGNORECASE),
    re.compile(r"\bnpm\b.*\binstall\b", re.IGNORECASE),
    re.compile(r"\bgit\b.*\b(push|reset|clean)\b", re.IGNORECASE),
    re.compile(r"\buv\b.*\badd\b", re.IGNORECASE),
]


@dataclass
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_ms: int
    truncated: bool = False


class ShellRunner:
    """Executa comandos shell de forma controlada.

    Args:
        workspace: Diretório de execução (cwd de todos os processos).
        safe_mode: Se True, risco médio exige confirm=True.
        timeout: Tempo máximo em segundos.
        max_output_bytes: Limite combinado de stdout+stderr.
    """

    def __init__(
        self,
        workspace: str | Path = "workspace",
        safe_mode: bool = True,
        timeout: int = 30,
        max_output_bytes: int = _MAX_OUTPUT_BYTES,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.safe_mode = safe_mode
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes

    def run(self, command: str, confirm: bool = False) -> CommandResult:
        """Executa um comando controlado.

        Args:
            command: Linha de comando (parseada com shlex, sem shell=True).
            confirm: Se True, bypass do safe_mode para risco médio.

        Raises:
            BlockedCommandError: Denylist ou fora da allowlist — nunca executa.
            SafeModeError: Risco médio em safe_mode sem confirm=True.
            CommandTimeoutError: Excedeu timeout.
            ShellError: Outros erros de execução.
        """
        args = self.validate(command, confirm=confirm)
        return self._execute(args)

    def validate(self, command: str, confirm: bool = False) -> list[str]:
        """Roda as verificações de segurança e devolve os args parseados,
        SEM executar. Usado pelo modo background do run_command (G17.3), que
        lança o processo via Popen mas precisa do mesmo gate de segurança.

        Raises:
            BlockedCommandError, SafeModeError, ShellError — iguais ao run().
        """
        self._check_denylist(command)
        args = self._parse_command(command)
        self._check_allowlist(args)
        if self.safe_mode and not confirm:
            self._check_medium_risk(command)
        return args

    # --- verificações de segurança -------------------------------------------

    def _check_denylist(self, command: str) -> None:
        for pattern in _DENYLIST:
            if pattern.search(command):
                raise BlockedCommandError(
                    f"Comando perigoso detectado: '{command[:80]}'.\n"
                    "Execucao permanentemente bloqueada (denylist).\n"
                    f"Padrao: {pattern.pattern}"
                )

    def _check_allowlist(self, args: list[str]) -> None:
        base = Path(args[0]).stem.lower()
        if base not in _ALLOWLIST:
            available = ", ".join(sorted(_ALLOWLIST))
            raise BlockedCommandError(
                f"Comando '{base}' nao esta na allowlist.\n"
                f"Permitidos: {available}"
            )

    def _check_medium_risk(self, command: str) -> None:
        for pattern in _MEDIUM_RISK:
            if pattern.search(command):
                raise SafeModeError(
                    f"Comando de risco medio: '{command[:80]}'.\n"
                    "Bloqueado em safe_mode=true.\n"
                    'Para executar: adicione "confirm": true nos args, '
                    "ou defina tools.safe_mode: false no config.yaml."
                )

    # --- parsing e execução --------------------------------------------------

    def _parse_command(self, command: str) -> list[str]:
        try:
            # posix=True sempre: strip correto de aspas em caminhos Windows como
            # "C:/path/python.exe" — com posix=False o shlex mantém as aspas no
            # token e subprocess.run falha com FileNotFoundError.
            args = shlex.split(command, posix=True)
        except ValueError as exc:
            raise ShellError(f"Comando invalido (parsing falhou): {exc}") from exc
        if not args:
            raise ShellError("Comando vazio.")
        return args

    def _execute(self, args: list[str]) -> CommandResult:
        if not self.workspace.is_dir():
            raise ShellError(f"Workspace nao existe: {self.workspace}")

        start = time.monotonic()
        try:
            proc = subprocess.run(
                args,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - start) * 1000)
            raise CommandTimeoutError(
                f"Comando excedeu {self.timeout}s de timeout. "
                f"Decorrido: {elapsed}ms"
            )
        except FileNotFoundError:
            raise ShellError(
                f"Comando nao encontrado: '{args[0]}'. "
                "Verifique se esta instalado e no PATH."
            )
        except OSError as exc:
            raise ShellError(f"Erro ao executar '{args[0]}': {exc}") from exc

        elapsed = int((time.monotonic() - start) * 1000)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        combined_size = len(stdout.encode()) + len(stderr.encode())
        truncated = combined_size > self.max_output_bytes
        if truncated:
            half = self.max_output_bytes // 2
            stdout = stdout.encode()[:half].decode(errors="replace")
            stderr = stderr.encode()[:half].decode(errors="replace")

        return CommandResult(
            command=args,
            returncode=proc.returncode,
            stdout=stdout,
            stderr=stderr,
            elapsed_ms=elapsed,
            truncated=truncated,
        )
