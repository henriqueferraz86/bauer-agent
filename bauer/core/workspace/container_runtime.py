"""Runtime mínimo, injetável e *fail-closed* para sandboxes de tarefa.

Este módulo não decide *quando* uma tarefa deve usar container; essa é a
responsabilidade da integração com ``TaskContract``. Ele concentra a fronteira
perigosa: descoberta do engine e todos os comandos Docker/Podman usados para
criar, executar e remover um sandbox.

Os comandos são sempre listas de argumentos com ``shell=False``. O processo da
tarefa não recebe variáveis de ambiente, socket do engine, credenciais ou mounts
do host além da entrada somente leitura e de um volume privado de trabalho.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence


_RUNTIME_NAMES = frozenset({"docker", "podman"})
_RESOURCE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)
_LABEL_VALUE_CHARS = _RESOURCE_NAME_CHARS | frozenset("/:@+=")

Runner = Callable[..., Any]


class ContainerRuntimeError(RuntimeError):
    """Erro serializável na fronteira entre Bauer e o engine de containers."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        runtime: str | None = None,
        command: Sequence[str] = (),
        returncode: int | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.runtime = runtime
        self.command = tuple(command)
        self.returncode = returncode
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        """Representação segura para evento, API ou log estruturado."""
        return {
            "code": self.code,
            "message": self.message,
            "runtime": self.runtime,
            "command": list(self.command),
            "returncode": self.returncode,
            "detail": self.detail,
        }


class ContainerRuntimeUnavailable(ContainerRuntimeError):
    """Nenhum engine compatível estava disponível no host."""


