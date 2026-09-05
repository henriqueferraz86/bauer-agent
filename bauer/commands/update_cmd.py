"""Comando ``bauer update`` para atualizar a instalação atual."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer

from ._common import console


_DEFAULT_EXTRAS = "gateway,voice,voice-kokoro"


def _repository_root() -> Path:
    """Retorna a raiz da instalação que está executando o comando."""
    return Path(__file__).resolve().parents[2]


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Executa uma etapa sem despejar stdout/stderr técnico no chat."""
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


def _failure_message(step: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    reason = detail[-1].strip() if detail else "sem detalhes adicionais"
    return f"Falha ao {step}: {reason}"


def update(
    extras: str = typer.Option(
        _DEFAULT_EXTRAS,
        "--extras",
        help="Extras a reinstalar (padrão: gateway,voice,voice-kokoro).",
    ),
):
    """Atualiza o Bauer pela versão mais recente da branch master.

    O comando atua somente no repositório da instalação que está executando o
    Bauer, preservando a configuração e os modelos que ficam fora dele.
    """
    root = _repository_root()
    if not (root / ".git").exists():
        console.print(
            "[red]Instalação do Bauer não encontrada como repositório Git.[/red]\n"
            "[dim]Use o instalador oficial para criar uma instalação atualizável.[/dim]"
        )
        raise typer.Exit(code=1)

    console.print("[cyan]Atualizando Bauer...[/cyan]")
    fetched = _run(["git", "fetch", "--depth=1", "origin", "master"], cwd=root)
    if fetched.returncode != 0:
        console.print(f"[red]{_failure_message('buscar a versão mais recente', fetched)}[/red]")
        raise typer.Exit(code=1)

    reset = _run(["git", "reset", "--hard", "origin/master"], cwd=root)
    if reset.returncode != 0:
        console.print(f"[red]{_failure_message('aplicar a atualização', reset)}[/red]")
        raise typer.Exit(code=1)

    extras_value = extras.strip()
    target = str(root) if not extras_value else f"{root}[{extras_value}]"
    console.print("[cyan]Atualizando dependências...[/cyan]")
    installed = _run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "-e", target],
        cwd=root,
    )
    if installed.returncode != 0:
        console.print(
            f"[red]{_failure_message('atualizar as dependências', installed)}[/red]"
        )
        raise typer.Exit(code=1)

    console.print("[green]Bauer atualizado com sucesso.[/green]")

