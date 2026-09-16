"""Supervisor seguro para vários workspaces Bauer.

O fleet é um coordenador de processos e estado. Ele não chama provider,
shell ou Kernel: para cada projeto elegível delega ao ``runtime supervise``
existente, que continua sendo o dono do dispatcher e do Autopilot daquele
workspace.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .supervisor import RuntimeSupervisor

logger = logging.getLogger("bauer.fleet_supervisor")

PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "TASKS.md",
)
DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    ".bauer_runtime",
    ".bauer_fleet",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    "target",
    "backup",
    ".backup",
    "_backup_root",
}


class FleetError(Exception):
    """Erro operacional do fleet."""


@dataclass(frozen=True)
class FleetProject:
    """Projeto descoberto e aprovado pela heurística do fleet."""

    id: str
    path: Path
    marker: str

    @property
    def relative_path(self) -> str:
        return self.path.name

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "path": str(self.path),
            "relative_path": self.relative_path,
            "marker": self.marker,
        }


@dataclass
class FleetProjectRuntime:
    id: str
    path: str
    marker: str
    state: str = "discovered"
    pid: int | None = None
    starts: int = 0
    restarts: int = 0
    last_started_at: str = ""
    last_error: str = ""
    runtime_state: str = "not_started"
    supervisor_alive: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "marker": self.marker,
            "state": self.state,
            "pid": self.pid,
            "starts": self.starts,
            "restarts": self.restarts,
            "last_started_at": self.last_started_at,
            "last_error": self.last_error,
            "runtime_state": self.runtime_state,
            "supervisor_alive": self.supervisor_alive,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FleetProjectRuntime":
        return cls(
            id=str(raw.get("id", "")),
            path=str(raw.get("path", "")),
            marker=str(raw.get("marker", "")),
            state=str(raw.get("state", "discovered")),
            pid=_as_int(raw.get("pid")),
            starts=max(0, _as_int(raw.get("starts")) or 0),
            restarts=max(0, _as_int(raw.get("restarts")) or 0),
            last_started_at=str(raw.get("last_started_at", "")),
            last_error=str(raw.get("last_error", "")),
            runtime_state=str(raw.get("runtime_state", "not_started")),
            supervisor_alive=bool(raw.get("supervisor_alive", False)),
        )


@dataclass
class FleetStatus:
    root: str
    state: str
    fleet_pid: int | None
    fleet_alive: bool
    generated_at: str
    paused: bool
    kill_switch: bool
    projects: list[dict[str, Any]] = field(default_factory=list)
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "state": self.state,
            "fleet_pid": self.fleet_pid,
            "fleet_alive": self.fleet_alive,
            "generated_at": self.generated_at,
            "paused": self.paused,
            "kill_switch": self.kill_switch,
            "projects": self.projects,
            "last_error": self.last_error,
        }


class FleetStateStore:
    """Estado persistente global do fleet, separado dos projetos."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.runtime_dir = self.root / ".bauer_fleet"
        self.state_file = self.runtime_dir / "fleet.json"
        self.stop_file = self.runtime_dir / "STOP"
        self.pause_file = self.runtime_dir / "PAUSE"
        self.kill_switch_file = self.runtime_dir / "KILL_SWITCH"
        self.lock_file = self.runtime_dir / "fleet.lock"
        self.log_file = self.runtime_dir / "fleet.log"

    def read(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def write(self, payload: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.state_file)

    def mark(self, path: Path, value: str = "operator") -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    @staticmethod
    def clear(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            logger.debug("fleet control marker already absent: %s", path)

    def acquire_lock(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raw = self.lock_file.read_text(encoding="utf-8", errors="replace").strip()
            if _pid_alive(_as_int(raw)):
                raise FleetError(f"fleet já está rodando (pid={raw})") from exc
            self.clear(self.lock_file)
            handle = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))

    def release_lock(self) -> None:
        self.clear(self.lock_file)


def project_id(path: Path) -> str:
    """ID estável por caminho canônico, sem expor o caminho em identificadores."""

    digest = hashlib.sha256(str(path.resolve()).casefold().encode("utf-8")).hexdigest()
    return digest[:16]


def discover_projects(
    root: str | Path,
    *,
    max_depth: int = 1,
    require_project_marker: bool = True,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    max_projects: int = 20,
) -> list[FleetProject]:
    """Descobre projetos sem seguir symlinks e sem executar código.

    A raiz nunca é incluída. ``max_depth=1`` considera apenas filhos diretos;
    profundidades maiores são úteis para agrupadores, mas continuam limitadas.
    """

    base = Path(root).expanduser().resolve()
    if not base.exists() or not base.is_dir():
        return []
    user_excludes = tuple(str(item).replace("\\", "/") for item in exclude)
    includes = tuple(str(item).replace("\\", "/") for item in include)
    depth_limit = max(1, int(max_depth))
    candidates: list[FleetProject] = []

    def walk(directory: Path, depth: int) -> None:
        if depth > depth_limit:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or entry.is_symlink():
                continue
            relative = entry.relative_to(base).as_posix()
            if _excluded(entry, relative, user_excludes):
                continue
            marker = _project_marker(entry) if require_project_marker else "directory"
            if marker and _included(entry, relative, includes):
                candidates.append(FleetProject(project_id(entry), entry.resolve(), marker))
                continue
            walk(entry, depth + 1)

    walk(base, 1)
    candidates.sort(key=lambda item: item.path.as_posix().casefold())
    return candidates[: max(1, int(max_projects))]


class FleetSupervisor:
    """Coordena runtimes isolados, um por projeto descoberto."""

    def __init__(
        self,
        root: str | Path,
        *,
        config: str | Path = "config.yaml",
        models: str | Path = "models.yaml",
        fleet_config: Any | None = None,
        python: str | None = None,
        cwd: str | Path | None = None,
    ):
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.config = Path(config).expanduser()
        self.models = Path(models).expanduser()
        if not self.config.is_absolute():
            self.config = (self.cwd / self.config).resolve()
        if not self.models.is_absolute():
            self.models = (self.cwd / self.models).resolve()
        self.python = python or sys.executable
        self.fleet_config = fleet_config
        self.store = FleetStateStore(root)
        self._project_runtime: dict[str, FleetProjectRuntime] = {}

    @property
    def root(self) -> Path:
        return self.store.root

    def projects(self) -> list[FleetProject]:
        cfg = self.fleet_config
        return discover_projects(
            self.root,
            max_depth=int(getattr(cfg, "max_depth", 1)),
            require_project_marker=bool(getattr(cfg, "require_project_marker", True)),
            include=getattr(cfg, "include", []),
            exclude=getattr(cfg, "exclude", []),
            max_projects=int(getattr(cfg, "max_projects", 20)),
        )

    def discover_status(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "projects": [project.to_dict() for project in self.projects()],
            "count": len(self.projects()),
        }

    def tick(self) -> FleetStatus:
        now = _now_iso()
        state = self.store.read()
        previous = {
            str(item.get("id")): FleetProjectRuntime.from_dict(item)
            for item in state.get("projects", [])
            if isinstance(item, dict) and item.get("id")
        }
        projects = self.projects()
        active = 0
        runtimes: list[FleetProjectRuntime] = []
        paused = self.store.pause_file.exists()
        kill_switch = self.store.kill_switch_file.exists()
        cfg = self.fleet_config
        max_parallel = max(1, int(getattr(cfg, "max_parallel_projects", 20)))

        for index, project in enumerate(projects):
            runtime = previous.get(project.id) or FleetProjectRuntime(
                id=project.id,
                path=str(project.path),
                marker=project.marker,
            )
            runtime.path = str(project.path)
            runtime.marker = project.marker
            supervisor = RuntimeSupervisor(
                project.path,
                config=self.config,
                models=self.models,
                python=self.python,
                cwd=self.cwd,
            )
            project_status = supervisor.status().to_public_dict()
            alive = bool(project_status.get("supervisor_alive"))
            reported_pid = _as_int(project_status.get("supervisor_pid"))
            if not alive and runtime.pid and _pid_alive(runtime.pid):
                # O filho pode ainda não ter persistido supervisor.json no
                # primeiro ciclo; o PID do estado do fleet evita um segundo
                # lançamento durante essa janela.
                alive = True
            runtime.runtime_state = str(project_status.get("state", "not_started"))
            runtime.supervisor_alive = alive
            runtime.pid = reported_pid or (runtime.pid if alive else None)
            if alive:
                active += 1
                runtime.state = "paused" if paused or kill_switch else "running"
                if paused or kill_switch:
                    _pause_project(project.path)
            elif paused or kill_switch:
                runtime.state = "paused"
                runtime.pid = None
            elif active < max_parallel and _should_start(runtime, cfg):
                try:
                    args = self._runtime_args(project, index, cfg)
                    result = supervisor.start_background(args)
                    runtime.pid = _as_int(result.get("pid"))
                    runtime.starts += 1
                    if runtime.starts > 1:
                        runtime.restarts += 1
                    runtime.last_started_at = now
                    runtime.last_error = ""
                    runtime.state = "starting"
                    runtime.supervisor_alive = bool(runtime.pid)
                    active += 1
                except Exception as exc:  # one bad project cannot sink fleet
                    runtime.state = "failed"
                    runtime.last_error = str(exc)[:500]
                    logger.exception("fleet failed to start project %s", project.path)
            else:
                runtime.state = "capped" if active >= max_parallel else "stopped"
                runtime.pid = None
            runtimes.append(runtime)

        payload = {
            "schema_version": 1,
            "state": "paused" if paused or kill_switch else "running",
            "root": str(self.root),
            "fleet_pid": os.getpid(),
            "heartbeat_at": now,
            "projects": [runtime.to_dict() for runtime in runtimes],
        }
        self.store.write(payload)
        return self.status()

    def run_forever(self) -> None:
        self.store.acquire_lock()
        self.store.clear(self.store.stop_file)
        try:
            while not self.store.stop_file.exists():
                self.tick()
                interval = max(1.0, float(getattr(self.fleet_config, "poll_interval_s", 30.0)))
                time.sleep(interval)
        except KeyboardInterrupt:
            self.store.mark(self.store.stop_file, "keyboard_interrupt")
        finally:
            self.stop_projects()
            state = self.store.read()
            state.update({"state": "stopped", "heartbeat_at": _now_iso()})
            self.store.write(state)
            self.store.release_lock()

    def start_background(self) -> dict[str, Any]:
        try:
            self.store.acquire_lock()
        except FleetError:
            state = self.store.read()
            pid = _as_int(state.get("fleet_pid"))
            if pid and _pid_alive(pid):
                return {"pid": pid, "already_running": True, "log_path": str(self.store.log_file)}
            raise
        try:
            state = self.store.read()
            pid = _as_int(state.get("fleet_pid"))
            if pid and _pid_alive(pid):
                return {"pid": pid, "already_running": True, "log_path": str(self.store.log_file)}
            self.store.clear(self.store.stop_file)
            self.store.runtime_dir.mkdir(parents=True, exist_ok=True)
            command = [
                self.python,
                "-m",
                "bauer.cli",
                "runtime",
                "fleet",
                "supervise",
                "--root",
                str(self.root),
                "--config",
                str(self.config),
                "--models",
                str(self.models),
            ]
            handle = self.store.log_file.open("ab")
            try:
                kwargs: dict[str, Any] = {
                    "cwd": str(self.cwd),
                    "stdout": handle,
                    "stderr": subprocess.STDOUT,
                    "stdin": subprocess.DEVNULL,
                    "close_fds": True,
                }
                if os.name == "nt":
                    kwargs["creationflags"] = (
                        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                        | getattr(subprocess, "DETACHED_PROCESS", 0)
                    )
                else:
                    kwargs["start_new_session"] = True
                process = subprocess.Popen(command, **kwargs)
            finally:
                handle.close()
            self.store.write(
                {
                    "schema_version": 1,
                    "state": "starting",
                    "root": str(self.root),
                    "fleet_pid": process.pid,
                    "heartbeat_at": _now_iso(),
                    "projects": [],
                }
            )
            return {"pid": process.pid, "already_running": False, "log_path": str(self.store.log_file)}
        finally:
            self.store.release_lock()

    def stop_projects(self) -> None:
        seen: set[str] = set()
        for project in self.projects():
            seen.add(project.id)
            try:
                RuntimeSupervisor(project.path, config=self.config, models=self.models).request_stop(terminate=True)
            except Exception:
                logger.exception("fleet failed to stop project %s", project.path)
        for raw in self.store.read().get("projects", []):
            if not isinstance(raw, dict) or str(raw.get("id")) in seen:
                continue
            raw_path = str(raw.get("path", "")).strip()
            if raw_path:
                path = Path(raw_path)
                try:
                    RuntimeSupervisor(path, config=self.config, models=self.models).request_stop(terminate=True)
                except Exception:
                    logger.exception("fleet failed to stop stale project %s", path)

    def request_stop(self, *, terminate: bool = True) -> dict[str, Any]:
        self.store.mark(self.store.stop_file, "operator_stop")
        if terminate:
            self.stop_projects()
        return self.status().to_dict()

    def set_paused(self, paused: bool) -> dict[str, Any]:
        if paused:
            self.store.mark(self.store.pause_file, "operator_pause")
            for project in self.projects():
                _pause_project(project.path)
        else:
            self.store.clear(self.store.pause_file)
            for project in self.projects():
                _resume_project(project.path)
        return self.status().to_dict()

    def set_kill_switch(self, enabled: bool) -> dict[str, Any]:
        if enabled:
            self.store.mark(self.store.kill_switch_file, "operator_kill_switch")
            for project in self.projects():
                _pause_project(project.path)
        else:
            self.store.clear(self.store.kill_switch_file)
        return self.status().to_dict()

    def status(self) -> FleetStatus:
        state = self.store.read()
        project_rows: list[dict[str, Any]] = []
        for project in self.projects():
            supervisor = RuntimeSupervisor(project.path, config=self.config, models=self.models)
            project_status = supervisor.status().to_public_dict()
            row = next(
                (item for item in state.get("projects", []) if isinstance(item, dict) and item.get("id") == project.id),
                {},
            )
            project_rows.append(
                {
                    **dict(row),
                    **project.to_dict(),
                    "runtime_state": project_status.get("state", "not_started"),
                    "supervisor_pid": project_status.get("supervisor_pid"),
                    "supervisor_alive": project_status.get("supervisor_alive", False),
                    "autopilot": project_status.get("autopilot"),
                }
            )
        fleet_pid = _as_int(state.get("fleet_pid"))
        return FleetStatus(
            root=str(self.root),
            state=str(state.get("state", "not_started")),
            fleet_pid=fleet_pid,
            fleet_alive=_pid_alive(fleet_pid),
            generated_at=_now_iso(),
            paused=self.store.pause_file.exists(),
            kill_switch=self.store.kill_switch_file.exists(),
            projects=project_rows,
            last_error=str(state.get("last_error", "")),
        )

    def _runtime_args(self, project: FleetProject, index: int, cfg: Any) -> list[str]:
        args = [
            "--workspace",
            str(project.path),
            "--config",
            str(self.config),
            "--models",
            str(self.models),
            "--autopilot",
            "--max-spawn",
            "1",
            "--max-in-progress",
            "1",
        ]
        if bool(getattr(cfg, "start_kanban", False)):
            args.extend(
                [
                    "--kanban",
                    "--kanban-port",
                    str(int(getattr(cfg, "kanban_base_port", 8765)) + index),
                ]
            )
        else:
            args.append("--no-kanban")
        return args


def fleet_root_from_config(config: Any, explicit: str | Path | None = None) -> Path:
    if explicit is not None and str(explicit).strip():
        return Path(explicit).expanduser().resolve()
    configured = str(getattr(config, "root", "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".bauer" / "workspace"


def _project_marker(path: Path) -> str:
    for marker in PROJECT_MARKERS:
        if (path / marker).exists():
            return marker
    return ""


def _excluded(path: Path, relative: str, patterns: Iterable[str]) -> bool:
    parts = set(path.parts)
    if any(part in DEFAULT_EXCLUDES for part in parts):
        return True
    candidates = (relative, path.name)
    return any(fnmatch.fnmatch(candidate, pattern) for pattern in patterns for candidate in candidates)


def _included(path: Path, relative: str, patterns: Iterable[str]) -> bool:
    patterns = tuple(patterns)
    if not patterns:
        return True
    return any(
        fnmatch.fnmatch(candidate, pattern)
        for pattern in patterns
        for candidate in (relative, path.name)
    )


def _should_start(runtime: FleetProjectRuntime, cfg: Any) -> bool:
    if runtime.starts == 0:
        return True
    return bool(getattr(cfg, "auto_restart", True)) and runtime.restarts < int(
        getattr(cfg, "max_restarts_per_project", 5)
    )


def _pause_project(path: Path) -> None:
    runtime_dir = path / ".bauer_runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "AUTOPILOT_PAUSE").write_text("fleet_pause", encoding="utf-8")


def _resume_project(path: Path) -> None:
    try:
        (path / ".bauer_runtime" / "AUTOPILOT_PAUSE").unlink()
    except FileNotFoundError:
        logger.debug("project autopilot pause marker already absent: %s", path)


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
