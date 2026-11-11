"""Testes do atualizador nativo ``bauer update``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bauer.commands import update_cmd


def _result(command: list[str], code: int = 0, *, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)


def test_update_preserva_estado_e_reinstala_extras_com_uv(tmp_path: Path, monkeypatch):
    root = tmp_path / "BauerAgent"
    home = tmp_path / "home"
    (root / ".git").mkdir(parents=True)
    home.mkdir()
    config = home / "config.yaml"
    env_file = home / ".env"
    memory = home / "memory" / "USER_PREFERENCES.md"
    config.write_text("model: antigo\n", encoding="utf-8")
    env_file.write_text("BAUER_TTS_PROVIDER=kokoro\n", encoding="utf-8")
    memory.parent.mkdir()
    memory.write_text("preferencia preservada\n", encoding="utf-8")
    original = {p: p.read_bytes() for p in (config, env_file, memory)}
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path):
        calls.append(command)
        if command[:2] == ["git", "rev-parse"]:
            return _result(command, stdout="old-head\n")
        if command[:3] == ["git", "reset", "--hard"] and command[-1] == "origin/master":
            config.write_text("config do master\n", encoding="utf-8")
        if command[:2] == ["uv-test", "sync"]:
            env_file.write_text("ambiente alterado\n", encoding="utf-8")
        return _result(command)

    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.setattr(update_cmd, "_repository_root", lambda: root)
    monkeypatch.setattr(update_cmd, "_run", fake_run)
    monkeypatch.setattr(update_cmd, "_uv_command", lambda: "uv-test")

    update_cmd.update(extras="gateway,voice,voice-kokoro")

    assert {p: p.read_bytes() for p in original} == original
    assert calls[0] == ["git", "rev-parse", "HEAD"]
    assert calls[1] == [
        "git", "fetch", "--depth=1", "origin",
        "+refs/heads/master:refs/remotes/origin/master",
    ]
    assert calls[2] == ["git", "reset", "--hard", "origin/master"]
    assert calls[3] == [
        "uv-test", "sync", "--frozen",
        "--extra", "gateway", "--extra", "voice", "--extra", "voice-kokoro",
    ]
    assert calls[4][-2:] == ["-c", "import bauer"]
    assert all("pip" not in call for call in calls)


def test_update_remove_estado_novo_que_nao_existia_antes(tmp_path: Path, monkeypatch):
    root = tmp_path / "BauerAgent"
    home = tmp_path / "home"
    (root / ".git").mkdir(parents=True)
    home.mkdir()
    created = home / "agents.yaml"

    def fake_run(command: list[str], *, cwd: Path):
        if command[:2] == ["git", "rev-parse"]:
            return _result(command, stdout="old-head\n")
        if command[:3] == ["git", "reset", "--hard"] and command[-1] == "origin/master":
            created.write_text("configuração criada pela atualização\n", encoding="utf-8")
        return _result(command)

    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.setattr(update_cmd, "_repository_root", lambda: root)
    monkeypatch.setattr(update_cmd, "_run", fake_run)
    monkeypatch.setattr(update_cmd, "_uv_command", lambda: "uv-test")

    update_cmd.update(extras="gateway,voice,voice-kokoro")

    assert not created.exists()


def test_update_faz_rollback_se_a_sincronizacao_falhar(tmp_path: Path, monkeypatch):
    root = tmp_path / "BauerAgent"
    home = tmp_path / "home"
    (root / ".git").mkdir(parents=True)
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("model: funcionando\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path):
        calls.append(command)
        if command[:2] == ["git", "rev-parse"]:
            return _result(command, stdout="old-head\n")
        if command[:3] == ["git", "reset", "--hard"] and command[-1] == "origin/master":
            config.write_text("modelo quebrado\n", encoding="utf-8")
        if command[:2] == ["uv-test", "sync"]:
            return _result(command, 1, stderr="dependências incompatíveis")
        return _result(command)

    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.setattr(update_cmd, "_repository_root", lambda: root)
    monkeypatch.setattr(update_cmd, "_run", fake_run)
    monkeypatch.setattr(update_cmd, "_uv_command", lambda: "uv-test")

    with pytest.raises(update_cmd.typer.Exit) as exc:
        update_cmd.update(extras="voice,voice-kokoro")

    assert exc.value.exit_code == 1
    assert config.read_text(encoding="utf-8") == "model: funcionando\n"
    assert ["git", "reset", "--hard", "old-head"] in calls
    assert any(
        call[:5] == [sys.executable, "-m", "pip", "install", "--no-deps"]
        for call in calls
    )


def test_update_recusa_diretorio_sem_git(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(update_cmd, "_repository_root", lambda: tmp_path)

    with pytest.raises(update_cmd.typer.Exit) as exc:
        update_cmd.update()

    assert exc.value.exit_code == 1


def test_update_em_clone_de_uma_branch_so_chega_ao_master_buscado(tmp_path: Path, monkeypatch):
    """Clone `--single-branch` de outra branch: `fetch origin master` sozinho não
    move `origin/master`, e o reset caía numa versão antiga anunciando sucesso."""

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
            cwd=str(cwd), check=True, capture_output=True, text=True,
        ).stdout.strip()

    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git("init", "-q", "-b", "master", cwd=upstream)
    (upstream / "VERSION").write_text("1\n", encoding="utf-8")
    git("add", "VERSION", cwd=upstream)
    git("commit", "-q", "-m", "v1", cwd=upstream)
    git("branch", "feature", cwd=upstream)
    root = tmp_path / "BauerAgent"
    git("clone", "-q", "--branch", "feature", "--single-branch",
        upstream.as_uri(), str(root), cwd=tmp_path)
    # origin/master antigo existente, como na instalação real
    git("fetch", "-q", "origin", "master:refs/remotes/origin/master", cwd=root)
    (upstream / "VERSION").write_text("2\n", encoding="utf-8")
    git("commit", "-q", "-am", "v2", cwd=upstream)
    newest = git("rev-parse", "HEAD", cwd=upstream)

    home = tmp_path / "home"
    home.mkdir()

    def run(command: list[str], *, cwd: Path):
        if command[0] == "git":
            return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)
        return _result(command)  # uv sync e smoke: fora do escopo deste teste

    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.setattr(update_cmd, "_repository_root", lambda: root)
    monkeypatch.setattr(update_cmd, "_run", run)
    monkeypatch.setattr(update_cmd, "_uv_command", lambda: "uv-test")

    update_cmd.update(extras="gateway")

    assert git("rev-parse", "HEAD", cwd=root) == newest
    assert (root / "VERSION").read_text(encoding="utf-8") == "2\n"
