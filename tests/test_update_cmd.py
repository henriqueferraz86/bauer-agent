"""Testes do atualizador nativo ``bauer update``."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bauer.commands import update_cmd


def _ok(command: list[str], *, cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def test_update_busca_codigo_aplica_master_e_reinstala_extras(tmp_path: Path, monkeypatch):
    root = tmp_path / "BauerAgent"
    (root / ".git").mkdir(parents=True)
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path):
        calls.append(command)
        return _ok(command, cwd=str(cwd))

    monkeypatch.setattr(update_cmd, "_repository_root", lambda: root)
    monkeypatch.setattr(update_cmd, "_run", fake_run)
    monkeypatch.setattr(update_cmd.sys, "executable", "python-test")

    update_cmd.update(extras="gateway,voice,voice-kokoro")

    assert calls == [
        ["git", "fetch", "--depth=1", "origin", "master"],
        ["git", "reset", "--hard", "origin/master"],
        [
            "python-test", "-m", "pip", "install", "--quiet", "--upgrade", "-e",
            f"{root}[gateway,voice,voice-kokoro]",
        ],
    ]


def test_update_recusa_diretorio_sem_git(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(update_cmd, "_repository_root", lambda: tmp_path)

    with pytest.raises(update_cmd.typer.Exit) as exc:
        update_cmd.update()

    assert exc.value.exit_code == 1
