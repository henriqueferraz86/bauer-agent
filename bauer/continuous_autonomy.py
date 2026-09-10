"""Autonomia contínua segura e orientada a alvos.

O módulo observa endpoints HTTP, processos e containers Docker declarados ou
descobertos no bootstrap do servidor. O retrato fica no mesmo
``RuntimeStateStore`` usado pelo Kernel e os eventos no mesmo ``EventBus``.
Não há descoberta de rede ampla nem governança alternativa aqui.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger("bauer.continuous_autonomy")

CONTINUOUS_STATES = {"off", "starting", "running", "stopping", "stopped", "error"}
IMPORTANT_ALERTS = {"incident", "approval_required", "action_completed", "budget_limit"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def discover_docker_containers(
    *, runner: Callable[..., Any] | None = None,
) -> list[dict[str, str]] | None:
    """Retorna os containers conhecidos pelo Docker, ou ``None`` se indisponível.

    ``None`` é diferente de uma lista vazia: quando o daemon não responde,
    não removemos alvos descobertos anteriormente do cadastro persistido.
    """
    run = runner or subprocess.run
    try:
        result = run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    containers: list[dict[str, str]] = []
    for line in (result.stdout or "").splitlines():
        try:
            record = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        name = str(record.get("Names") or record.get("Name") or "").strip()
        if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
            continue
        containers.append({
            "name": name,
            "id": str(record.get("ID") or "").strip(),
            "image": str(record.get("Image") or "").strip(),
            "state": str(record.get("State") or "").strip(),
        })
    return containers


def sync_discovered_docker_targets(
    config_path: str | Path,
    *,
    runner: Callable[..., Any] | None = None,
) -> bool:
    """Cadastra containers do host como alvos pausados e autocorrigíveis.

    Apenas alvos marcados como ``auto_discovered`` podem ser removidos quando
    um container deixa de existir. Alvos escritos pelo usuário são preservados
    e containers já ativados continuam ativados após um novo boot.
    """
    discovered = discover_docker_containers(runner=runner)
    path = Path(config_path)
    if discovered is None or not path.exists():
        return False

    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(raw, dict):
        raw = {}
    section = raw.setdefault("continuous_autonomy", {})
    if not isinstance(section, dict):
        section = {}
        raw["continuous_autonomy"] = section
    targets = section.setdefault("targets", [])
    if not isinstance(targets, list):
        return False

    by_name = {
        item.get("container_name"): item
        for item in targets
        if isinstance(item, dict) and item.get("container_name")
    }
    names = {item["name"] for item in discovered}
    changed = False
    kept: list[Any] = []
    for item in targets:
        if isinstance(item, dict) and item.get("auto_discovered") and item.get("container_name") not in names:
            changed = True
            continue
        kept.append(item)
    targets[:] = kept

    interval_s = float(section.get("check_interval_s", 60.0) or 60.0)
    for item in discovered:
        if item["name"] in by_name and by_name[item["name"]] in targets:
            existing = by_name[item["name"]]
            if existing.get("auto_discovered") and existing.get("name") != f"Container {item['name']}":
                existing["name"] = f"Container {item['name']}"
                changed = True
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-") or "container"
        candidate = {
            "id": f"docker-{slug}"[:100].rstrip("-"),
            "name": f"Container {item['name']}",
            "type": "docker_container",
            "url": "",
            "interval_s": interval_s,
            "timeout_s": 5.0,
            "expected_status": 200,
            "enabled": False,
            "auto_recover": True,
            "recovery_action": "docker_recover",
            "container_name": item["name"],
            "max_recovery_attempts": 2,
            "recovery_cooldown_s": 60.0,
            "auto_discovered": True,
        }
        targets.append(candidate)
        by_name[item["name"]] = candidate
        changed = True

    if not changed:
        return False
    try:
        path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


@dataclass(frozen=True)
class HealthTarget:
    id: str
    name: str
    url: str
    interval_s: float = 60.0
    timeout_s: float = 5.0
    expected_status: int = 200
    enabled: bool = True
    type: str = "http_health"
    auto_recover: bool = False
    recovery_action: str | None = None
    container_name: str | None = None
    process_name: str | None = None
    process_command: list[str] = field(default_factory=list)
    max_recovery_attempts: int = 2
    recovery_cooldown_s: float = 60.0
    auto_discovered: bool = False

    def validate(self) -> None:
        if not self.id.strip() or not self.name.strip():
            raise ValueError("alvo precisa de id e name")
        if self.type not in {"http_health", "docker_container", "process"}:
            raise ValueError(f"tipo de alvo inválido: {self.type}")
        if self.type == "http_health" and not self.url.startswith(("http://", "https://")):
            raise ValueError("alvo HTTP precisa usar http:// ou https://")
        if self.type == "docker_container" and self.url and not self.url.startswith(("http://", "https://")):
            raise ValueError("url do container precisa usar http:// ou https://")
        if self.type == "docker_container":
            if not self.container_name or not re.fullmatch(r"[A-Za-z0-9_.-]+", self.container_name):
                raise ValueError("container_name inválido")
            if self.auto_recover and self.recovery_action != "docker_recover":
                raise ValueError("docker_container exige recovery_action=docker_recover")
        if self.type == "process":
            if not self.process_name or not re.fullmatch(r"[A-Za-z0-9_.-]+", self.process_name):
                raise ValueError("process_name inválido")
            if not self.process_command or any(not isinstance(arg, str) or not arg for arg in self.process_command):
                raise ValueError("process_command precisa ser uma lista de argumentos")
            if self.auto_recover and self.recovery_action != "process_restart":
                raise ValueError("process exige recovery_action=process_restart")
        if self.auto_recover and self.type == "http_health":
            raise ValueError("http_health não possui correção automática configurada")
        if self.max_recovery_attempts < 0 or self.recovery_cooldown_s <= 0:
            raise ValueError("limites de recuperação inválidos")
        if self.interval_s <= 0 or self.timeout_s <= 0:
            raise ValueError("interval_s e timeout_s devem ser positivos")
        if not 100 <= self.expected_status <= 599:
            raise ValueError("expected_status deve estar entre 100 e 599")


@dataclass
class TargetStatus:
    id: str
    name: str
    url: str
    enabled: bool = True
    type: str = "http_health"
    auto_recover: bool = False
    recovery_action: str | None = None
    recovery_attempts: int = 0
    last_recovery_at: str | None = None
    last_diagnosis: str | None = None
    state: str = "unknown"
    status_code: int | None = None
    latency_ms: float | None = None
    last_checked_at: str | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    auto_discovered: bool = False


@dataclass
class ContinuousState:
    id: str = "controller"
    state: str = "off"
    message: str = "Autonomia contínua desligada."
    last_check_at: str | None = None
    incidents: int = 0
    recommendations: int = 0
    alert_level: str = "important"
    voice_enabled: bool = False
    started_at: str | None = None
    stopped_at: str | None = None
    error: str | None = None
    owner_pid: int | None = None
    updated_at: str = field(default_factory=_now)


@dataclass
class DelegationRecord:
    id: str
    kind: str
    message: str
    run_id: str
    workspace: str
    branch: str | None = None
    isolation: str = "worktree"
    status: str = "running"
    created_at: str = field(default_factory=_now)


class ContinuousAutonomy:
    """Controlador thread-safe para o botão/CLI/server de autonomia.

    ``config`` contém os alvos persistidos. A descoberta de containers é
    feita explicitamente pelo bootstrap do servidor, nunca durante uma
    sondagem ou uma chamada de rede.
    """

    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        config: Any | None = None,
        store: Any | None = None,
        event_bus: Any | None = None,
        probe: Callable[[HealthTarget], tuple[bool, int | None, float | None, str | None]] | None = None,
        alert_callback: Callable[[str, str], None] | None = None,
    ) -> None:
        from .core.events import EventBus
        from .core.runtime.state_store import SqliteStateStore

        self.root = Path(root)
        self.store = store or SqliteStateStore(self.root)
        self.event_bus = event_bus or EventBus(store=self.store)
        self.config = config
        self._probe = probe or self._http_probe
        self._alert_callback = alert_callback
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._recovery_attempts: dict[str, int] = {}
        self._last_recovery_monotonic: dict[str, float] = {}
        self._targets = self._load_targets(config)
        persisted = self.store.latest("continuous_autonomy", "controller")
        self._state = ContinuousState(**persisted) if persisted else ContinuousState(
            alert_level=self.alert_level, voice_enabled=self.voice_enabled,
        )
        if self._state.state in {"starting", "running", "stopping"} and not self._pid_alive(self._state.owner_pid):
            # Threads são deliberadamente in-process. Depois de um restart não
            # alegue que há observação viva sem um worker que possa ser parado.
            self._state.state = "stopped"
            self._state.message = "Worker anterior não está ativo; reinicie a observação."
            self._state.stopped_at = _now()
            self._state.owner_pid = None
            self._persist()
        self._target_status: dict[str, TargetStatus] = {
            t.id: TargetStatus(t.id, t.name, t.url, t.enabled, t.type,
                               t.auto_recover, t.recovery_action,
                               auto_discovered=t.auto_discovered)
            for t in self._targets
        }
        for target in self._targets:
            if not target.enabled:
                self._target_status[target.id].state = "paused"

    @property
    def alert_level(self) -> str:
        return str(getattr(self.config, "alert_level", "important") or "important")

    @property
    def voice_enabled(self) -> bool:
        return bool(getattr(self.config, "voice_enabled", False))

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid or pid == os.getpid():
            return pid == os.getpid()
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    @staticmethod
    def _load_targets(config: Any | None) -> list[HealthTarget]:
        raw = getattr(config, "targets", []) if config is not None else []
        targets: list[HealthTarget] = []
        for item in raw or []:
            if isinstance(item, HealthTarget):
                target = item
            elif isinstance(item, dict):
                target = HealthTarget(**item)
            else:
                target = HealthTarget(
                    id=str(getattr(item, "id")), name=str(getattr(item, "name")),
                    url=str(getattr(item, "url")),
                    interval_s=float(getattr(item, "interval_s", 60.0)),
                    timeout_s=float(getattr(item, "timeout_s", 5.0)),
                    expected_status=int(getattr(item, "expected_status", 200)),
                    enabled=bool(getattr(item, "enabled", True)),
                    type=str(getattr(item, "type", "http_health")),
                    auto_recover=bool(getattr(item, "auto_recover", False)),
                    recovery_action=getattr(item, "recovery_action", None),
                    container_name=getattr(item, "container_name", None),
                    process_name=getattr(item, "process_name", None),
                    process_command=list(getattr(item, "process_command", []) or []),
                    max_recovery_attempts=int(getattr(item, "max_recovery_attempts", 2)),
                    recovery_cooldown_s=float(getattr(item, "recovery_cooldown_s", 60.0)),
                    auto_discovered=bool(getattr(item, "auto_discovered", False)),
                )
            target.validate()
            targets.append(target)
        return targets

    def _persist(self) -> None:
        self._state.updated_at = _now()
        self.store.upsert("continuous_autonomy", asdict(self._state))

    def status(self) -> dict[str, Any]:
        self._refresh_delegations()
        with self._lock:
            persisted_state = self.store.latest("continuous_autonomy", "controller")
            if persisted_state and persisted_state.get("updated_at", "") >= self._state.updated_at:
                self._state = ContinuousState(**persisted_state)
            for target in self._targets:
                persisted_target = self.store.latest("continuous_target_status", target.id)
                if persisted_target:
                    restored = TargetStatus(**persisted_target)
                    if not target.enabled:
                        restored.enabled = False
                        restored.state = "paused"
                    self._target_status[target.id] = restored
            return {
                "state": asdict(self._state),
                "targets": [asdict(self._target_status[t.id]) for t in self._targets],
                "configured_targets": len(self._targets),
                "incidents": self._latest("continuous_incidents", 50),
                "recommendations": self._latest("continuous_recommendations", 50),
                "delegations": self._latest("continuous_delegations", 50),
            }

    def reload_config(self, config: Any) -> dict[str, Any]:
        """Atualiza alvos/configuração sem perder o estado das sondagens."""
        targets = self._load_targets(config)
        with self._lock:
            previous = self._target_status
            self.config = config
            self._targets = targets
            self._target_status = {}
            for target in targets:
                current = previous.get(target.id)
                if current is None:
                    current = TargetStatus(target.id, target.name, target.url, target.enabled,
                                           target.type, target.auto_recover, target.recovery_action,
                                           auto_discovered=target.auto_discovered)
                else:
                    current.name = target.name
                    current.url = target.url
                    current.enabled = target.enabled
                    current.type = target.type
                    current.auto_recover = target.auto_recover
                    current.recovery_action = target.recovery_action
                    current.auto_discovered = target.auto_discovered
                if not target.enabled:
                    current.state = "paused"
                elif current.state == "paused":
                    current.state = "unknown"
                self._target_status[target.id] = current
                persisted_target = self.store.latest("continuous_target_status", target.id)
                if persisted_target and target.enabled:
                    self._target_status[target.id] = TargetStatus(**persisted_target)
            self._state.alert_level = self.alert_level
            self._state.voice_enabled = self.voice_enabled
            self._persist()
        return self.status()

    def start(self, target_ids: list[str] | None = None) -> dict[str, Any]:
        if self.config is not None and not bool(getattr(self.config, "enabled", False)):
            raise ValueError("autonomia contínua está desabilitada na configuração")
        selected = [t for t in self._targets if t.enabled and (not target_ids or t.id in target_ids)]
        if not selected:
            with self._lock:
                self._state = ContinuousState(
                    state="off", message="Nenhum alvo HTTP configurado; nada foi sondado.",
                    alert_level=self.alert_level, voice_enabled=self.voice_enabled,
                    error="no_configured_targets",
                )
                self._persist()
            raise ValueError("nenhum alvo HTTP configurado")
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.status()
            if self._state.state in {"starting", "running", "stopping"} and self._pid_alive(self._state.owner_pid):
                raise ValueError(f"supervisor já está ativo (pid {self._state.owner_pid})")
            self._stop.clear()
            self.store.upsert("continuous_control", {"id": "controller", "stop_requested": False})
            self._state.state = "starting"
            self._state.message = "Iniciando verificações dos alvos configurados."
            self._state.error = None
            self._state.started_at = _now()
            self._state.owner_pid = os.getpid()
            self._state.alert_level = self.alert_level
            self._state.voice_enabled = self.voice_enabled
            self._persist()
            self._publish("autonomy.state.changed", "starting", self._state.message)
            selected_ids = {target.id for target in selected} if target_ids else None
            self._thread = threading.Thread(target=self._run, args=(selected_ids,),
                                             name="bauer-autonomy", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.store.upsert("continuous_control", {"id": "controller", "stop_requested": True})
            self._stop.set()
            if self._state.state in {"starting", "running"}:
                self._state.state = "stopping"
                self._state.message = "Parada solicitada; nenhuma nova verificação será iniciada."
                self._persist()
                self._publish("autonomy.state.changed", "stopping", self._state.message)
        return self.status()

    def set_alerts(self, *, voice_enabled: bool | None = None, alert_level: str | None = None) -> dict[str, Any]:
        if alert_level is not None and alert_level not in {"off", "important", "all"}:
            raise ValueError("alert_level deve ser off, important ou all")
        with self._lock:
            if voice_enabled is not None:
                self._state.voice_enabled = bool(voice_enabled)
            if alert_level is not None:
                self._state.alert_level = alert_level
            self._persist()
        return self.status()

    def _run(self, selected_ids: set[str] | None) -> None:
        with self._lock:
            self._state.state = "running"
            self._state.message = "Observando apenas os alvos configurados."
            self._persist()
            self._publish("autonomy.started", "running", self._state.message)
        next_check: dict[str, float] = {}
        try:
            while not self._stop.is_set() and not self._stop_requested():
                now = time.monotonic()
                did_check = False
                for target in list(self._targets):
                    if not target.enabled or (selected_ids is not None and target.id not in selected_ids):
                        continue
                    if now < next_check.get(target.id, 0.0):
                        continue
                    self._check(target)
                    next_check[target.id] = now + target.interval_s
                    did_check = True
                    if self._stop.is_set() or self._stop_requested():
                        break
                if not did_check:
                    self._stop.wait(0.25)
        except Exception as exc:  # supervisor remains auditable, never silent
            logger.exception("autonomia contínua falhou")
            with self._lock:
                self._state.state = "error"
                self._state.error = str(exc)
                self._state.message = "A supervisão parou por erro; verifique a auditoria."
                self._persist()
                self._publish("autonomy.failed", "error", str(exc))
        finally:
            with self._lock:
                if self._state.state != "error":
                    self._state.state = "stopped"
                    self._state.stopped_at = _now()
                    self._state.message = "Autonomia contínua parada."
                    self._state.owner_pid = None
                    self._persist()
                    self._publish("autonomy.stopped", "stopped", self._state.message)

    def _stop_requested(self) -> bool:
        record = self.store.latest("continuous_control", "controller")
        return bool(record and record.get("stop_requested"))

    def _check(self, target: HealthTarget) -> None:
        ok, status_code, latency_ms, error = self._probe(target)
        previous = self._target_status[target.id]
        was_failed = previous.state == "failed"
        previous.state = "healthy" if ok else "failed"
        previous.status_code = status_code
        previous.latency_ms = latency_ms
        previous.last_checked_at = _now()
        previous.last_error = error
        previous.consecutive_failures = 0 if ok else previous.consecutive_failures + 1
        if ok:
            self._recovery_attempts.pop(target.id, None)
            self._last_recovery_monotonic.pop(target.id, None)
        with self._lock:
            self._state.last_check_at = previous.last_checked_at
            self._persist()
            self.store.upsert("continuous_target_status", asdict(previous))
        self._publish("autonomy.check.completed", previous.state, f"{target.name}: {previous.state}")
        if not ok and not was_failed:
            self._record_incident(target, previous)
        elif not ok:
            self._record_recommendation(target, previous)

    @staticmethod
    def _http_probe(target: HealthTarget) -> tuple[bool, int | None, float | None, str | None]:
        if target.type == "process":
            return ContinuousAutonomy._process_probe(target)
        if target.type == "docker_container":
            return ContinuousAutonomy._docker_probe(target)
        import httpx

        started = time.perf_counter()
        try:
            response = httpx.get(target.url, timeout=target.timeout_s)
            latency = round((time.perf_counter() - started) * 1000.0, 2)
            ok = response.status_code == target.expected_status
            return ok, response.status_code, latency, None if ok else (
                f"HTTP {response.status_code}; esperado {target.expected_status}"
            )
        except Exception as exc:  # network failure is an incident, not a crash
            return False, None, round((time.perf_counter() - started) * 1000.0, 2), str(exc)

    @staticmethod
    def _docker_probe(target: HealthTarget) -> tuple[bool, int | None, float | None, str | None]:
        started = time.perf_counter()
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Running}}", str(target.container_name)],
                capture_output=True, text=True, timeout=target.timeout_s, check=False,
            )
            running = result.returncode == 0 and (result.stdout or "").strip().lower() == "true"
            latency = round((time.perf_counter() - started) * 1000.0, 2)
            return running, 200 if running else None, latency, None if running else (
                f"container {target.container_name} não está em execução"
            )
        except Exception as exc:
            return False, None, round((time.perf_counter() - started) * 1000.0, 2), str(exc)

    @staticmethod
    def _process_probe(target: HealthTarget) -> tuple[bool, int | None, float | None, str | None]:
        started = time.perf_counter()
        try:
            if os.name == "nt":
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {target.process_name}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=target.timeout_s, check=False,
                )
                healthy = bool(result.stdout.strip()) and "INFO:" not in result.stdout
            else:
                result = subprocess.run(
                    ["pgrep", "-x", str(target.process_name)],
                    capture_output=True, text=True, timeout=target.timeout_s, check=False,
                )
                healthy = result.returncode == 0
            latency = round((time.perf_counter() - started) * 1000.0, 2)
            return healthy, 200 if healthy else None, latency, None if healthy else (
                f"processo {target.process_name} não está em execução"
            )
        except Exception as exc:
            return False, None, round((time.perf_counter() - started) * 1000.0, 2), str(exc)

    @staticmethod
    def _run_diagnostic(command: list[str], timeout: float = 15.0) -> str:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
            output = (result.stdout or result.stderr or "").strip()
            return output[-4000:] if output else f"exit code {result.returncode}"
        except Exception as exc:
            return str(exc)

    def _diagnose(self, target: HealthTarget, status: TargetStatus) -> dict[str, Any]:
        diagnosis: dict[str, Any] = {
            "health_error": status.last_error,
            "status_code": status.status_code,
            "consecutive_failures": status.consecutive_failures,
        }
        if target.type == "docker_container" and target.container_name:
            diagnosis["container_state"] = self._run_diagnostic([
                "docker", "inspect", "--format", "{{json .State}}", target.container_name,
            ])
            diagnosis["recent_logs"] = self._run_diagnostic([
                "docker", "logs", "--tail", "40", target.container_name,
            ])
        elif target.type == "process" and target.process_name:
            diagnosis["process"] = self._run_diagnostic(
                ["tasklist", "/FI", f"IMAGENAME eq {target.process_name}"]
                if os.name == "nt" else ["pgrep", "-a", "-x", target.process_name]
            )
        self._publish("autonomy.diagnosis", "failed", f"Diagnóstico de {target.name}", data=diagnosis)
        return diagnosis

    def _attempt_recovery(self, target: HealthTarget, status: TargetStatus) -> dict[str, Any] | None:
        if not target.auto_recover or not target.recovery_action:
            return None
        allowed = getattr(self.config, "allowlisted_actions", []) or []
        if target.recovery_action not in allowed:
            return {"success": False, "action": target.recovery_action,
                    "reason": "ação não está na allowlist de autonomia"}
        now = time.monotonic()
        if self._recovery_attempts.get(target.id, 0) >= target.max_recovery_attempts:
            return {"success": False, "action": target.recovery_action, "reason": "limite de tentativas atingido"}
        if now - self._last_recovery_monotonic.get(target.id, 0.0) < target.recovery_cooldown_s:
            return {"success": False, "action": target.recovery_action, "reason": "aguardando cooldown"}
        self._recovery_attempts[target.id] = self._recovery_attempts.get(target.id, 0) + 1
        self._last_recovery_monotonic[target.id] = now
        attempt = self._recovery_attempts[target.id]
        self._publish("autonomy.recovery.attempted", "running",
                      f"Recuperação automática de {target.name} (tentativa {attempt})")
        command: list[str]
        if target.recovery_action == "docker_recover" and target.container_name:
            running = self._run_diagnostic([
                "docker", "inspect", "--format", "{{.State.Running}}", target.container_name,
            ]).lower() == "true"
            command = ["docker", "restart" if running else "start", target.container_name]
        elif target.recovery_action == "process_restart" and target.process_name:
            if os.name == "nt":
                subprocess.run(["taskkill", "/IM", target.process_name, "/T", "/F"],
                               capture_output=True, text=True, timeout=15, check=False)
            else:
                subprocess.run(["pkill", "-TERM", "-x", target.process_name],
                               capture_output=True, text=True, timeout=15, check=False)
            command = target.process_command
        else:
            return {"success": False, "action": target.recovery_action, "reason": "receita incompleta"}
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
            output = (result.stdout or result.stderr or "").strip()[-1000:]
            if result.returncode != 0:
                recovery = {"success": False, "action": target.recovery_action,
                            "attempt": attempt, "reason": output or f"exit code {result.returncode}"}
                self._publish("autonomy.recovery.failed", "failed", f"Falha ao recuperar {target.name}", data=recovery)
                return recovery
            for _ in range(3):
                time.sleep(1.0)
                if self._probe(target)[0]:
                    status.state = "healthy"
                    status.last_error = None
                    status.consecutive_failures = 0
                    status.last_recovery_at = _now()
                    status.recovery_attempts = attempt
                    self.store.upsert("continuous_target_status", asdict(status))
                    recovery = {"success": True, "action": target.recovery_action,
                                "attempt": attempt, "command": command}
                    self._publish("autonomy.recovery.completed", "healthy",
                                  f"{target.name} recuperado automaticamente", data=recovery)
                    self._alert("action_completed", f"{target.name} foi recuperado automaticamente e voltou a responder.")
                    return recovery
            recovery = {"success": False, "action": target.recovery_action, "attempt": attempt,
                        "reason": "comando executado, mas health check continuou falhando"}
            self._publish("autonomy.recovery.failed", "failed", f"{target.name} não voltou após recuperação", data=recovery)
            return recovery
        except Exception as exc:
            recovery = {"success": False, "action": target.recovery_action,
                        "attempt": attempt, "reason": str(exc)}
            self._publish("autonomy.recovery.failed", "failed", f"Falha ao recuperar {target.name}", data=recovery)
            return recovery

    def _record_incident(self, target: HealthTarget, status: TargetStatus) -> None:
        diagnosis = self._diagnose(target, status)
        recovery = self._attempt_recovery(target, status)
        incident = {
            "id": f"incident-{uuid4()}", "target_id": target.id, "target": target.name,
            "status": "open", "message": status.last_error or "health check falhou",
            "created_at": _now(), "last_checked_at": status.last_checked_at,
            "diagnosis": diagnosis,
        }
        if recovery is not None:
            incident["recovery"] = recovery
        self.store.append("continuous_incidents", incident)
        with self._lock:
            self._state.incidents += 1
            self._persist()
        self._publish("autonomy.incident", "incident", incident["message"], data=incident)
        self._alert("incident", f"Falha detectada em {target.name}: {incident['message']}")
        self._record_recommendation(target, status, recovery)

    def _record_recommendation(self, target: HealthTarget, status: TargetStatus,
                               recovery: dict[str, Any] | None = None) -> None:
        recovered = bool(recovery and recovery.get("success"))
        recommendation = {
            "id": f"recommendation-{uuid4()}", "target_id": target.id,
            "message": (f"{target.name} foi recuperado automaticamente. Verifique os logs."
                         if recovered else f"Investigue {target.name}; a recuperação automática não resolveu."),
            "safe_action": recovery.get("action") if recovery else "retry_health_check",
            "requires_approval": not recovered,
            "created_at": _now(), "status": "open",
        }
        self.store.append("continuous_recommendations", recommendation)
        with self._lock:
            self._state.recommendations += 1
            self._persist()
        self._publish("autonomy.recommendation", "recommendation", recommendation["message"], data=recommendation)

    def _alert(self, kind: str, message: str) -> None:
        level = self._state.alert_level or self.alert_level
        if level == "off" or (level == "important" and kind not in IMPORTANT_ALERTS):
            return
        self._publish("autonomy.alert", kind, message)
        if self._state.voice_enabled:
            try:
                from .audio_playback import play_audio_file
                from .tts import synthesize_speech
                result = synthesize_speech(message[:4096])
                if result.get("success"):
                    play_audio_file(result["path"], blocking=False)
                else:
                    logger.warning("TTS indisponível: %s", result.get("error"))
            except Exception as exc:  # voice is best-effort; text event remains
                logger.warning("alerta de voz indisponível: %s", exc)
        if self._alert_callback:
            try:
                self._alert_callback(kind, message)
            except Exception as exc:
                logger.debug("alert callback failed: %s", exc)

    def _publish(self, event_type: str, status: str, message: str,
                 *, data: dict[str, Any] | None = None) -> None:
        try:
            self.event_bus.publish(event_type, status=status, message=message, data=data or {})
        except Exception as exc:
            logger.debug("autonomy event failed: %s", exc)

    def _latest(self, collection: str, limit: int) -> list[dict[str, Any]]:
        return self.store.list(collection)[-limit:]

    def record_delegation(self, record: DelegationRecord) -> DelegationRecord:
        self.store.append("continuous_delegations", asdict(record))
        self._publish("autonomy.delegation.created", "running", record.message,
                      data=asdict(record))
        return record

    def _refresh_delegations(self) -> None:
        try:
            from .core.runtime.run_manager import RunManager
            runs = {run.id: run.status for run in RunManager(root=self.root).list_runs()}
            for raw in self.store.list_latest("continuous_delegations"):
                status = runs.get(raw.get("run_id"))
                if status and status != raw.get("status"):
                    self.store.append("continuous_delegations", {**raw, "status": status})
        except Exception as exc:
            logger.debug("delegation refresh failed: %s", exc)


def create_worktree_for_delegation(workspace: str | Path, delegation_id: str) -> tuple[Path, str]:
    """Cria worktree obrigatório para delegações de aplicação/melhoria."""
    from .task_worktree import create_worktree

    info = create_worktree(workspace, delegation_id)
    if info is None:
        raise ValueError("delegação exige um workspace dentro de um repositório Git")
    return info.path, info.branch
