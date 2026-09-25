"""Contrato hermético do runtime Docker/Podman para sandbox de tarefas."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bauer.core.workspace.container_runtime import (
    ContainerCommandError,
    ContainerRuntime,
    ContainerRuntimeError,
    ContainerRuntimeUnavailable,
    ContainerSpec,
    FakeContainerRuntime,
    RuntimeCapabilities,
)


class RecordingRunner:
    """Runner fake: cada resposta corresponde a uma chamada do subprocess."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _spec(tmp_path: Path, **kwargs) -> ContainerSpec:
    source = tmp_path / "input"
    source.mkdir(exist_ok=True)
    values = {
        "image": "registry.example/bauer-sandbox@sha256:abc123",
        "input_path": source,
        "workspace_volume": "bauer-workspace-run-1",
        "name": "bauer-run-1",
        "run_id": "run-1",
        "command": ("python", "-m", "bauer.container_worker"),
    }
    values.update(kwargs)
    return ContainerSpec(**values)


def _runtime(*responses) -> tuple[ContainerRuntime, RecordingRunner]:
    runner = RecordingRunner(_result(stdout="26.1.0\n"), *responses)
    return ContainerRuntime("docker", runner=runner), runner


def test_discover_prefere_docker_e_usa_subprocess_sem_shell():
    runner = RecordingRunner(_result(stderr="missing", returncode=1), _result(stdout="5.4.2\n"))

    runtime = ContainerRuntime.discover(runner=runner)

    assert runtime.executable == "podman"
    assert runtime.capabilities.version == "5.4.2"
    assert [call[0][0] for call in runner.calls] == ["docker", "podman"]
    assert all(call[1]["shell"] is False for call in runner.calls)
    assert all(isinstance(call[0], list) for call in runner.calls)


def test_discover_erro_estruturado_quando_nenhum_engine_existe():
    runner = RecordingRunner(FileNotFoundError("docker"), FileNotFoundError("podman"))

    with pytest.raises(ContainerRuntimeUnavailable) as raised:
        ContainerRuntime.discover(runner=runner)

    assert raised.value.code == "runtime_unavailable"
    assert raised.value.to_dict()["detail"] == "docker: executável não encontrado; podman: executável não encontrado"


def test_start_aplica_hardening_sem_env_socket_ou_segredos(tmp_path):
    runtime, runner = _runtime(_result(stdout="container-123\n"))

    session = runtime.start(_spec(tmp_path))

    assert session.container_id == "container-123"
    command, kwargs = runner.calls[-1]
    assert command[:3] == ["docker", "run", "--detach"]
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--read-only" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert command[command.index("--security-opt") + 1] == "no-new-privileges:true"
    assert command[command.index("--pids-limit") + 1] == "256"
    assert command[command.index("--memory") + 1] == str(512 * 1024 * 1024) + "b"
    assert command[command.index("--cpus") + 1] == "1.0"
    mounts = [command[index + 1] for index, value in enumerate(command) if value == "--mount"]
    assert any("dst=/input,readonly" in mount for mount in mounts)
    assert "type=volume,src=bauer-workspace-run-1,dst=/workspace" in mounts
    assert "--env" not in command and "--env-file" not in command
    assert not any("docker.sock" in argument or "BAUER_HOME" in argument for argument in command)
    assert kwargs["shell"] is False
    assert "env" not in kwargs


def test_start_falha_fechado_quando_engine_indisponivel(tmp_path):
    unavailable = ContainerRuntime(
        "docker",
        capabilities=ContainerRuntime.probe("docker", runner=RecordingRunner(FileNotFoundError("docker"))),
    )

    with pytest.raises(ContainerRuntimeUnavailable) as raised:
        unavailable.start(_spec(tmp_path))

    assert raised.value.code == "runtime_unavailable"


def test_start_falha_fechado_quando_falta_capability_de_hardening(tmp_path):
    runtime = ContainerRuntime(
        "docker",
        capabilities=RuntimeCapabilities("docker", True, supports_no_new_privileges=False),
    )

    with pytest.raises(ContainerRuntimeUnavailable) as raised:
        runtime.start(_spec(tmp_path))

    assert raised.value.code == "runtime_capability_missing"
    assert raised.value.to_dict()["detail"] == "no_new_privileges"


