"""Gerenciador genérico de serviço do SO para processos Bauer.

Usado por ``bauer daemon service`` e ``bauer runtime service`` (e já usado
indiretamente pelo gateway via gateway_service.py — que foi o protótipo desta
abstração).

Plataformas:
- **Linux**: systemd user unit (``~/.config/systemd/user/<name>.service``)
  com ``Restart=always`` + ``loginctl enable-linger``.
- **macOS**: LaunchAgent plist (``~/Library/LaunchAgents/com.bauer.<name>.plist``)
  com ``KeepAlive=true`` + ``RunAtLoad=true`` — sobe no logon, reinicia em crash.
- **Windows**: Tarefa Agendada (Task Scheduler) com LogonTrigger +
  RestartOnFailure via pythonw (sem janela de console).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("bauer.process_service")


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProcessServiceConfig:
    """Tudo que difere entre daemon, runtime e outros processos gerenciados."""

    service_name: str
    """Nome do unit systemd — ex: ``bauer-daemon``."""

    task_name: str
    """Nome da Tarefa Agendada no Windows — ex: ``BauerDaemon``."""

    description: str
    """Descrição exibida no systemd/Task Scheduler."""

    entry_args: list[str]
    """Args passados ao Python após ``-m bauer.cli`` — ex: ``["daemon", "_run"]``."""

    log_file: Path
    """Caminho do log relativo ao project_dir — ex: ``Path("logs/daemon.log")``."""

    pid_reader: Callable[[Path], tuple[int | None, float | None, float | None]]
    """Função ``(project_dir) -> (pid, uptime_s, memory_mb)`` do processo vivo."""

    cmdline_keyword: str = ""
    """Palavra que deve aparecer no cmdline do processo para confirmar identidade."""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de plataforma (espelhados do gateway_service.py)
# ─────────────────────────────────────────────────────────────────────────────


def detect_service_platform() -> str:
    """'windows' | 'systemd' | 'launchd' | 'unsupported'."""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        from shutil import which
        if which("systemctl"):
            return "systemd"
    return "unsupported"


def _service_python(project_dir: Path) -> str:
    candidates: list[Path] = []
    if sys.platform == "win32":
        candidates = [
            project_dir / ".venv" / "Scripts" / "pythonw.exe",
            project_dir / ".venv" / "Scripts" / "python.exe",
            Path(sys.executable).with_name("pythonw.exe"),
        ]
    else:
        candidates = [project_dir / ".venv" / "bin" / "python"]
    for c in candidates:
        if c.is_file():
            return str(c)
    return sys.executable


def _launchd_label(service_name: str) -> str:
    """'bauer-daemon' → 'com.bauer.daemon'."""
    return "com.bauer." + service_name.removeprefix("bauer-")


def format_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


# ─────────────────────────────────────────────────────────────────────────────
# Templates systemd / launchd / Task Scheduler
# ─────────────────────────────────────────────────────────────────────────────


def build_systemd_unit(
    python_path: str,
    project_dir: Path,
    cfg: ProcessServiceConfig,
) -> str:
    args_str = " ".join(cfg.entry_args)
    env_line = f"EnvironmentFile=-{project_dir / '.env'}\n"
    return f"""[Unit]
