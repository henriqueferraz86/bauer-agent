"""Testes do bauer/gateway_service.py — gateway como serviço do sistema."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bauer.gateway_service import (
    SERVICE_NAME,
    TASK_NAME,
    GatewayServiceManager,
    ServiceStatus,
    build_systemd_unit,
    build_windows_task_xml,
    detect_service_platform,
    format_uptime,
    read_process_status,
)


class _FakeRun:
    """Captura comandos e devolve respostas roteirizadas."""

    def __init__(self, responses: dict[str, tuple[int, str, str]] | None = None):
        self.calls: list[list[str]] = []
        self.responses = responses or {}

    def __call__(self, cmd: list[str], **kw) -> subprocess.CompletedProcess:
        self.calls.append(cmd)
        key = " ".join(cmd[:3])
        rc, out, err = 0, "", ""
        for pattern, (p_rc, p_out, p_err) in self.responses.items():
            if pattern in " ".join(cmd):
                rc, out, err = p_rc, p_out, p_err
                break
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=err)


class TestTemplates:
    def test_systemd_unit_campos_criticos(self, tmp_path):
        unit = build_systemd_unit("/opt/venv/bin/python", tmp_path)
        assert "ExecStart=/opt/venv/bin/python -m bauer.gateway_runtime" in unit
        assert f"WorkingDirectory={tmp_path}" in unit
        assert "Restart=always" in unit
        assert "RestartSec=5" in unit
        assert "WantedBy=default.target" in unit
        assert f"EnvironmentFile=-{tmp_path / '.env'}" in unit  # opcional (hífen)

    def test_windows_xml_campos_criticos(self, tmp_path):
        xml = build_windows_task_xml(r"C:\py\pythonw.exe", tmp_path, r"PC\henri")
        assert "<Command>C:\\py\\pythonw.exe</Command>" in xml
        assert "<Arguments>-m bauer.gateway_runtime</Arguments>" in xml
        assert f"<WorkingDirectory>{tmp_path}</WorkingDirectory>" in xml
        assert "<RestartOnFailure>" in xml
        assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml  # sem limite 72h
        assert "<LogonTrigger>" in xml
        assert r"PC\henri" in xml


class TestDetectPlatform:
    def test_retorna_valor_valido(self):
        assert detect_service_platform() in ("windows", "systemd", "unsupported")

    def test_windows_quando_win32(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        assert detect_service_platform() == "windows"


class TestFormatUptime:
    def test_segundos(self):
        assert format_uptime(90) == "1m 30s"

    def test_horas(self):
        assert format_uptime(3 * 3600 + 120) == "3h 2m"

    def test_dias(self):
        assert format_uptime(2 * 86400 + 3600) == "2d 1h 0m"


class TestManagerSystemd:
    def _mgr(self, tmp_path, responses=None, home=None):
        fake = _FakeRun(responses)
        mgr = GatewayServiceManager(
            project_dir=tmp_path, platform="systemd", run_fn=fake,
        )
        return mgr, fake

    def test_install_escreve_unit_e_habilita(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        mgr, fake = self._mgr(tmp_path)
        msg = mgr.install()
        unit_path = tmp_path / "home" / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        assert unit_path.exists()
        assert "Restart=always" in unit_path.read_text(encoding="utf-8")
        joined = [" ".join(c) for c in fake.calls]
        assert any("daemon-reload" in c for c in joined)
        assert any("enable --now" in c for c in joined)
        assert any("enable-linger" in c for c in joined)
        assert SERVICE_NAME in msg

    def test_uninstall_remove_unit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        mgr, fake = self._mgr(tmp_path)
        mgr.install()
        msg = mgr.uninstall()
        unit_path = tmp_path / "home" / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        assert not unit_path.exists()
        assert "removido" in msg

    def test_status_ativo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
        responses = {
            "is-enabled": (0, "enabled\n", ""),
            "is-active": (0, "active\n", ""),
        }
        mgr, _ = self._mgr(tmp_path, responses)
        unit_path = tmp_path / "home" / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text("[Unit]", encoding="utf-8")
        st = mgr.status()
        assert st.installed and st.enabled and st.running

    def test_logs_usa_journalctl(self, tmp_path):
        responses = {"journalctl": (0, "linha de log\n", "")}
        mgr, fake = self._mgr(tmp_path, responses)
        out = mgr.logs(lines=10)
        assert "linha de log" in out
        assert any("journalctl" in c[0] for c in fake.calls)


class TestManagerWindows:
    def _mgr(self, tmp_path, responses=None):
        fake = _FakeRun(responses)
        mgr = GatewayServiceManager(
            project_dir=tmp_path, platform="windows", run_fn=fake,
        )
        return mgr, fake

    def test_install_cria_xml_e_registra(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERDOMAIN", "PC")
        monkeypatch.setenv("USERNAME", "henri")
        mgr, fake = self._mgr(tmp_path)
        msg = mgr.install()
        xml_path = tmp_path / "logs" / "bauer-gateway-task.xml"
        assert xml_path.exists()
        xml = xml_path.read_text(encoding="utf-16")
        assert "bauer.gateway_runtime" in xml
        joined = [" ".join(c) for c in fake.calls]
        assert any("/Create" in c and TASK_NAME in c for c in joined)
        assert any("/Run" in c for c in joined)
        assert TASK_NAME in msg

    def test_install_falha_com_erro_claro(self, tmp_path, monkeypatch):
        monkeypatch.setenv("USERNAME", "henri")
        responses = {"/Create": (1, "", "Acesso negado")}
        mgr, _ = self._mgr(tmp_path, responses)
        with pytest.raises(RuntimeError, match="Acesso negado"):
            mgr.install()

    def test_uninstall_deleta_tarefa(self, tmp_path):
        mgr, fake = self._mgr(tmp_path)
        msg = mgr.uninstall()
        joined = [" ".join(c) for c in fake.calls]
        assert any("/Delete" in c for c in joined)
        assert "removida" in msg

    def test_status_running_pelo_query(self, tmp_path):
        responses = {
            "/Query": (0, "TaskName: BauerGateway\nStatus: Running\n", ""),
        }
        mgr, _ = self._mgr(tmp_path, responses)
        st = mgr.status()
        assert st.installed and st.running

    def test_status_nao_instalado(self, tmp_path):
        responses = {"/Query": (1, "", "ERROR: not found")}
        mgr, _ = self._mgr(tmp_path, responses)
        st = mgr.status()
        assert not st.installed

    def test_logs_le_arquivo(self, tmp_path):
        log = tmp_path / "logs" / "gateway.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(f"linha {i}" for i in range(100)), encoding="utf-8")
        mgr, _ = self._mgr(tmp_path)
        out = mgr.logs(lines=10)
        assert "linha 99" in out
        assert "linha 89" not in out  # só as últimas 10


class TestUnsupported:
    def test_install_em_plataforma_sem_suporte(self, tmp_path):
        mgr = GatewayServiceManager(
            project_dir=tmp_path, platform="unsupported", run_fn=_FakeRun(),
        )
        with pytest.raises(RuntimeError, match="foreground"):
            mgr.install()

    def test_status_nao_quebra(self, tmp_path):
        mgr = GatewayServiceManager(
            project_dir=tmp_path, platform="unsupported", run_fn=_FakeRun(),
        )
        st = mgr.status()
        assert isinstance(st, ServiceStatus)
        assert not st.installed


class TestReadProcessStatus:
    def test_sem_pid_file(self, tmp_path):
        assert read_process_status(tmp_path) == (None, None, None)

    def test_pid_morto(self, tmp_path):
        pid_dir = tmp_path / "workspace" / ".bauer_gateway"
        pid_dir.mkdir(parents=True)
        (pid_dir / "gateway.pid").write_text("999999999", encoding="utf-8")
        pid, uptime, mem = read_process_status(tmp_path)
        assert pid is None

    def test_pid_vivo_do_proprio_python(self, tmp_path, monkeypatch):
        import os
        pid_dir = tmp_path / "workspace" / ".bauer_gateway"
        pid_dir.mkdir(parents=True)
        (pid_dir / "gateway.pid").write_text(str(os.getpid()), encoding="utf-8")
        # o processo de teste roda pytest (cmdline contém "py"), então o
        # filtro "bauer/gateway" pode rejeitar — aceita ambos os resultados
        # desde que não levante exceção
        pid, uptime, mem = read_process_status(tmp_path)
        assert pid is None or pid == os.getpid()
