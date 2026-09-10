"""Contratos do MVP de autonomia contínua segura."""

from __future__ import annotations

import time
import subprocess
import threading
from types import SimpleNamespace

import yaml

import pytest


def _wait_for(manager, state: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.status()["state"]["state"] == state:
            return
        time.sleep(0.01)
    pytest.fail(f"estado não chegou a {state}: {manager.status()}")


def test_sem_alvo_nao_sonda_rede(tmp_path):
    from bauer.config_loader import ContinuousAutonomySection
    from bauer.continuous_autonomy import ContinuousAutonomy

    calls = []
    manager = ContinuousAutonomy(root=tmp_path, config=ContinuousAutonomySection(enabled=True),
                                 probe=lambda target: calls.append(target) or (True, 200, 1, None))
    with pytest.raises(ValueError, match="nenhum alvo"):
        manager.start()
    assert calls == []
    assert manager.status()["state"]["state"] == "off"


def test_boot_cadastra_containers_pausados_com_autocorrecao(tmp_path):
    from bauer.config_loader import ContinuousAutonomySection
    from bauer.continuous_autonomy import (
        ContinuousAutonomy,
        sync_discovered_docker_targets,
    )

    config = tmp_path / "config.yaml"
    config.write_text("continuous_autonomy:\n  enabled: false\n", encoding="utf-8")

    def fake_run(command, **kwargs):
        assert command == ["docker", "ps", "-a", "--format", "{{json .}}"]
        return SimpleNamespace(
            returncode=0,
            stdout='{"ID":"abc123","Names":"api","Image":"example/api","State":"running"}\n',
            stderr="",
        )

    assert sync_discovered_docker_targets(config, runner=fake_run) is True
    assert sync_discovered_docker_targets(config, runner=fake_run) is False
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    target = raw["continuous_autonomy"]["targets"][0]
    assert target["container_name"] == "api"
    assert target["enabled"] is False
    assert target["auto_recover"] is True
    assert target["recovery_action"] == "docker_recover"
    assert target["auto_discovered"] is True

    target["auto_recover"] = False
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert sync_discovered_docker_targets(config, runner=fake_run) is False
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert raw["continuous_autonomy"]["targets"][0]["auto_recover"] is False

    manager = ContinuousAutonomy(
        root=tmp_path / "runtime",
        config=ContinuousAutonomySection.model_validate(raw["continuous_autonomy"]),
    )
    status = manager.status()["targets"][0]
    assert status["state"] == "paused"
    assert status["enabled"] is False
    assert status["auto_recover"] is False


def test_boot_nao_remove_cadastro_se_docker_estiver_indisponivel(tmp_path):
    from bauer.continuous_autonomy import sync_discovered_docker_targets

    config = tmp_path / "config.yaml"
    config.write_text(
        "continuous_autonomy:\n"
        "  targets:\n"
        "    - id: docker-api\n"
        "      name: Container api\n"
        "      type: docker_container\n"
        "      container_name: api\n"
        "      enabled: false\n"
        "      auto_recover: true\n"
        "      recovery_action: docker_recover\n"
        "      auto_discovered: true\n",
        encoding="utf-8",
    )

    def unavailable(command, **kwargs):
        raise OSError("docker indisponível")

    assert sync_discovered_docker_targets(config, runner=unavailable) is False
    assert "container_name: api" in config.read_text(encoding="utf-8")


def test_docker_probe_usa_estado_do_container(tmp_path, monkeypatch):
    from bauer.continuous_autonomy import ContinuousAutonomy, HealthTarget

    monkeypatch.setattr(
        "bauer.continuous_autonomy.subprocess.run",
        lambda command, **kwargs: SimpleNamespace(returncode=0, stdout="true\n", stderr=""),
    )
    target = HealthTarget(
        id="docker-api", name="Container api", type="docker_container",
        url="", container_name="api", auto_recover=True,
        recovery_action="docker_recover",
    )
    assert ContinuousAutonomy._docker_probe(target)[0] is True


def test_start_status_stop_e_incidente(tmp_path):
    from bauer.config_loader import ContinuousAutonomySection, ContinuousAutonomyTarget
    from bauer.continuous_autonomy import ContinuousAutonomy

    section = ContinuousAutonomySection(enabled=True, targets=[ContinuousAutonomyTarget(
        id="api", name="API", url="http://example.test/health", interval_s=1,
    )])
    events = []
    manager = ContinuousAutonomy(
        root=tmp_path, config=section,
        probe=lambda target: (False, 503, 4.0, "serviço indisponível"),
    )
    manager.event_bus.subscribe("autonomy.incident", events.append)
    manager.start()
    _wait_for(manager, "running")
    deadline = time.monotonic() + 2
    while ((not manager.status()["incidents"] or not manager.status()["recommendations"])
           and time.monotonic() < deadline):
        time.sleep(0.01)
    assert manager.status()["targets"][0]["state"] == "failed"
    assert manager.status()["recommendations"]
    assert events and events[0].message == "serviço indisponível"
    manager.stop()
    _wait_for(manager, "stopped")


def test_start_reinicia_worker_antigo_ainda_vivo(tmp_path):
    from bauer.config_loader import ContinuousAutonomySection, ContinuousAutonomyTarget
    from bauer.continuous_autonomy import ContinuousAutonomy

    section = ContinuousAutonomySection(enabled=True, targets=[ContinuousAutonomyTarget(
        id="api", name="API", url="http://example.test/health", interval_s=1,
    )])
    manager = ContinuousAutonomy(
        root=tmp_path, config=section,
        probe=lambda target: (True, 200, 1.0, None),
    )
    old_finished = threading.Event()

    def stale_worker():
        while not manager._stop.is_set():
            time.sleep(0.01)
        old_finished.set()

    old_thread = threading.Thread(target=stale_worker, daemon=True)
    old_thread.start()
    manager._thread = old_thread
    manager._state.state = "stopped"
    manager._state.owner_pid = None

    manager.start()
    _wait_for(manager, "running")
    assert old_finished.wait(1.0)
    manager.close()
    _wait_for(manager, "stopped")


def test_close_persiste_worker_parado(tmp_path):
    from bauer.config_loader import ContinuousAutonomySection, ContinuousAutonomyTarget
    from bauer.continuous_autonomy import ContinuousAutonomy

    section = ContinuousAutonomySection(enabled=True, targets=[ContinuousAutonomyTarget(
        id="api", name="API", url="http://example.test/health", interval_s=1,
    )])
    manager = ContinuousAutonomy(
        root=tmp_path, config=section,
        probe=lambda target: (True, 200, 1.0, None),
    )
    manager.start()
    _wait_for(manager, "running")

    manager.close()

    assert manager.status()["state"]["state"] == "stopped"
    assert manager.status()["state"]["owner_pid"] is None


def test_alerta_de_voz_falha_vira_log_e_evento(tmp_path, monkeypatch):
    from bauer.config_loader import ContinuousAutonomySection, ContinuousAutonomyTarget
    from bauer.continuous_autonomy import ContinuousAutonomy

    section = ContinuousAutonomySection(
        enabled=True,
        voice_enabled=True,
        targets=[ContinuousAutonomyTarget(id="api", name="API", url="http://x.test")],
    )
    manager = ContinuousAutonomy(root=tmp_path, config=section,
                                 probe=lambda target: (False, None, None, "offline"))
    events = []
    manager.event_bus.subscribe("autonomy.alert", events.append)
    monkeypatch.setattr("bauer.tts.synthesize_speech", lambda text: {"success": False, "error": "sem TTS"})
    manager.start()
    _wait_for(manager, "running")
    deadline = time.monotonic() + 2
    while not events and time.monotonic() < deadline:
        time.sleep(0.01)
    assert events and events[0].status == "incident"
    manager.stop()


def test_desktop_controles_e_delegacao_persistem(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bauer import desktop_api

    monkeypatch.setattr("bauer.continuous_autonomy.discover_docker_containers", lambda **kwargs: [])

    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: x\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Bauer Tests"], cwd=workspace, check=True)
    (workspace / "README.md").write_text("workspace\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=workspace, check=True)
    app = FastAPI()
    app.include_router(desktop_api.build_desktop_router(
        get_config_path=lambda: config,
        get_workspace=lambda: workspace,
        runtime_root=tmp_path / "runtime",
        start_loop=lambda message, project_id, isolated: {"run_id": "run-test"},
    ))
    client = TestClient(app)
    assert client.get("/api/autonomy/status").json()["state"]["state"] == "off"
    assert client.post("/api/autonomy/start").status_code == 409
    added = client.post("/api/autonomy/targets", json={
        "name": "MT5 dashboard", "url": "http://127.0.0.1:8010/",
    })
    assert added.status_code == 200
    assert added.json()["target"]["id"] == "mt5-dashboard"
    assert client.get("/api/autonomy/status").json()["configured_targets"] == 1
    paused = client.post("/api/autonomy/targets/mt5-dashboard/enabled", json={"enabled": False})
    assert paused.status_code == 200
    assert client.get("/api/autonomy/status").json()["targets"][0]["state"] == "paused"
    resumed = client.post("/api/autonomy/targets/mt5-dashboard/enabled", json={"enabled": True})
    assert resumed.status_code == 200
    assert client.post("/api/autonomy/targets", json={
        "name": "MT5 dashboard", "url": "http://127.0.0.1:8010/",
    }).status_code == 422
    docker_added = client.post("/api/autonomy/targets", json={
        "name": "Container api", "type": "docker_container",
        "container_name": "api", "enabled": False,
    })
    assert docker_added.status_code == 200
    docker_id = docker_added.json()["target"]["id"]
    turned_on = client.post(f"/api/autonomy/targets/{docker_id}/auto-recover", json={"enabled": True})
    assert turned_on.status_code == 200
    assert turned_on.json()["auto_recover"] is True
    turned_off = client.post(f"/api/autonomy/targets/{docker_id}/auto-recover", json={"enabled": False})
    assert turned_off.status_code == 200
    assert turned_off.json()["auto_recover"] is False
    deleted = client.delete(f"/api/autonomy/targets/{docker_id}")
    assert deleted.status_code == 200
    assert deleted.json()["removed"] == docker_id
    assert client.get("/api/autonomy/status").json()["configured_targets"] == 1
    assert client.delete(f"/api/autonomy/targets/{docker_id}").status_code == 404
    assert client.post("/api/autonomy/stop").status_code == 200
    delegated = client.post("/api/autonomy/delegate", json={"kind": "application", "message": "x"})
    assert delegated.status_code == 200
    assert delegated.json()["delegation"]["isolation"] == "worktree"
    assert delegated.json()["approval"]["operation"] == "autonomy.merge_worktree"


def test_alert_level_validation(tmp_path):
    from bauer.continuous_autonomy import ContinuousAutonomy

    manager = ContinuousAutonomy(root=tmp_path)
    with pytest.raises(ValueError, match="alert_level"):
        manager.set_alerts(alert_level="yolo")


def test_docker_target_diagnostica_recupera_e_verifica(tmp_path, monkeypatch):
    from bauer.config_loader import ContinuousAutonomySection, ContinuousAutonomyTarget
    from bauer.continuous_autonomy import ContinuousAutonomy

    section = ContinuousAutonomySection(enabled=True, targets=[ContinuousAutonomyTarget(
        id="mt5", name="MT5", type="docker_container", url="http://127.0.0.1:8010/",
        container_name="nautilus-mt5-dashboard", auto_recover=True,
        recovery_action="docker_recover", recovery_cooldown_s=1,
    )])
    calls = []
    alerts = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0:2] == ["docker", "inspect"]:
            output = "false" if "{{.State.Running}}" in command else '{"Status":"exited"}'
        elif command[1] == "logs":
            output = "container stopped"
        else:
            output = "nautilus-mt5-dashboard"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    probe_results = iter([(False, None, 3.0, "conexão recusada"), (True, 200, 5.0, None)])
    monkeypatch.setattr("bauer.continuous_autonomy.subprocess.run", fake_run)
    manager = ContinuousAutonomy(root=tmp_path, config=section,
                                 probe=lambda target: next(probe_results),
                                 alert_callback=lambda kind, message: alerts.append((kind, message)))
    manager._check(manager._targets[0])
    status = manager.status()["targets"][0]
    assert ["docker", "start", "nautilus-mt5-dashboard"] in calls
    assert status["state"] == "healthy"
    assert manager.status()["incidents"][0]["recovery"]["success"] is True
    assert any(kind == "action_completed" for kind, _message in alerts)
