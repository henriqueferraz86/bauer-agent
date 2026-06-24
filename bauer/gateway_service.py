"""Bauer Gateway como SERVIÇO do sistema — sobrevive a logout/reboot.

Paridade com o Hermes (`hermes-gateway.service`): o gateway não é mais um
processo de terminal que morre com a sessão — vira serviço gerenciado pelo
sistema operacional, com restart automático em crash e logs persistentes.

Plataformas:
- **Linux**: systemd user unit (``~/.config/systemd/user/bauer-gateway.service``)
  com ``Restart=always`` + ``loginctl enable-linger`` (sobrevive a logout) —
  exatamente o modelo do Hermes.
- **macOS**: LaunchAgent plist (``~/Library/LaunchAgents/com.bauer.gateway.plist``)
  com ``KeepAlive=true`` + ``RunAtLoad=true`` — sobe no logon e reinicia em crash.
- **Windows**: Tarefa Agendada (Task Scheduler) com LogonTrigger +
  RestartOnFailure — sobe no logon, reinicia em crash, roda em background
  via ``pythonw`` (sem janela de console).

Uso (CLI)::

    bauer gateway service install     # instala e inicia
    bauer gateway service status     # PID, uptime, memória
    bauer gateway service logs -n 50
    bauer gateway service stop|start
    bauer gateway service uninstall

O serviço executa ``<python> -m bauer.gateway_runtime`` com o diretório do
projeto como working dir (o ``.env`` e o ``config.yaml`` são lidos de lá).
Logs vão para ``logs/gateway.log`` (rotativo) em qualquer plataforma; no
Linux o journald também captura stdout/stderr.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("bauer.gateway_service")

SERVICE_NAME = "bauer-gateway"          # systemd unit
TASK_NAME = "BauerGateway"              # Task Scheduler
SERVICE_DESCRIPTION = "Bauer Gateway - Canais de chat (Telegram/Discord) do Bauer Agent"
LOG_FILE = Path("logs") / "gateway.log"


# ─────────────────────────────────────────────────────────────────────────────
# Detecção de plataforma
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


def _launchd_label() -> str:
    return "com.bauer.gateway"


def _service_python(project_dir: Path) -> str:
    """Python que o serviço vai usar.

    Prefere o pythonw.exe/python do venv onde o bauer está instalado
    (detectado via sys.executable), garantindo que o mesmo venv que
    roda o CLI é usado pelo serviço — independente do working dir.
    """
    exe = Path(sys.executable)
    if sys.platform == "win32":
        # Tenta pythonw.exe (sem janela de console) no mesmo diretório do exe atual
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
        return str(exe)
    return str(exe)


# ─────────────────────────────────────────────────────────────────────────────
# Templates (puros — testáveis sem SO)
# ─────────────────────────────────────────────────────────────────────────────


def build_systemd_unit(python_path: str, project_dir: Path) -> str:
    """Unit file systemd (user) — modelo do hermes-gateway.service.

    ``EnvironmentFile=-`` (com hífen): carrega o .env se existir, sem falhar
    quando ausente — tokens de canal ficam fora do unit file.
    """
    env_line = f"EnvironmentFile=-{project_dir / '.env'}\n"
    return f"""[Unit]
Description={SERVICE_DESCRIPTION}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart={python_path} -m bauer.gateway_runtime
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


