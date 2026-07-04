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


# ── Allowlist aprendida (persistida) ────────────────────────────────────────
# Comandos que o usuário liberou com "sempre" no prompt de confirmação. Ficam
# em $BAUER_HOME/allowed_commands.yaml e são carregados junto da allowlist fixa.

def _learned_commands_path() -> "Path":
    from .paths import get_bauer_home
    return get_bauer_home() / "allowed_commands.yaml"


def load_learned_commands() -> set[str]:
    """Comandos aprendidos (liberados com 'sempre'). Vazio se não há/ilegível."""
    try:
        import yaml
        data = yaml.safe_load(_learned_commands_path().read_text(encoding="utf-8"))
        return {str(c).strip().lower() for c in (data or []) if str(c).strip()}
    except Exception:
        return set()


def add_learned_command(base: str) -> None:
    """Persiste um comando na allowlist aprendida. Best-effort."""
    base = (base or "").strip().lower()
    if not base:
        return
    cur = load_learned_commands()
    if base in cur:
        return
    cur.add(base)
    try:
        import yaml
        p = _learned_commands_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(sorted(cur), allow_unicode=True), encoding="utf-8")
    except Exception:
        pass


class ShellRunner:
    """Executa comandos shell de forma controlada.

    Args:
        workspace: Diretório de execução (cwd de todos os processos).
        safe_mode: Se True, risco médio exige confirm=True.
        timeout: Tempo máximo em segundos.
        max_output_bytes: Limite combinado de stdout+stderr.
        extra_allowed_commands: Comandos extras liberados além da allowlist
            fixa embutida (ex.: docker, kubectl) — vem de
            config.tools.extra_allowed_commands. Ainda passam pela denylist
            (sempre bloqueada) e pelo safe_mode (risco médio exige confirm).
    """

    def __init__(
        self,
        workspace: str | Path = "workspace",
        safe_mode: bool = True,
        timeout: int = 30,
        max_output_bytes: int = _MAX_OUTPUT_BYTES,
        extra_allowed_commands: "list[str] | frozenset[str] | None" = None,
        allowlist_callback=None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.safe_mode = safe_mode
        self.timeout = timeout
        self.max_output_bytes = max_output_bytes
        self.extra_allowed_commands: frozenset[str] = frozenset(
            c.strip().lower() for c in (extra_allowed_commands or []) if c.strip()
        )
        # Confirmação interativa da allowlist (o gate que engessa: docker/pip/…).
        # callback(base) -> "once" | "session" | "always" | "deny". "always"
        # grava no allowed_commands.yaml (aprende). None = comportamento antigo
        # (bloqueia comando fora da allowlist). Só instalado em chat TTY.
        self.allowlist_callback = allowlist_callback
        # Comandos aprendidos (persistidos) + os liberados nesta sessão via
        # "session"/"once". Mesclam com a allowlist fixa e extra_allowed_commands.
        self._runtime_allowed: set[str] = set(load_learned_commands())

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
        if (base in _ALLOWLIST or base in self.extra_allowed_commands
                or base in self._runtime_allowed):
            return
        # Fora da allowlist: se houver callback interativo, pergunta e (talvez)
        # aprende — em vez de bloquear em silêncio. "always" persiste.
        if self.allowlist_callback is not None:
            try:
                decision = str(self.allowlist_callback(base) or "deny").lower()
            except Exception:
                decision = "deny"
            if decision == "always":
                add_learned_command(base)
                self._runtime_allowed.add(base)
                return
            if decision in ("once", "session"):
                # "session" e "once" liberam sem persistir em disco; a diferença
                # prática (once re-pergunta no próximo turno) não compensa a
                # fricção — ambos liberam pelo runtime desta sessão.
                self._runtime_allowed.add(base)
                return
            # "deny" → cai no raise abaixo
        available = ", ".join(sorted(_ALLOWLIST | self.extra_allowed_commands))
        raise BlockedCommandError(
            f"Comando '{base}' nao esta na allowlist.\n"
            f"Permitidos: {available}\n"
            "Para liberar mais comandos (ex.: docker, kubectl), adicione em "
            "config.yaml: tools.extra_allowed_commands: [docker, ...]"
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
