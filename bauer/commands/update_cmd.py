"""Comando ``bauer update`` para atualizar a instalação sem perder estado."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer

from ._common import console


_DEFAULT_EXTRAS = "gateway,voice,voice-kokoro"
_PRESERVED_FILES = (
    ".env",
    "config.yaml",
    "models.yaml",
    "agents.yaml",
    ".runtime_state.json",
)


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


def _uv_command() -> str:
    """Resolve uv sem depender de ele estar no PATH do serviço."""
    configured = os.environ.get("BAUER_UV", "").strip()
    if configured:
        return configured
    found = shutil.which("uv")
    if found:
        return found
    candidates = [
        Path.home() / ".local" / "bin" / "uv",
        Path.home() / ".local" / "bin" / "uv.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "uv" / "uv.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "uv"


def _extras_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _sync_command(extras: str) -> list[str]:
    command = [_uv_command(), "sync", "--frozen"]
    for extra in _extras_list(extras):
        command.extend(("--extra", extra))
    return command


def _user_home() -> Path:
    configured = os.environ.get("BAUER_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".bauer"


def _preserved_paths(root: Path) -> list[Path]:
    """Lista apenas estado pequeno e mutável que uma atualização deve custodiar."""
    bases = {root, _user_home()}
    paths: set[Path] = set()
    for base in bases:
        paths.update(base / name for name in _PRESERVED_FILES)
        memory = base / "memory"
        if memory.is_dir():
            paths.update(path for path in memory.rglob("*.md") if path.is_file())
    return sorted(paths)


def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    """Captura também ausências para impedir que a atualização crie estado novo."""
    return {path: path.read_bytes() if path.is_file() else None for path in paths}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            if path.is_file():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != content:
            path.write_bytes(content)


def _unchanged(snapshot: dict[Path, bytes | None]) -> bool:
    return all(
        (content is None and not path.exists())
        or (content is not None and path.is_file() and path.read_bytes() == content)
        for path, content in snapshot.items()
    )


def _rollback(root: Path, previous_head: str, snapshot: dict[Path, bytes | None]) -> None:
    _run(["git", "reset", "--hard", previous_head], cwd=root)
    _restore(snapshot)


def _abort_update(
    *,
    root: Path,
    previous_head: str,
    snapshot: dict[Path, bytes | None],
    step: str,
    result: subprocess.CompletedProcess[str],
) -> None:
    _rollback(root, previous_head, snapshot)
    console.print(
        f"[red]{_failure_message(step, result)}[/red]\n"
        "[yellow]A atualização foi revertida; configurações e memória foram preservadas.[/yellow]"
    )
    raise typer.Exit(code=1)


def update(
    extras: str = typer.Option(
        _DEFAULT_EXTRAS,
        "--extras",
        help="Extras a manter no ambiente (padrão: gateway,voice,voice-kokoro).",
    ),
):
    """Atualiza a master sem desconfigurar o ambiente funcional.

    O código do repositório é substituído, mas config, .env, credenciais,
    modelos, agentes, estado e memória ficam sob snapshot e são restaurados.
    Dependências são sincronizadas pelo lock com os extras selecionados.
    """
    root = _repository_root()
    if not (root / ".git").exists():
        console.print(
            "[red]Instalação do Bauer não encontrada como repositório Git.[/red]\n"
            "[dim]Use o instalador oficial para criar uma instalação atualizável.[/dim]"
        )
        raise typer.Exit(code=1)

    previous = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if previous.returncode != 0:
        console.print(f"[red]{_failure_message('ler a versão atual', previous)}[/red]")
        raise typer.Exit(code=1)
    previous_head = previous.stdout.strip()
    preserved = _snapshot(_preserved_paths(root))

    console.print(
        f"[cyan]Atualizando Bauer (estado preservado: {len(preserved)} arquivo(s))...[/cyan]"
    )
    fetched = _run(["git", "fetch", "--depth=1", "origin", "master"], cwd=root)
    if fetched.returncode != 0:
        _abort_update(
            root=root,
            previous_head=previous_head,
            snapshot=preserved,
            step="buscar a versão mais recente",
            result=fetched,
        )

    reset = _run(["git", "reset", "--hard", "origin/master"], cwd=root)
    if reset.returncode != 0:
        _abort_update(
            root=root,
            previous_head=previous_head,
            snapshot=preserved,
            step="aplicar a atualização",
            result=reset,
        )
    _restore(preserved)

    extras_value = extras.strip()
    console.print("[cyan]Sincronizando dependências pelo uv.lock...[/cyan]")
    installed = _run(_sync_command(extras_value), cwd=root)
    if installed.returncode != 0:
        _abort_update(
            root=root,
            previous_head=previous_head,
            snapshot=preserved,
            step="atualizar as dependências",
            result=installed,
        )

    _restore(preserved)
    if not _unchanged(preserved):
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="estado preservado foi alterado")
        _abort_update(
            root=root,
            previous_head=previous_head,
            snapshot=preserved,
            step="validar o estado preservado",
            result=result,
        )

    smoke = _run([sys.executable, "-c", "import bauer"], cwd=root)
    if smoke.returncode != 0:
        _abort_update(
            root=root,
            previous_head=previous_head,
            snapshot=preserved,
            step="validar o ambiente atualizado",
            result=smoke,
        )

    console.print(
        f"[green]Bauer atualizado com sucesso; {len(preserved)} arquivo(s) preservado(s).[/green]"
    )