class ContainerCommandError(ContainerRuntimeError):
    """O engine executou um comando, mas retornou falha."""


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Resultado de uma descoberta sem efeitos no daemon."""

    executable: str
    available: bool
    version: str | None = None
    reason: str | None = None
    supports_network_none: bool = True
    supports_read_only: bool = True
    supports_cap_drop: bool = True
    supports_no_new_privileges: bool = True
    supports_resource_limits: bool = True


@dataclass(frozen=True)
class ContainerLimits:
    """Limites conservadores aplicados a toda tarefa isolada."""

    cpus: float = 1.0
    memory_bytes: int = 512 * 1024 * 1024
    pids_limit: int = 256

    def __post_init__(self) -> None:
        if self.cpus <= 0:
            raise ValueError("cpus deve ser positivo")
        if self.memory_bytes <= 0:
            raise ValueError("memory_bytes deve ser positivo")
        if self.pids_limit <= 0:
            raise ValueError("pids_limit deve ser positivo")


@dataclass(frozen=True)
class ContainerSpec:
    """Descrição declarativa de um sandbox antes de falar com o engine.

    ``input_path`` é a única parte do host exposta à tarefa e vai sempre para
    ``/input`` em modo somente leitura. O trabalho ocorre exclusivamente no
    volume nomeado em ``/workspace``.
    """

    image: str
    input_path: Path
    workspace_volume: str
    name: str
    run_id: str
    command: tuple[str, ...] = ("sleep", "infinity")
    limits: ContainerLimits = field(default_factory=ContainerLimits)

    def __post_init__(self) -> None:
        _validate_image(self.image)
        _validate_resource_name(self.workspace_volume, "workspace_volume")
        _validate_resource_name(self.name, "name")
        _validate_label_value(self.run_id, "run_id")
        if not self.command or any(not isinstance(arg, str) or not arg for arg in self.command):
            raise ValueError("command deve ser uma lista não vazia de argumentos")


@dataclass
class ContainerSession:
    """Identidade de um sandbox criado por este runtime."""

    container_id: str
    name: str
    workspace_volume: str
    runtime: str
    _cleaned: bool = field(default=False, init=False, repr=False)


@dataclass(frozen=True)
class ContainerCommandResult:
    stdout: str
    stderr: str
    returncode: int = 0


@dataclass(frozen=True)
class PatchExport:
    """Patch binário produzido dentro de ``/workspace``."""

    patch: bytes


@dataclass(frozen=True)
class CleanupResult:
    container_removed: bool
    volume_removed: bool
    already_cleaned: bool = False


class ContainerRuntimeBackend(Protocol):
    """Interface que a futura integração pode receber por injeção."""

    capabilities: RuntimeCapabilities

    def start(self, spec: ContainerSpec) -> ContainerSession: ...

    def exec(
        self, session: ContainerSession, args: Sequence[str], *, timeout: float | None = None
    ) -> ContainerCommandResult: ...

    def export_patch(self, session: ContainerSession, *, timeout: float | None = None) -> PatchExport: ...

    def cleanup(self, session: ContainerSession) -> CleanupResult: ...


class ContainerRuntime:
    """Implementação Docker/Podman baseada somente em ``subprocess.run``.

    O ``runner`` permite testes herméticos: nenhuma operação deste módulo exige
    que Docker ou Podman estejam instalados para a suíte unitária.
    """

    def __init__(
        self,
        executable: str,
        *,
        runner: Runner | None = None,
        capabilities: RuntimeCapabilities | None = None,
        default_timeout: float = 30.0,
    ) -> None:
        self.executable = _normalize_executable(executable)
        if default_timeout <= 0:
            raise ValueError("default_timeout deve ser positivo")
        self._runner = runner or subprocess.run
        self.default_timeout = default_timeout
        self.capabilities = capabilities or self.probe(
            self.executable, runner=self._runner, timeout=default_timeout
        )

    @classmethod
    def probe(
        cls, executable: str, *, runner: Runner | None = None, timeout: float = 10.0
    ) -> RuntimeCapabilities:
        """Verifica a disponibilidade sem criar nem alterar containers."""
        normalized = _normalize_executable(executable)
        run = runner or subprocess.run
        command = [normalized, "version", "--format", "{{.Server.Version}}"]
        try:
            result = run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except FileNotFoundError:
            return RuntimeCapabilities(normalized, False, reason="executável não encontrado")
        except subprocess.TimeoutExpired:
            return RuntimeCapabilities(normalized, False, reason="timeout ao consultar engine")
        except OSError as exc:
            return RuntimeCapabilities(normalized, False, reason=_short_detail(str(exc)))

        if int(getattr(result, "returncode", 1)) != 0:
            return RuntimeCapabilities(
                normalized,
                False,
                reason=_short_detail(_as_text(getattr(result, "stderr", ""))) or "engine indisponível",
            )
        version = _as_text(getattr(result, "stdout", "")).strip() or None
        return RuntimeCapabilities(normalized, True, version=version)

    @classmethod
    def discover(
        cls,
        *,
        candidates: Sequence[str] = ("docker", "podman"),
        runner: Runner | None = None,
        timeout: float = 10.0,
    ) -> "ContainerRuntime":
        """Retorna o primeiro runtime disponível, em ordem declarada."""
        outcomes: list[RuntimeCapabilities] = []
        for candidate in candidates:
            capabilities = cls.probe(candidate, runner=runner, timeout=timeout)
            outcomes.append(capabilities)
            if capabilities.available:
                return cls(
                    capabilities.executable,
                    runner=runner,
                    capabilities=capabilities,
                    default_timeout=timeout,
                )
        detail = "; ".join(
            f"{outcome.executable}: {outcome.reason or 'indisponível'}" for outcome in outcomes
        )
        raise ContainerRuntimeUnavailable(
            "runtime_unavailable",
            "nenhum runtime de container compatível está disponível",
            detail=_short_detail(detail),
        )

    def start(self, spec: ContainerSpec) -> ContainerSession:
        """Cria um sandbox com a política imutável de hardening da P0."""
        self._require_available()
        input_path = spec.input_path.resolve()
        if not input_path.is_dir():
            raise ContainerRuntimeError(
                "invalid_input_path",
                "input_path precisa apontar para um diretório existente",
                runtime=self.executable,
            )
        if "," in str(input_path) or any(char in str(input_path) for char in "\r\n\x00"):
            raise ContainerRuntimeError(
                "invalid_input_path",
                "input_path contém caracteres incompatíveis com mount seguro",
                runtime=self.executable,
            )
        command = self._start_command(spec, input_path)
        result = self._run_text(command)
        container_id = result.stdout.strip()
        if not container_id:
            raise ContainerCommandError(
                "empty_container_id",
                "engine não retornou a identidade do container criado",
                runtime=self.executable,
                command=command,
            )
        return ContainerSession(container_id, spec.name, spec.workspace_volume, self.executable)

    def exec(
        self, session: ContainerSession, args: Sequence[str], *, timeout: float | None = None
    ) -> ContainerCommandResult:
        """Executa argumentos diretos no sandbox; não aceita shell nem env."""
        self._require_open_session(session)
        _validate_arguments(args, "args")
        command = [self.executable, "exec", session.container_id, *args]
        return self._run_text(command, timeout=timeout)

    def export_patch(self, session: ContainerSession, *, timeout: float | None = None) -> PatchExport:
        """Exporta somente o diff binário do diretório de trabalho privado."""
        self._require_open_session(session)
        command = [
            self.executable,
            "exec",
            session.container_id,
            "git",
            "-C",
            "/workspace",
            "diff",
            "--binary",
            "--no-ext-diff",
        ]
        result = self._run_bytes(command, timeout=timeout)
        return PatchExport(result)

    def cleanup(self, session: ContainerSession) -> CleanupResult:
        """Remove container e volume privado; repetir a chamada é um no-op."""
        self._assert_own_session(session)
        if session._cleaned:
            return CleanupResult(False, False, already_cleaned=True)

        container_command = [self.executable, "rm", "--force", session.container_id]
        volume_command = [self.executable, "volume", "rm", session.workspace_volume]
        container_removed = self._cleanup_one(container_command)
        volume_removed = self._cleanup_one(volume_command)
        session._cleaned = True
        return CleanupResult(container_removed, volume_removed)

    def _start_command(self, spec: ContainerSpec, input_path: Path) -> list[str]:
        limits = spec.limits
        return [
            self.executable,
            "run",
            "--detach",
            "--name",
            spec.name,
            "--label",
            "bauer.managed=true",
            "--label",
            f"bauer.run_id={spec.run_id}",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            str(limits.pids_limit),
            "--memory",
            f"{limits.memory_bytes}b",
            "--cpus",
            str(limits.cpus),
            "--mount",
            f"type=bind,src={input_path},dst=/input,readonly",
            "--mount",
            f"type=volume,src={spec.workspace_volume},dst=/workspace",
            "--workdir",
            "/workspace",
            spec.image,
            *spec.command,
        ]

    def _run_text(
        self, command: Sequence[str], *, timeout: float | None = None
    ) -> ContainerCommandResult:
        result = self._run(command, timeout=timeout, text=True)
        return ContainerCommandResult(
            stdout=_as_text(getattr(result, "stdout", "")),
            stderr=_as_text(getattr(result, "stderr", "")),
            returncode=0,
        )

    def _run_bytes(self, command: Sequence[str], *, timeout: float | None = None) -> bytes:
        result = self._run(command, timeout=timeout, text=False)
        output = getattr(result, "stdout", b"")
        return output if isinstance(output, bytes) else _as_text(output).encode("utf-8")

    def _run(self, command: Sequence[str], *, timeout: float | None, text: bool) -> Any:
        try:
            result = self._runner(
                list(command),
                capture_output=True,
                text=text,
                timeout=timeout if timeout is not None else self.default_timeout,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise ContainerRuntimeUnavailable(
                "runtime_unavailable",
                "executável do runtime de container não foi encontrado",
                runtime=self.executable,
                command=command,
                detail=_short_detail(str(exc)),
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ContainerCommandError(
                "runtime_timeout",
                "comando do runtime de container excedeu o timeout",
                runtime=self.executable,
                command=command,
                detail=_short_detail(str(exc)),
            ) from exc
        except OSError as exc:
            raise ContainerRuntimeUnavailable(
                "runtime_unavailable",
                "não foi possível chamar o runtime de container",
                runtime=self.executable,
                command=command,
                detail=_short_detail(str(exc)),
            ) from exc

        returncode = int(getattr(result, "returncode", 1))
        if returncode != 0:
            raise ContainerCommandError(
                "runtime_command_failed",
                "runtime de container recusou o comando",
                runtime=self.executable,
                command=command,
                returncode=returncode,
                detail=_short_detail(_as_text(getattr(result, "stderr", ""))),
            )
        return result

    def _cleanup_one(self, command: Sequence[str]) -> bool:
        try:
            self._run(command, timeout=self.default_timeout, text=True)
            return True
        except ContainerCommandError as exc:
            if exc.returncode is not None and _already_absent(exc.detail):
                return False
            raise ContainerRuntimeError(
                "cleanup_failed",
                "não foi possível remover recurso do sandbox",
                runtime=self.executable,
                command=command,
                returncode=exc.returncode,
                detail=exc.detail,
            ) from exc

    def _require_available(self) -> None:
        if not self.capabilities.available:
            raise ContainerRuntimeUnavailable(
                "runtime_unavailable",
                "runtime de container não está disponível",
                runtime=self.executable,
                detail=self.capabilities.reason,
            )
        required = {
            "network_none": self.capabilities.supports_network_none,
            "read_only": self.capabilities.supports_read_only,
            "cap_drop": self.capabilities.supports_cap_drop,
            "no_new_privileges": self.capabilities.supports_no_new_privileges,
            "resource_limits": self.capabilities.supports_resource_limits,
        }
        missing = sorted(name for name, supported in required.items() if not supported)
        if missing:
            raise ContainerRuntimeUnavailable(
                "runtime_capability_missing",
                "runtime de container não atende à política mínima de isolamento",
                runtime=self.executable,
                detail=", ".join(missing),
            )

    def _assert_own_session(self, session: ContainerSession) -> None:
        if session.runtime != self.executable:
            raise ContainerRuntimeError(
                "foreign_session",
                "sessão pertence a outro runtime de container",
                runtime=self.executable,
            )

    def _require_open_session(self, session: ContainerSession) -> None:
        self._assert_own_session(session)
        if session._cleaned:
            raise ContainerRuntimeError(
                "session_closed",
                "sessão de container já foi limpa",
                runtime=self.executable,
            )


class FakeContainerRuntime:
    """Fake determinístico para integração sem Docker/Podman instalado."""

    def __init__(self, *, executable: str = "fake", patch: bytes = b"") -> None:
        self.capabilities = RuntimeCapabilities(executable, True, version="fake")
        self.executable = executable
        self.patch = patch
        self.starts: list[ContainerSpec] = []
        self.exec_calls: list[tuple[str, tuple[str, ...]]] = []
        self.cleanups: list[str] = []
        self._next_id = 0

    def start(self, spec: ContainerSpec) -> ContainerSession:
        if not spec.input_path.resolve().is_dir():
            raise ContainerRuntimeError("invalid_input_path", "input_path precisa existir")
        self.starts.append(spec)
        self._next_id += 1
        return ContainerSession(
            f"fake-{self._next_id}", spec.name, spec.workspace_volume, self.executable
        )

    def exec(
        self, session: ContainerSession, args: Sequence[str], *, timeout: float | None = None
    ) -> ContainerCommandResult:
        del timeout
        self._require_open_session(session)
        _validate_arguments(args, "args")
        self.exec_calls.append((session.container_id, tuple(args)))
        return ContainerCommandResult(stdout="", stderr="")

    def export_patch(self, session: ContainerSession, *, timeout: float | None = None) -> PatchExport:
        del timeout
        self._require_open_session(session)
        return PatchExport(self.patch)

    def cleanup(self, session: ContainerSession) -> CleanupResult:
        self._assert_own_session(session)
        if session._cleaned:
            return CleanupResult(False, False, already_cleaned=True)
        session._cleaned = True
        self.cleanups.append(session.container_id)
        return CleanupResult(True, True)

    def _assert_own_session(self, session: ContainerSession) -> None:
        if session.runtime != self.executable:
            raise ContainerRuntimeError("foreign_session", "sessão pertence a outro runtime")

    def _require_open_session(self, session: ContainerSession) -> None:
        self._assert_own_session(session)
        if session._cleaned:
            raise ContainerRuntimeError("session_closed", "sessão de container já foi limpa")


def _normalize_executable(executable: str) -> str:
    value = str(executable).strip()
    base = Path(value).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base not in _RUNTIME_NAMES:
        raise ValueError("runtime precisa ser docker ou podman")
    return value


def _validate_resource_name(value: str, field_name: str) -> None:
    if not value or any(char not in _RESOURCE_NAME_CHARS for char in value):
        raise ValueError(f"{field_name} contém caracteres inválidos")


def _validate_label_value(value: str, field_name: str) -> None:
    if not value or any(char not in _LABEL_VALUE_CHARS for char in value):
        raise ValueError(f"{field_name} contém caracteres inválidos")


def _validate_image(image: str) -> None:
    if not image or image.startswith("-") or any(char.isspace() for char in image):
        raise ValueError("image inválida")


def _validate_arguments(args: Sequence[str], field_name: str) -> None:
    if not args or any(not isinstance(arg, str) or not arg for arg in args):
        raise ValueError(f"{field_name} deve ser uma lista não vazia de argumentos")


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _short_detail(value: str, limit: int = 500) -> str | None:
    cleaned = value.strip().replace("\x00", "")
    return cleaned[:limit] or None


def _already_absent(detail: str | None) -> bool:
    text = (detail or "").lower()
    return "no such" in text or "not found" in text or "does not exist" in text