def test_start_reporta_erro_estruturado_do_engine(tmp_path):
    runtime, _runner = _runtime(_result(stderr="image is not allowed", returncode=125))

    with pytest.raises(ContainerCommandError) as raised:
        runtime.start(_spec(tmp_path))

    assert raised.value.code == "runtime_command_failed"
    assert raised.value.returncode == 125
    assert raised.value.to_dict()["detail"] == "image is not allowed"


def test_exec_e_export_patch_usam_argumentos_diretos_e_preservam_bytes(tmp_path):
    runtime, runner = _runtime(
        _result(stdout="container-123\n"),
        _result(stdout="ok\n"),
        _result(stdout=b"diff --git a/a b/a\n\x00binary"),
    )
    session = runtime.start(_spec(tmp_path))

    result = runtime.exec(session, ["python", "-V"])
    exported = runtime.export_patch(session)

    assert result.stdout == "ok\n"
    assert exported.patch.endswith(b"\x00binary")
    assert runner.calls[-2][0] == ["docker", "exec", "container-123", "python", "-V"]
    assert runner.calls[-2][1]["shell"] is False
    assert runner.calls[-1][0][-6:] == ["git", "-C", "/workspace", "diff", "--binary", "--no-ext-diff"]
    assert runner.calls[-1][1]["text"] is False


def test_cleanup_remove_recursos_e_repeticao_e_noop(tmp_path):
    runtime, runner = _runtime(
        _result(stdout="container-123\n"),
        _result(stdout="container-123\n"),
        _result(stdout="bauer-workspace-run-1\n"),
    )
    session = runtime.start(_spec(tmp_path))

    first = runtime.cleanup(session)
    repeated = runtime.cleanup(session)

    assert first.container_removed and first.volume_removed
    assert repeated.already_cleaned
    assert runner.calls[-2][0] == ["docker", "rm", "--force", "container-123"]
    assert runner.calls[-1][0] == ["docker", "volume", "rm", "bauer-workspace-run-1"]


def test_cleanup_aceita_recurso_ja_ausente_e_ainda_remove_volume(tmp_path):
    runtime, _runner = _runtime(
        _result(stdout="container-123\n"),
        _result(stderr="Error: No such container: container-123", returncode=1),
        _result(stdout="bauer-workspace-run-1\n"),
    )
    session = runtime.start(_spec(tmp_path))

    result = runtime.cleanup(session)

    assert not result.container_removed and result.volume_removed


def test_fake_runtime_e_injetavel_sem_subprocess(tmp_path):
    runtime = FakeContainerRuntime(patch=b"patch")
    session = runtime.start(_spec(tmp_path))

    command = runtime.exec(session, ["pytest", "-q"])
    exported = runtime.export_patch(session)
    cleanup = runtime.cleanup(session)

    assert session.container_id == "fake-1"
    assert command.returncode == 0
    assert exported.patch == b"patch"
    assert runtime.exec_calls == [("fake-1", ("pytest", "-q"))]
    assert cleanup.container_removed and cleanup.volume_removed


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"image": "--privileged"}, "image inválida"),
        ({"workspace_volume": "workspace;rm"}, "workspace_volume contém caracteres inválidos"),
        ({"run_id": "run with spaces"}, "run_id contém caracteres inválidos"),
        ({"command": ()}, "command deve ser uma lista não vazia de argumentos"),
    ],
)
def test_spec_recusa_input_inseguro_antes_do_subprocess(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        _spec(tmp_path, **kwargs)


def test_start_recusa_path_com_virgula_que_poderia_alterar_mount(tmp_path):
    source = tmp_path / "input,malicioso"
    source.mkdir()
    runtime = ContainerRuntime("docker", capabilities=RuntimeCapabilities("docker", True))

    with pytest.raises(ContainerRuntimeError, match="mount seguro"):
        runtime.start(_spec(tmp_path, input_path=source))


def test_sessao_de_outro_runtime_e_sessao_limpa_sao_bloqueadas(tmp_path):
    runtime, _runner = _runtime(_result(stdout="container-123\n"), _result(), _result())
    session = runtime.start(_spec(tmp_path))
    foreign = FakeContainerRuntime().start(_spec(tmp_path, name="other", workspace_volume="other"))

    with pytest.raises(ContainerRuntimeError, match="outro runtime"):
        runtime.exec(foreign, ["true"])

    runtime.cleanup(session)
    with pytest.raises(ContainerRuntimeError, match="já foi limpa"):
        runtime.exec(session, ["true"])