def build_windows_task_xml(python_path: str, project_dir: Path, user_id: str) -> str:
    """XML da Tarefa Agendada — LogonTrigger + RestartOnFailure.

    ``ExecutionTimeLimit=PT0S`` desliga o limite de 72h default (o gateway
    roda para sempre). ``RestartOnFailure`` cobre crash do processo;
    ``StartWhenAvailable`` cobre logon perdido.
    """
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{SERVICE_DESCRIPTION}</Description>
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
      <LogonType>InteractiveToken</LogonType>
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
      <Arguments>-m bauer.gateway_runtime</Arguments>
      <WorkingDirectory>{project_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def build_launchd_plist(python_path: str, project_dir: Path) -> str:
    """Plist LaunchAgent (macOS) — KeepAlive + RunAtLoad."""
    label = _launchd_label()
    log_path = str(project_dir / LOG_FILE)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>bauer.gateway_runtime</string>
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


# ─────────────────────────────────────────────────────────────────────────────
# Estado do processo (PID file + psutil)
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


def _pid_file(project_dir: Path) -> Path:
    # workspace default; respeita config se carregável
    workspace = project_dir / "workspace"
    try:
        from .config_loader import load_config
        workspace = Path(load_config(project_dir / "config.yaml").agent.workspace)
        if not workspace.is_absolute():
            workspace = project_dir / workspace
    except Exception:  # noqa: BLE001
        pass
    return workspace / ".bauer_gateway" / "gateway.pid"


def read_process_status(project_dir: Path) -> tuple[int | None, float | None, float | None]:
    """(pid, uptime_s, memory_mb) do gateway pelo PID file — None se morto."""
    pid_path = _pid_file(project_dir)
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        return None, None, None
    try:
        import time

        import psutil
        proc = psutil.Process(pid)
        cmdline = " ".join(proc.cmdline()).lower()
        if "gateway" not in cmdline and "bauer" not in cmdline:
            return None, None, None  # PID reciclado por outro processo
        uptime = time.time() - proc.create_time()
        rss_mb = proc.memory_info().rss / (1024 * 1024)
        return pid, uptime, rss_mb
    except Exception:  # noqa: BLE001
        return None, None, None


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
# Manager
# ─────────────────────────────────────────────────────────────────────────────


class GatewayServiceManager:
    """Instala/gerencia o gateway como serviço do SO.

    ``run_fn`` é injetável para testes (assinatura de subprocess.run).
    """

    def __init__(
        self,
        project_dir: str | Path | None = None,
        platform: str | None = None,
        run_fn: Callable[..., Any] | None = None,
    ) -> None:
        from .paths import get_bauer_home
        # working dir do serviço é sempre ~/.bauer/ (config.yaml e .env ficam lá)
        self.project_dir = Path(project_dir) if project_dir else get_bauer_home()
        self.platform = platform or detect_service_platform()
        self._run = run_fn or self._default_run

    @staticmethod
    def _default_run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 — comandos montados internamente
            cmd, capture_output=True, text=True, timeout=60, **kw
        )

    def _check_platform(self) -> None:
        if self.platform == "unsupported":
            raise RuntimeError(
                "Plataforma sem gerenciador de serviço suportado. "
                "Use `bauer gateway start` (foreground) ou Docker."
            )

    # ── systemd helpers ─────────────────────────────────────────────────────

    @property
    def _unit_path(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"

    def _systemctl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["systemctl", "--user", *args])

    # ── launchd helpers ─────────────────────────────────────────────────────

    @property
    def _plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{_launchd_label()}.plist"

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess:
        return self._run(["launchctl", *args])

    # ── API ─────────────────────────────────────────────────────────────────

    def install(self) -> str:
        """Instala E inicia o serviço. Retorna mensagem de sucesso."""
        self._check_platform()
        python_path = _service_python(self.project_dir)
        (self.project_dir / "logs").mkdir(exist_ok=True)

        if self.platform == "systemd":
            unit = build_systemd_unit(python_path, self.project_dir)
            self._unit_path.parent.mkdir(parents=True, exist_ok=True)
            self._unit_path.write_text(unit, encoding="utf-8")
            self._systemctl("daemon-reload")
            r = self._systemctl("enable", "--now", SERVICE_NAME)
            if r.returncode != 0:
                raise RuntimeError(f"systemctl enable falhou: {r.stderr.strip()}")
            # linger: serviço sobrevive a logout (igual Hermes)
            user = os.environ.get("USER", "")
            lr = self._run(["loginctl", "enable-linger", user])
            linger_note = (
                " Linger habilitado (sobrevive a logout)."
                if lr.returncode == 0
                else " ⚠️ Não consegui habilitar linger — rode: sudo loginctl enable-linger $USER"
            )
            return (
                f"Serviço {SERVICE_NAME} instalado e iniciado "
                f"({self._unit_path}).{linger_note}"
            )

        if self.platform == "launchd":
            plist = build_launchd_plist(python_path, self.project_dir)
            self._plist_path.parent.mkdir(parents=True, exist_ok=True)
            self._plist_path.write_text(plist, encoding="utf-8")
            # Unload primeiro (idempotente)
            self._launchctl("unload", str(self._plist_path))
            r = self._launchctl("load", "-w", str(self._plist_path))
            if r.returncode != 0:
                raise RuntimeError(f"launchctl load falhou: {(r.stderr or r.stdout).strip()}")
            return (
                f"LaunchAgent '{_launchd_label()}' instalado e iniciado "
                f"({self._plist_path}). Sobe automaticamente no logon; reinicia em crash."
            )

        # windows — Task Scheduler via XML (UTF-16 obrigatório)
        domain = os.environ.get("USERDOMAIN", "")
        username = os.environ.get("USERNAME", "")
        user_id = f"{domain}\\{username}" if domain else username
        xml = build_windows_task_xml(python_path, self.project_dir, user_id)
        xml_path = self.project_dir / "logs" / "bauer-gateway-task.xml"
        xml_path.write_text(xml, encoding="utf-16")
        r = self._run([
            "schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F",
        ])
        if r.returncode != 0:
            raise RuntimeError(f"schtasks /Create falhou: {(r.stderr or r.stdout).strip()}")
        self._run(["schtasks", "/Run", "/TN", TASK_NAME])
        return (
            f"Tarefa '{TASK_NAME}' instalada e iniciada (Task Scheduler). "
            f"Sobe automaticamente no logon de {user_id}; reinicia em crash."
        )

    def uninstall(self) -> str:
        self._check_platform()
        self.stop(quiet=True)
        if self.platform == "systemd":
            self._systemctl("disable", SERVICE_NAME)
            self._unit_path.unlink(missing_ok=True)
            self._systemctl("daemon-reload")
            return f"Serviço {SERVICE_NAME} removido."
        if self.platform == "launchd":
            self._launchctl("unload", "-w", str(self._plist_path))
            self._plist_path.unlink(missing_ok=True)
            return f"LaunchAgent '{_launchd_label()}' removido."
        r = self._run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        if r.returncode != 0:
            raise RuntimeError(f"schtasks /Delete falhou: {(r.stderr or r.stdout).strip()}")
        return f"Tarefa '{TASK_NAME}' removida."

    def start(self) -> str:
        self._check_platform()
        if self.platform == "systemd":
            r = self._systemctl("start", SERVICE_NAME)
            if r.returncode != 0:
                raise RuntimeError(f"systemctl start falhou: {r.stderr.strip()}")
            return f"Serviço {SERVICE_NAME} iniciado."
        if self.platform == "launchd":
            if not self._plist_path.exists():
                raise RuntimeError(
                    "LaunchAgent não instalado — rode 'bauer gateway service install' primeiro."
                )
            r = self._launchctl("load", "-w", str(self._plist_path))
            if r.returncode != 0:
                raise RuntimeError(f"launchctl load falhou: {(r.stderr or r.stdout).strip()}")
            return f"LaunchAgent '{_launchd_label()}' iniciado."
        r = self._run(["schtasks", "/Run", "/TN", TASK_NAME])
        if r.returncode != 0:
            raise RuntimeError(
                f"schtasks /Run falhou: {(r.stderr or r.stdout).strip()} "
                f"— o serviço está instalado? (bauer gateway service install)"
            )
        return f"Tarefa '{TASK_NAME}' iniciada."

    def stop(self, quiet: bool = False) -> str:
        self._check_platform()
        if self.platform == "systemd":
            self._systemctl("stop", SERVICE_NAME)
            return f"Serviço {SERVICE_NAME} parado."
        if self.platform == "launchd":
            self._launchctl("unload", str(self._plist_path))
            pid, _, _ = read_process_status(self.project_dir)
            if pid is not None:
                try:
                    import psutil
                    psutil.Process(pid).terminate()
                except Exception:  # noqa: BLE001
                    pass
            if not quiet:
                return f"LaunchAgent '{_launchd_label()}' parado."
            return ""
        self._run(["schtasks", "/End", "/TN", TASK_NAME])
        # /End não mata filhos órfãos — garante via PID file
        pid, _, _ = read_process_status(self.project_dir)
        if pid is not None:
            try:
                import psutil
                psutil.Process(pid).terminate()
            except Exception:  # noqa: BLE001
                pass
        if not quiet:
            return f"Tarefa '{TASK_NAME}' parada."
        return ""

    def status(self) -> ServiceStatus:
        st = ServiceStatus(platform=self.platform)
        if self.platform == "unsupported":
            st.detail = "plataforma sem systemd/launchd/Task Scheduler"
            return st

        if self.platform == "systemd":
            st.installed = self._unit_path.exists()
            if st.installed:
                en = self._systemctl("is-enabled", SERVICE_NAME)
                st.enabled = en.stdout.strip() == "enabled"
                ac = self._systemctl("is-active", SERVICE_NAME)
                st.running = ac.stdout.strip() == "active"
        elif self.platform == "launchd":
            st.installed = self._plist_path.exists()
            if st.installed:
                st.enabled = True
                r = self._launchctl("list", _launchd_label())
                st.running = r.returncode == 0 and '"PID"' in r.stdout
        else:
            q = self._run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
            st.installed = q.returncode == 0
            if st.installed:
                st.enabled = True  # tarefa existe = trigger de logon ativo
                st.running = "running" in q.stdout.lower() or "em execu" in q.stdout.lower()

        pid, uptime, mem = read_process_status(self.project_dir)
        if pid is not None:
            st.running = True
            st.pid, st.uptime_s, st.memory_mb = pid, uptime, mem
        elif st.running:
            st.detail = "gerenciador reporta ativo; PID file ainda não confirma"
        return st

    def logs(self, lines: int = 50) -> str:
        """Últimas N linhas de log do gateway."""
        if self.platform == "systemd":
            r = self._run([
                "journalctl", "--user", "-u", SERVICE_NAME,
                "-n", str(lines), "--no-pager",
            ])
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout
        # launchd e Windows: log vai para arquivo
        log_path = self.project_dir / LOG_FILE
        if not log_path.exists():
            return f"(sem logs ainda — esperado em {log_path})"
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
            return "\n".join(content.splitlines()[-lines:])
        except OSError as exc:
            return f"(falha lendo {log_path}: {exc})"