Description={cfg.description}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart={python_path} -m bauer.cli {args_str}
WorkingDirectory={project_dir}
{env_line}Restart=always
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def build_launchd_plist(
    python_path: str,
    project_dir: Path,
    cfg: ProcessServiceConfig,
) -> str:
    """Plist de LaunchAgent para macOS.

    - ``KeepAlive=true``: reinicia automaticamente se o processo sair.
    - ``RunAtLoad=true``: inicia imediatamente ao fazer ``launchctl load``.
    - Stdout/Stderr vão para o log file (launchd não tem journald).
    - O ``.env`` é lido pelo ``env_loader.py`` via WorkingDirectory — sem
      precisar embutir segredos no plist.
    """
    label = _launchd_label(cfg.service_name)
    log_path = str(project_dir / cfg.log_file)
    prog_args = "\n    ".join(
        f"<string>{a}</string>"
        for a in [python_path, "-m", "bauer.cli", *cfg.entry_args]
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
    {prog_args}
    </array>
    <key>WorkingDirectory</key>
    <string>{project_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""


def build_windows_task_xml(
    python_path: str,
    project_dir: Path,
    user_id: str,
    cfg: ProcessServiceConfig,
) -> str:
    """``LogonType=S4U`` (não ``InteractiveToken``): batch-logon independente de
    sessão interativa — ver docstring irmã em gateway_service.build_windows_task_xml.
    """
    args_str = "-m bauer.cli " + " ".join(cfg.entry_args)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{cfg.description}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user_id}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user_id}</UserId>
      <LogonType>S4U</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_path}</Command>
      <Arguments>{args_str}</Arguments>
      <WorkingDirectory>{project_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


# ─────────────────────────────────────────────────────────────────────────────
# PID readers reutilizáveis
# ─────────────────────────────────────────────────────────────────────────────


def pid_reader_from_file(
    pid_file_fn: Callable[[Path], Path],
    keyword: str = "",
) -> Callable[[Path], tuple[int | None, float | None, float | None]]:
    """Retorna um pid_reader que lê o PID de um arquivo."""
    def _read(project_dir: Path) -> tuple[int | None, float | None, float | None]:
        try:
            pid = int(pid_file_fn(project_dir).read_text(encoding="utf-8").strip())
        except Exception:
            return None, None, None
        return _inspect_pid(pid, keyword)
    return _read


def pid_reader_from_supervisor_json(
    workspace_fn: Callable[[Path], Path],
) -> Callable[[Path], tuple[int | None, float | None, float | None]]:
    """Retorna um pid_reader que lê o PID de supervisor.json."""
    def _read(project_dir: Path) -> tuple[int | None, float | None, float | None]:
        try:
            import json
            sj = workspace_fn(project_dir) / ".bauer_runtime" / "supervisor.json"
            data = json.loads(sj.read_text(encoding="utf-8"))
            pid = int(data.get("supervisor_pid") or 0)
            if not pid:
                return None, None, None
        except Exception:
            return None, None, None
        return _inspect_pid(pid, "supervisor")
    return _read


def _inspect_pid(
    pid: int, keyword: str
) -> tuple[int | None, float | None, float | None]:
    import time
    try:
        import psutil
        proc = psutil.Process(pid)
        if keyword:
            cmdline = " ".join(proc.cmdline()).lower()
            if keyword not in cmdline and "bauer" not in cmdline:
                return None, None, None
        uptime = time.time() - proc.create_time()
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        return pid, uptime, rss_mb
    except Exception:
        return None, None, None


# ─────────────────────────────────────────────────────────────────────────────
# Status
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ServiceStatus:
    platform: str
    installed: bool = False
    enabled: bool = False
    running: bool = False
    pid: int | None = None
    uptime_s: float | None = None
    memory_mb: float | None = None
    detail: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Manager
# ─────────────────────────────────────────────────────────────────────────────


class ProcessServiceManager:
    """Instala/gerencia um processo Bauer como serviço do SO.

    ``run_fn`` é injetável para testes.
    """

    def __init__(
        self,
        cfg: ProcessServiceConfig,
        project_dir: str | Path | None = None,
        platform: str | None = None,
        run_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self.project_dir = Path(project_dir or Path.cwd()).resolve()
        self.platform = platform or detect_service_platform()
        self._run = run_fn or self._default_run

    @staticmethod
    def _default_run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=60, **kw
        )

    def _check_platform(self) -> None:
        if self.platform == "unsupported":
            raise RuntimeError(
                "Plataforma sem gerenciador de serviço suportado. "
                "Use o comando de start/stop direto."
            )

    @property
    def _unit_path(self) -> Path:
        return (
            Path.home()
            / ".config"
            / "systemd"
            / "user"
            / f"{self.cfg.service_name}.service"
        )

    @property
    def _plist_path(self) -> Path:
        label = _launchd_label(self.cfg.service_name)
        return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    @property
    def _launchd_label(self) -> str:
        return _launchd_label(self.cfg.service_name)

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["systemctl", "--user", *args])

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["launchctl", *args])

    # ── API ─────────────────────────────────────────────────────────────────

    def install(self) -> str:
        self._check_platform()
        python_path = _service_python(self.project_dir)
        (self.project_dir / "logs").mkdir(exist_ok=True)

        if self.platform == "systemd":
            unit = build_systemd_unit(python_path, self.project_dir, self.cfg)
            self._unit_path.parent.mkdir(parents=True, exist_ok=True)
            self._unit_path.write_text(unit, encoding="utf-8")
            self._systemctl("daemon-reload")
            r = self._systemctl("enable", "--now", self.cfg.service_name)
            if r.returncode != 0:
                raise RuntimeError(f"systemctl enable falhou: {r.stderr.strip()}")
            user = os.environ.get("USER", "")
            lr = self._run(["loginctl", "enable-linger", user])
            linger_note = (
                " Linger habilitado (sobrevive a logout)."
                if lr.returncode == 0
                else " ⚠ Não consegui habilitar linger — rode: sudo loginctl enable-linger $USER"
            )
            return (
                f"Serviço {self.cfg.service_name} instalado e iniciado "
                f"({self._unit_path}).{linger_note}"
            )

        if self.platform == "launchd":
            plist = build_launchd_plist(python_path, self.project_dir, self.cfg)
            self._plist_path.parent.mkdir(parents=True, exist_ok=True)
            (self.project_dir / self.cfg.log_file).parent.mkdir(parents=True, exist_ok=True)
            self._plist_path.write_text(plist, encoding="utf-8")
            # Unload first if already registered (idempotente)
            self._launchctl("unload", str(self._plist_path))
            r = self._launchctl("load", "-w", str(self._plist_path))
            if r.returncode != 0:
                raise RuntimeError(f"launchctl load falhou: {(r.stderr or r.stdout).strip()}")
            return (
                f"LaunchAgent '{self._launchd_label}' instalado e iniciado "
                f"({self._plist_path}). Sobe automaticamente no logon; reinicia em crash."
            )

        domain = os.environ.get("USERDOMAIN", "")
        username = os.environ.get("USERNAME", "")
        user_id = f"{domain}\\{username}" if domain else username
        xml = build_windows_task_xml(python_path, self.project_dir, user_id, self.cfg)
        xml_path = self.project_dir / "logs" / f"{self.cfg.task_name}-task.xml"
        xml_path.write_text(xml, encoding="utf-16")
        r = self._run(["schtasks", "/Create", "/TN", self.cfg.task_name, "/XML", str(xml_path), "/F"])
        if r.returncode != 0:
            raise RuntimeError(f"schtasks /Create falhou: {(r.stderr or r.stdout).strip()}")
        self._run(["schtasks", "/Run", "/TN", self.cfg.task_name])
        return (
            f"Tarefa '{self.cfg.task_name}' instalada e iniciada (Task Scheduler). "
            f"Sobe automaticamente no logon de {user_id}; reinicia em crash."
        )

    def uninstall(self) -> str:
        self._check_platform()
        self.stop(quiet=True)
        if self.platform == "systemd":
            self._systemctl("disable", self.cfg.service_name)
            self._unit_path.unlink(missing_ok=True)
            self._systemctl("daemon-reload")
            return f"Serviço {self.cfg.service_name} removido."
        if self.platform == "launchd":
            self._launchctl("unload", "-w", str(self._plist_path))
            self._plist_path.unlink(missing_ok=True)
            return f"LaunchAgent '{self._launchd_label}' removido."
        r = self._run(["schtasks", "/Delete", "/TN", self.cfg.task_name, "/F"])
        if r.returncode != 0:
            raise RuntimeError(f"schtasks /Delete falhou: {(r.stderr or r.stdout).strip()}")
        return f"Tarefa '{self.cfg.task_name}' removida."

    def start(self) -> str:
        self._check_platform()
        if self.platform == "systemd":
            r = self._systemctl("start", self.cfg.service_name)
            if r.returncode != 0:
                raise RuntimeError(f"systemctl start falhou: {r.stderr.strip()}")
            return f"Serviço {self.cfg.service_name} iniciado."
        if self.platform == "launchd":
            if not self._plist_path.exists():
                raise RuntimeError(
                    "LaunchAgent não instalado — rode 'service install' primeiro."
                )
            r = self._launchctl("load", "-w", str(self._plist_path))
            if r.returncode != 0:
                raise RuntimeError(f"launchctl load falhou: {(r.stderr or r.stdout).strip()}")
            return f"LaunchAgent '{self._launchd_label}' iniciado."
        r = self._run(["schtasks", "/Run", "/TN", self.cfg.task_name])
        if r.returncode != 0:
            raise RuntimeError(
                f"schtasks /Run falhou: {(r.stderr or r.stdout).strip()} "
                f"— o serviço está instalado? Use 'service install' primeiro."
            )
        return f"Tarefa '{self.cfg.task_name}' iniciada."

    def stop(self, quiet: bool = False) -> str:
        self._check_platform()
        if self.platform == "systemd":
            self._systemctl("stop", self.cfg.service_name)
            return f"Serviço {self.cfg.service_name} parado."
        if self.platform == "launchd":
            self._launchctl("unload", str(self._plist_path))
            # Garante kill via PID se launchctl não matou
            pid, _, _ = self.cfg.pid_reader(self.project_dir)
            if pid is not None:
                try:
                    import psutil
                    psutil.Process(pid).terminate()
                except Exception:
                    pass
            if not quiet:
                return f"LaunchAgent '{self._launchd_label}' parado."
            return ""
        self._run(["schtasks", "/End", "/TN", self.cfg.task_name])
        pid, _, _ = self.cfg.pid_reader(self.project_dir)
        if pid is not None:
            try:
                import psutil
                psutil.Process(pid).terminate()
            except Exception:
                pass
        if not quiet:
            return f"Tarefa '{self.cfg.task_name}' parada."
        return ""

    def status(self) -> ServiceStatus:
        st = ServiceStatus(platform=self.platform)
        if self.platform == "unsupported":
            st.detail = "plataforma sem systemd/launchd/Task Scheduler"
            return st

        if self.platform == "systemd":
            st.installed = self._unit_path.exists()
            if st.installed:
                en = self._systemctl("is-enabled", self.cfg.service_name)
                st.enabled = en.stdout.strip() == "enabled"
                ac = self._systemctl("is-active", self.cfg.service_name)
                st.running = ac.stdout.strip() == "active"
        elif self.platform == "launchd":
            st.installed = self._plist_path.exists()
            if st.installed:
                st.enabled = True
                r = self._launchctl("list", self._launchd_label)
                st.running = r.returncode == 0 and '"PID"' in r.stdout
        else:
            q = self._run(["schtasks", "/Query", "/TN", self.cfg.task_name, "/FO", "LIST"])
            st.installed = q.returncode == 0
            if st.installed:
                st.enabled = True
                st.running = "running" in q.stdout.lower() or "em execu" in q.stdout.lower()

        pid, uptime, mem = self.cfg.pid_reader(self.project_dir)
        if pid is not None:
            st.running = True
            st.pid, st.uptime_s, st.memory_mb = pid, uptime, mem
        elif st.running:
            st.detail = "gerenciador reporta ativo; PID ainda não confirma"
        return st

    def logs(self, lines: int = 50) -> str:
        if self.platform == "systemd":
            r = self._run([
                "journalctl", "--user", "-u", self.cfg.service_name,
                "-n", str(lines), "--no-pager",
            ])
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        # launchd e Windows: log vai para arquivo
        log_path = self.project_dir / self.cfg.log_file
        if not log_path.exists():
            return f"(sem logs ainda — esperado em {log_path})"
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(content.splitlines()[-lines:])
        except OSError as exc:
            return f"(falha lendo {log_path}: {exc})"
