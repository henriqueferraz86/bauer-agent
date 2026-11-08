"""Leitura e gravação confinadas de arquivos internos (sprint 59: WEB-01, T05).

Stores internos (sessões JSONL, ``.bauer_memory.json``, ``.bauer_skills.json``,
``.bauer_cronjobs.json``) são arquivos regulares do próprio Bauer, diretamente
sob uma raiz conhecida. Comportamento definido:

* o diretório pai, depois de resolvido, precisa ficar dentro da raiz resolvida
  (a raiz em si pode ser um symlink — é resolvida uma vez);
* o componente final **nunca** é seguido: se for symlink (para dentro ou para
  fora da raiz), leitura e gravação falham com :class:`ConfinementError`;
* leitura abre com ``O_NOFOLLOW`` quando o SO oferece (POSIX), então a
  verificação e a abertura são a mesma syscall; sem ``O_NOFOLLOW`` (Windows)
  compara o ``lstat`` com o ``fstat`` do descritor aberto;
* gravação escreve num temporário exclusivo no mesmo diretório e faz
  ``os.replace`` — que substitui a entrada do diretório e nunca escreve no
  alvo de um symlink, mesmo que ele apareça entre a checagem e a troca;
* arquivo com mais de um hardlink é recusado na leitura (pode ser alias de
  um arquivo externo).
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from pathlib import Path


class ConfinementError(ValueError):
    """Caminho interno fora da raiz, symlink ou arquivo não regular."""


def _confined_target(path: str | Path, root: str | Path) -> Path:
    root_resolved = Path(root).resolve()
    candidate = Path(path)
    if not candidate.name or candidate.name in (".", ".."):
        raise ConfinementError(f"Caminho interno inválido: {candidate}")
    parent = candidate.parent.resolve()
    if parent != root_resolved and not parent.is_relative_to(root_resolved):
        raise ConfinementError(f"Caminho interno fora da raiz permitida: {candidate}")
    return parent / candidate.name


def read_text(path: str | Path, root: str | Path, *, errors: str = "strict") -> str | None:
    """Lê um arquivo interno sem seguir symlink; ``None`` quando não existe."""
    target = _confined_target(path, root)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    # O_NONBLOCK: um FIFO plantado no lugar do store não trava a abertura.
    flags = os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    before: os.stat_result | None = None
    try:
        if not nofollow:
            before = os.lstat(target)
            if stat.S_ISLNK(before.st_mode):
                raise ConfinementError(f"Symlink recusado em arquivo interno: {target}")
        fd = os.open(target, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ConfinementError(f"Symlink recusado em arquivo interno: {target}") from exc
        raise
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ConfinementError(f"Arquivo interno não é regular: {target}")
        if before is not None and not os.path.samestat(before, opened):
            raise ConfinementError(f"Arquivo interno trocado durante a abertura: {target}")
        if opened.st_nlink > 1:
            raise ConfinementError(f"Arquivo interno com hardlink recusado: {target}")
        return handle.read().decode("utf-8", errors=errors)


def write_text_atomic(path: str | Path, text: str, root: str | Path) -> Path:
    """Grava atomicamente um arquivo interno sem nunca escrever via symlink."""
    target = _confined_target(path, root)
    try:
        if stat.S_ISLNK(os.lstat(target).st_mode):
            raise ConfinementError(f"Symlink recusado em arquivo interno: {target}")
    except FileNotFoundError:
        pass
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def unlink(path: str | Path, root: str | Path) -> bool:
    """Remove a entrada interna (um symlink é removido, nunca o alvo)."""
    target = _confined_target(path, root)
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    return True


def exists(path: str | Path, root: str | Path) -> bool:
    """Existe como arquivo interno regular (symlink não conta)."""
    target = _confined_target(path, root)
    try:
        return stat.S_ISREG(os.lstat(target).st_mode)
    except FileNotFoundError:
        return False
