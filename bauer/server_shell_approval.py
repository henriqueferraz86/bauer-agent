"""Session-bound approval of shell allowlist denials for the HTTP chat.

Only ShellRunner can originate a pending request. User/model text never selects
the executable to authorize, and accepted requests return to the governed motor.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator

from .shell_runner import ShellRunner, add_learned_command, load_learned_commands, observe_blocked_commands


logger = logging.getLogger("bauer.server_shell_approval")
_ACCEPT = frozenset({"sim", "autorizo", "permito", "sim permanentemente"})
_REFUSE = frozenset({"não", "nao", "nego", "não autorizo", "nao autorizo", "não permito", "nao permito", "cancelar", "cancele"})


@dataclass(frozen=True)
class _PendingCommand:
    workspace: Path
    task: str
    command: str
    args: tuple[str, ...]
    base: str
    expires_at: float
    presented: bool = False


def _workspace(value: Any) -> Path | None:
    if not isinstance(value, (str, Path)):
        return None
    try:
        return Path(value).resolve()
    except (OSError, ValueError):
        return None


class ShellApprovalManager:
    """Keep short-lived requests isolated by session and effective workspace."""

    def __init__(self, *, ttl_seconds: float = 600, clock: Callable[[], float] | None = None):
        self._ttl_seconds = ttl_seconds
        self._clock = clock or time.monotonic
        self._pending: dict[str, _PendingCommand] = {}
        self._generations: dict[str, object] = {}
        self._lock = threading.RLock()

    def _prune_expired(self) -> None:
        """Drop abandoned requests during regular traffic, under self._lock."""
        now = self._clock()
        expired = [sid for sid, pending in self._pending.items() if pending.expires_at <= now]
        for sid in expired:
            self._pending.pop(sid, None)

    def _current(self, session_id: str, workspace: Path | None) -> _PendingCommand | None:
        """Read under self._lock and invalidate stale or differently rooted requests."""
        pending = self._pending.get(session_id)
        if pending and (workspace != pending.workspace or pending.expires_at <= self._clock()):
            self._pending.pop(session_id, None)
            return None
        return pending

    @contextmanager
    def capture(self, session_id: str, workspace: Any, task: str) -> Iterator[None]:
        """Capture the first actual allowlist denial in a turn, preserving denial."""
        expected_workspace = _workspace(workspace)
        captured = False
        active = True
        with self._lock:
            self._prune_expired()
            generation = object()
            self._generations[session_id] = generation

        def observer(runner: ShellRunner, command: str, args: list[str]) -> None:
            nonlocal captured
            if not session_id or expected_workspace is None or runner.workspace != expected_workspace:
                return
            base = runner._base_command(args)
            if not base:
                return
            with self._lock:
                if captured or not active or self._generations.get(session_id) is not generation:
                    return
                self._pending[session_id] = _PendingCommand(
                    workspace=expected_workspace, task=task, command=command,
                    args=tuple(args), base=base, expires_at=self._clock() + self._ttl_seconds,
                )
                captured = True

        try:
            with observe_blocked_commands(observer):
                yield
        finally:
            with self._lock:
                active = False
                if self._generations.get(session_id) is generation:
                    self._generations.pop(session_id, None)

    def prompt(self, session_id: str, workspace: Any, *, presented: bool = True) -> str | None:
        """Read the question; mark presented only when the transport delivers it."""
        with self._lock:
            pending = self._current(session_id, _workspace(workspace))
            if pending is None:
                return None
            if presented:
                self._pending[session_id] = replace(pending, presented=True)
            return (
                f"O comando '{pending.base}' está fora da allowlist. "
                "Você autoriza adicioná-lo permanentemente à allowlist para eu executar? "
                "Responda 'sim' ou 'não'."
            )

    def resolve(self, session_id: str, message: str, router: Any) -> str:
        """Consume exact consent for a known command; never execute it here."""
        with self._lock:
            self._prune_expired()
            # A new user turn invalidates observers retained by an older worker.
            self._generations.pop(session_id, None)
            workspace = _workspace(getattr(router, "workspace", None))
            pending = self._current(session_id, workspace)
            if pending is None:
                return message
            if not pending.presented:
                self._pending.pop(session_id, None)
                return message
            answer = message.strip().casefold()
            if answer not in _ACCEPT and answer not in _REFUSE:
                self._pending.pop(session_id, None)
                return message
            self._pending.pop(session_id, None)
            if answer in _REFUSE:
                return (
                    f"O usuário recusou autorizar o comando '{pending.base}'. "
                    "Não execute o comando pendente nem o substitua para contornar a recusa. "
                    "Informe que a autorização foi recusada."
                )
            runner = getattr(router, "_shell_runner", None)
            available_tools = getattr(router, "available_tools", None)
            try:
                authorized_tools = available_tools() if callable(available_tools) else []
            except Exception as exc:  # noqa: BLE001 — unavailable policy cannot grant access
                logger.debug("shell tools unavailable during approval: %s", exc)
                authorized_tools = []
            if (not isinstance(runner, ShellRunner) or runner.workspace != pending.workspace
                    or not {"run_command", "process"}.intersection(authorized_tools)):
                return "Não foi possível autorizar o comando: o shell deste workspace está indisponível. Não execute o comando pendente."
            try:
                runner._check_denylist(pending.command)
                args = runner._parse_command(pending.command)
                base = runner._base_command(args)
                if tuple(args) != pending.args or base != pending.base or not base:
                    raise ValueError("pending command identity changed")
                add_learned_command(base)
                if base not in load_learned_commands():
                    raise OSError("command permission was not persisted")
                runner._runtime_allowed.add(base)
            except Exception as exc:  # noqa: BLE001 — a failed accessory write grants nothing
                logger.debug("shell allowlist approval failed: %s", exc)
                return (
                    "Não foi possível salvar a autorização permanente do comando. "
                    "Não anuncie sucesso nem execute o comando pendente; informe a falha de persistência."
                )
            return (
                f"O usuário autorizou adicionar permanentemente '{base}' à allowlist; "
                "a permissão foi salva e verificada. Continue a tarefa original no mesmo workspace. "
                "Execute o comando pelo ToolRouter e mantenha policy, denylist e safe_mode; "
                "a autorização da allowlist não dispensa esses controles.\n"
                f"Tarefa original: {pending.task}\nComando pendente: {pending.command}"
            )
