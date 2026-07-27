"""Testes do caminho --update do install.sh.

O bug que motivou: quem instalou por `pipx install bauer-agent` não tem clone em
~/.local/share/bauer-agent, então `install.sh --update` morria com "execute sem
--update para instalar" — conselho que faz nascer uma SEGUNDA instalação
paralela, e aí qual das duas roda vira questão de ordem do PATH.

Roda o script de verdade com HOME e PATH falsos: o `pipx` do PATH é um stub que
grava os argumentos recebidos, então dá para afirmar o que foi chamado.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parent.parent / "install.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name == "nt",
    reason="install.sh só roda em shell POSIX",
)


def _fake_env(tmp_path: Path, *, pipx_installed: bool) -> tuple[dict, Path]:
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    if pipx_installed:
        (home / ".local" / "share" / "pipx" / "venvs" / "bauer-agent").mkdir(parents=True)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "pipx_args.txt"
    stub = bindir / "pipx"
    stub.write_text(f'#!/usr/bin/env bash\necho "$@" >> "{log}"\n', encoding="utf-8")
    stub.chmod(0o755)

    env = dict(os.environ)
    env.update({"HOME": str(home), "PATH": f"{bindir}{os.pathsep}{env['PATH']}"})
    return env, log


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(INSTALL_SH), "--update"],
        capture_output=True, text=True, env=env, timeout=120,
    )


def test_update_delega_ao_pipx_quando_e_instalacao_pipx(tmp_path: Path):
    env, log = _fake_env(tmp_path, pipx_installed=True)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert log.exists(), "pipx não foi chamado"
    chamada = log.read_text(encoding="utf-8")
    assert "install --force" in chamada
    assert "git+" in chamada and "@master" in chamada


def test_update_sem_nenhuma_instalacao_nao_sugere_duplicar(tmp_path: Path):
    env, log = _fake_env(tmp_path, pipx_installed=False)

    result = _run(env)

    assert result.returncode != 0
    assert not log.exists(), "não devia ter chamado pipx sem instalação pipx"
    assert "Nenhuma instalação encontrada" in result.stderr
