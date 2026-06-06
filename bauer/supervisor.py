"""Process supervisor for the Bauer always-on runtime.

The supervisor is intentionally small and boring: it starts the existing Bauer
daemons as child processes, records their state under the workspace, and
restarts crashed services with bounded backoff.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"disabled", "stopped"}
RUNNING_STATE = "running"


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    command: list[str]
    enabled: bool = True
    restart: bool = True
    backoff_seconds: int = 5
    description: str = ""


@dataclass
class ServiceRuntime:
    name: str
    command: list[str]
    enabled: bool
    restart: bool
    state: str = "disabled"
    pid: int | None = None
    starts: int = 0
    restarts: int = 0
    exit_code: int | None = None
    last_started_at: str = ""
    last_stopped_at: str = ""
    last_error: str = ""
    backoff_until: float = 0.0
    log_path: str = ""
    description: str = ""

    @classmethod
    def from_spec(cls, spec: ServiceSpec, log_path: Path) -> "ServiceRuntime":
        return cls(
            name=spec.name,
            command=list(spec.command),
            enabled=spec.enabled,
            restart=spec.restart,
            state="stopped" if spec.enabled else "disabled",
            log_path=str(log_path),
            description=spec.description,
        )

    def to_public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["alive"] = _pid_alive(self.pid) if self.pid else False
        if self.backoff_until:
            data["backoff_seconds_left"] = max(0, int(self.backoff_until - time.time()))
        else:
            data["backoff_seconds_left"] = 0
        return data


@dataclass
class SupervisorStatus:
    workspace: str
    runtime_dir: str
    supervisor_pid: int | None
    supervisor_alive: bool
    state: str
    generated_at: str
    heartbeat_at: str = ""
    stop_requested: bool = False
    services: list[dict[str, Any]] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


class RuntimeStateStore:
    """JSON state files for the runtime supervisor."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.runtime_dir = self.workspace / ".bauer_runtime"
        self.logs_dir = self.runtime_dir / "logs"
        self.state_file = self.runtime_dir / "supervisor.json"
        self.stop_file = self.runtime_dir / "STOP"

    def read(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def write(self, state: dict[str, Any]) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_file)

    def request_stop(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.stop_file.write_text(_now_iso(), encoding="utf-8")

    def clear_stop(self) -> None:
        try:
            self.stop_file.unlink()
        except FileNotFoundError:
            pass


class RuntimeSupervisor:
    """Supervise Bauer runtime services as child processes."""

    def __init__(
        self,
        workspace: str | Path = "workspace",
        *,
        config: str | Path = "config.yaml",
        models: str | Path = "models.yaml",
        python: str | None = None,
        cwd: str | Path | None = None,
    ):
        self.workspace = Path(workspace).resolve()
        self.config = Path(config)
        self.models = Path(models)
        self.python = python or sys.executable
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.store = RuntimeStateStore(self.workspace)
        self._processes: dict[str, subprocess.Popen] = {}
        self._handles: dict[str, Any] = {}
        self._services: dict[str, ServiceRuntime] = {}

    def build_service_specs(
        self,
        *,
        dispatcher: bool = True,
        cron: bool = True,
        outbox: bool = True,
        kanban: bool = True,
        dispatch_interval: int = 30,
        cron_interval: int = 60,
        outbox_interval: int = 30,
        kanban_host: str = "127.0.0.1",
        kanban_port: int = 8765,
        max_spawn: int = 1,
        max_in_progress: int = 1,
        max_jobs: int = 10,
        delivery_limit: int = 20,
    ) -> list[ServiceSpec]:
        return [
            ServiceSpec(
                name="dispatcher",
                enabled=dispatcher,
                command=[
                    self.python,
                    "-m",
                    "bauer.cli",
                    "dispatch",
                    "daemon",
                    "--workspace",
                    str(self.workspace),
                    "--config",
                    str(self.config),
                    "--models",
                    str(self.models),
                    "--interval",
                    str(max(1, int(dispatch_interval))),
                    "--max-spawn",
                    str(max(1, int(max_spawn))),
                    "--max-in-progress",
                    str(max(1, int(max_in_progress))),
                ],
                description="Durable READY task dispatcher.",
            ),
            ServiceSpec(
                name="cron",
                enabled=cron,
                command=[
                    self.python,
                    "-m",
                    "bauer.cli",
                    "cron",
                    "daemon",
                    "--workspace",
                    str(self.workspace),
                    "--interval",
                    str(max(1, int(cron_interval))),
                    "--max-jobs",
                    str(max(1, int(max_jobs))),
                ],
                description="Durable automation scheduler.",
            ),
            ServiceSpec(
                name="outbox",
                enabled=outbox,
                command=[
                    self.python,
                    "-m",
                    "bauer.cli",
                    "gateway-deliver",
                    "--workspace",
                    str(self.workspace),
                    "--limit",
                    str(max(1, int(delivery_limit))),
                    "--watch",
                    "--interval",
                    str(max(1, int(outbox_interval))),
                ],
                description="Gateway delivery outbox worker.",
            ),
            ServiceSpec(
                name="kanban",
                enabled=kanban,
                restart=False,
                backoff_seconds=10,
                command=[
                    self.python,
                    "-m",
                    "bauer.cli",
                    "kanban",
                    "--workspace",
                    str(self.workspace),
                    "--host",
                    kanban_host,
                    "--port",
                    str(max(1, int(kanban_port))),
                    "--no-browser",
                ],
                description="Kanban dashboard HTTP server.",
            ),
        ]

    def run_forever(
        self,
        specs: list[ServiceSpec],
        *,
        supervisor_interval: int = 5,
        once: bool = False,
    ) -> None:
        self.store.clear_stop()
        self._prepare_services(specs)
        self._write_state("starting")
        try:
            for service in self._services.values():
                if service.enabled:
                    self._start_service(service)
            self._write_state("running")
            while not self.store.stop_file.exists():
                self.tick()
                self._write_state("running")
                if once:
                    return
                time.sleep(max(1, int(supervisor_interval)))
        except KeyboardInterrupt:
            self.store.request_stop()
        finally:
            self.stop_services()
            self._write_state("stopped")
            self.store.clear_stop()

    def tick(self) -> None:
        now = time.time()
        for name, service in self._services.items():
            if not service.enabled:
                service.state = "disabled"
                continue
            process = self._processes.get(name)
            if process is not None and process.poll() is None:
                service.state = RUNNING_STATE
                service.pid = process.pid
                continue
            if process is not None:
                service.exit_code = process.poll()
                service.pid = None
                service.last_stopped_at = _now_iso()
                service.state = "failed" if service.exit_code else "stopped"
                self._close_handle(name)
                self._processes.pop(name, None)
                if service.restart:
                    service.restarts += 1
                    service.backoff_until = now + max(1, int(service.restarts * 2))
            if service.restart and service.state not in TERMINAL_STATES and now >= service.backoff_until:
                self._start_service(service)

    def stop_services(self, *, timeout: float = 5.0) -> None:
        for name, process in list(self._processes.items()):
            service = self._services.get(name)
            _terminate_process(process, timeout=timeout)
            if service is not None:
                service.pid = None
                service.exit_code = process.poll()
                service.state = "stopped"
                service.last_stopped_at = _now_iso()
        self._processes.clear()
        for name in list(self._handles):
            self._close_handle(name)

    def request_stop(self, *, terminate: bool = True) -> dict[str, Any]:
        self.store.request_stop()
        state = self.store.read()
        if terminate:
            for service in state.get("services", []):
                if isinstance(service, dict):
                    _terminate_pid(_to_int(service.get("pid")))
            supervisor_pid = _to_int(state.get("supervisor_pid"))
            if supervisor_pid and supervisor_pid != os.getpid():
                _terminate_pid(supervisor_pid)
        return self.status().to_public_dict()

    def status(self) -> SupervisorStatus:
        state = self.store.read()
        supervisor_pid = _to_int(state.get("supervisor_pid")) or None
        services = state.get("services", [])
        if not isinstance(services, list):
            services = []
        public_services: list[dict[str, Any]] = []
        for service in services:
            if not isinstance(service, dict):
                continue
            current = dict(service)
            pid = _to_int(current.get("pid")) or None
            current["alive"] = _pid_alive(pid) if pid else False
            public_services.append(current)
        return SupervisorStatus(
            workspace=str(self.workspace),
            runtime_dir=str(self.store.runtime_dir),
            supervisor_pid=supervisor_pid,
            supervisor_alive=_pid_alive(supervisor_pid) if supervisor_pid else False,
            state=str(state.get("state") or "not_started"),
            generated_at=_now_iso(),
            heartbeat_at=str(state.get("heartbeat_at") or ""),
            stop_requested=self.store.stop_file.exists(),
            services=public_services,
        )

    def start_background(
        self,
        args: list[str],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        command = [self.python, "-m", "bauer.cli", "runtime", "supervise", *args]
        if dry_run:
            return {"command": command, "pid": None, "dry_run": True}
        self.store.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.store.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.store.logs_dir / "supervisor.log"
        handle = log_path.open("ab")
        try:
            popen_kwargs: dict[str, Any] = {
                "cwd": str(self.cwd),
                "stdout": handle,
                "stderr": subprocess.STDOUT,
                "stdin": subprocess.DEVNULL,
                "close_fds": True,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                )
            else:
                popen_kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **popen_kwargs)
        finally:
            handle.close()
        return {"command": command, "pid": process.pid, "dry_run": False, "log_path": str(log_path)}

    def _prepare_services(self, specs: list[ServiceSpec]) -> None:
        self.store.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.store.logs_dir.mkdir(parents=True, exist_ok=True)
        self._services = {
            spec.name: ServiceRuntime.from_spec(spec, self.store.logs_dir / f"{spec.name}.log")
            for spec in specs
        }

    def _start_service(self, service: ServiceRuntime) -> None:
        self.store.logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = Path(service.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("ab")
        try:
            _write_log_banner(handle, f"starting {service.name}: {' '.join(service.command)}")
            process = subprocess.Popen(
                service.command,
                cwd=str(self.cwd),
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as exc:
            handle.close()
            service.state = "failed"
            service.last_error = str(exc)
            service.backoff_until = time.time() + max(1, int(service.backoff_seconds))
            return
        self._handles[service.name] = handle
        self._processes[service.name] = process
        service.pid = process.pid
        service.state = RUNNING_STATE
        service.starts += 1
        service.exit_code = None
        service.last_started_at = _now_iso()
        service.last_error = ""
        service.backoff_until = 0.0

    def _close_handle(self, name: str) -> None:
        handle = self._handles.pop(name, None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _write_state(self, state: str) -> None:
        payload = {
            "schema_version": 1,
            "state": state,
            "workspace": str(self.workspace),
            "runtime_dir": str(self.store.runtime_dir),
            "supervisor_pid": os.getpid(),
            "cwd": str(self.cwd),
            "heartbeat_at": _now_iso(),
            "services": [service.to_public_dict() for service in self._services.values()],
        }
        self.store.write(payload)


def tail_log(path: str | Path, *, lines: int = 80) -> list[str]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    raw = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return raw[-max(1, int(lines)):]


def _write_log_banner(handle: Any, message: str) -> None:
    handle.write(f"\n[{_now_iso()}] {message}\n".encode("utf-8", errors="replace"))
    try:
        handle.flush()
    except OSError:
        pass


def _terminate_process(process: subprocess.Popen, *, timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _terminate_pid(pid: int) -> None:
    if not pid or pid <= 0:
        return
    if pid == os.getpid():
        return
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        return
    except Exception:
        pass
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def _to_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
