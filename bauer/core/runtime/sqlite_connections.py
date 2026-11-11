"""Conexões SQLite reutilizadas pelos bancos do runtime (sprint 76, plano 068).

Abrir e fechar uma conexão por operação faz, em WAL, cada fechamento da
última conexão do arquivo rodar checkpoint e apagar ``-wal``/``-shm``; a
próxima abertura reconstrói os dois sob lock exclusivo, e quem abre nesse
intervalo espera no busy handler. No Windows (fsync e remoção de arquivo
lentos, antivírus segurando arquivo recém-criado) essas esperas chegaram ao
``busy_timeout`` inteiro (run 42935274304).

Cada arquivo ganha um ``SqliteConnectionPool`` por processo, com:

- **uma conexão por thread**: ``sqlite3.Connection`` nunca é usada por duas
  threads ao mesmo tempo; leitores continuam em conexões próprias e não veem
  a transação aberta de outra thread;
- **uma conexão âncora** que fica aberta sem uso: enquanto ela existe, o
  fechamento de qualquer outra conexão não é o último, então o ``-wal`` e o
  ``-shm`` nunca são apagados e recriados durante a vida do processo.

``close()`` fecha tudo (o pool continua utilizável e reabre sob demanda); o
fechamento do processo (``atexit``) e o filho de ``fork`` são tratados aqui.
"""

from __future__ import annotations

import atexit
import os
import sqlite3
import threading
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ...logging_config import log_suppressed

Configure = Callable[[sqlite3.Connection], None]

_REGISTRY_LOCK = threading.Lock()
_POOLS: dict[str, SqliteConnectionPool] = {}
# Conexões herdadas por um filho de fork: nunca são usadas nem fechadas no
# filho (o SQLite proíbe levar conexão aberta através do fork; fechar poderia
# mexer em locks e no -shm do pai). Ficam referenciadas só para o coletor não
# fechá-las.
_INHERITED: list[sqlite3.Connection] = []


@dataclass
class _Entry:
    thread: weakref.ref[threading.Thread]
    conn: sqlite3.Connection
    generation: int
    depth: int = 0
    discard: bool = False


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_dev, stat.st_ino


def _close_quietly(conn: sqlite3.Connection, context: str) -> None:
    try:
        conn.close()
    except sqlite3.Error as exc:
        log_suppressed(context, exc, logger_name="bauer.runtime.sqlite")


class SqliteConnectionPool:
    """Conexões reutilizadas de um arquivo SQLite, uma por thread, mais a âncora."""

    def __init__(self, path: Path, configure: Configure, *, timeout: float = 5.0):
        self.path = path
        self._configure = configure
        self._timeout = timeout
        self._lock = threading.Lock()
        self._entries: dict[int, _Entry] = {}
        self._anchor: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        self._generation = 0
        self._pid = os.getpid()
        #: Conexões abertas por este pool (âncora incluída); usado em testes e
        #: na medição da sprint 76.
        self.opened = 0

    def _open(self) -> sqlite3.Connection:
        # check_same_thread=False só para que close() (teardown, atexit) possa
        # fechar a conexão de outra thread; o uso normal é sempre da thread dona.
        conn = sqlite3.connect(
            str(self.path), isolation_level=None, timeout=self._timeout,
            check_same_thread=False,
        )
        try:
            self._configure(conn)
        except BaseException:
            conn.close()
            raise
        with self._lock:
            self.opened += 1
        return conn

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Empresta a conexão da thread atual (fora de transação ao devolver)."""
        if self._pid != os.getpid():
            self._forget_after_fork()
        thread = threading.current_thread()
        ident = threading.get_ident()
        with self._lock:
            self._check_identity_locked()
            entry = self._entries.get(ident)
            if entry is not None and (entry.thread() is not thread
                                      or entry.generation != self._generation):
                # ident reaproveitado por outra thread, ou close() aconteceu
                # enquanto a conexão estava ociosa nesta thread.
                del self._entries[ident]
                _close_quietly(entry.conn, "sqlite_pool.stale_entry")
                entry = None
            nested = entry is not None and entry.depth > 0
            if entry is not None and not nested:
                entry.depth = 1
            generation = self._generation
            need_anchor = self._anchor is None
        if nested:
            # Uso reentrante na mesma thread (ex.: leitura dentro de uma
            # transação): conexão própria, como antes da sprint 76, para não
            # enxergar nem misturar a transação aberta da conexão emprestada.
            temporary = self._open()
            try:
                yield temporary
            finally:
                temporary.close()
            return
        if need_anchor:
            self._ensure_anchor(generation)
        if entry is None:
            conn = self._open()
            entry = _Entry(weakref.ref(thread), conn, generation, depth=1)
            with self._lock:
                self._prune_dead_locked()
                if generation == self._generation:
                    self._entries[ident] = entry
                else:
                    entry.discard = True
        try:
            yield entry.conn
        finally:
            if entry.conn.in_transaction:
                # Um caller que abriu transação e saiu sem fechar não pode
                # vazar o lock de escrita para o próximo uso da conexão.
                try:
                    entry.conn.execute("ROLLBACK")
                except sqlite3.Error as exc:
                    log_suppressed("sqlite_pool.rollback", exc,
                                   logger_name="bauer.runtime.sqlite")
            with self._lock:
                entry.depth = 0
                discard = entry.discard
            if discard:
                _close_quietly(entry.conn, "sqlite_pool.discard")

    def _ensure_anchor(self, generation: int) -> None:
        anchor = self._open()
        try:
            # Ler o schema faz a âncora abrir o banco e mapear o -shm; é isso
            # que segura o -wal enquanto ela existir.
            anchor.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except BaseException:
            anchor.close()
            raise
        with self._lock:
            if self._anchor is None and generation == self._generation:
                self._anchor = anchor
                self._identity = _file_identity(self.path)
                return
        anchor.close()

    def _check_identity_locked(self) -> None:
        """Arquivo apagado ou substituído por fora: as conexões apontam para o antigo."""
        if self._anchor is None:
            return
        if _file_identity(self.path) != self._identity:
            self._close_locked()

    def _prune_dead_locked(self) -> None:
        for ident, entry in list(self._entries.items()):
            thread = entry.thread()
            if thread is None or not thread.is_alive():
                del self._entries[ident]
                if entry.depth == 0:
                    _close_quietly(entry.conn, "sqlite_pool.dead_thread")
                else:
                    entry.discard = True

    def _close_locked(self) -> None:
        self._generation += 1
        entries = list(self._entries.values())
        self._entries.clear()
        for entry in entries:
            if entry.depth == 0:
                _close_quietly(entry.conn, "sqlite_pool.close")
            else:
                entry.discard = True  # fecha quando a thread dona devolver
        anchor, self._anchor = self._anchor, None
        self._identity = None
        if anchor is not None:
            # A âncora fecha por último: assim o checkpoint de fechamento roda
            # uma vez só e o -wal/-shm somem do disco.
            _close_quietly(anchor, "sqlite_pool.close_anchor")

    def close(self) -> None:
        """Fecha a âncora e as conexões ociosas; as emprestadas fecham ao devolver."""
        with self._lock:
            self._close_locked()

    def open_connections(self) -> int:
        with self._lock:
            return len(self._entries) + (1 if self._anchor is not None else 0)

    def _forget_after_fork(self) -> None:
        with self._lock:
            if self._pid == os.getpid():
                return
            _INHERITED.extend(entry.conn for entry in self._entries.values())
            if self._anchor is not None:
                _INHERITED.append(self._anchor)
            self._entries.clear()
            self._anchor = None
            self._identity = None
            self._generation += 1
            self._pid = os.getpid()


def _key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def pool_for(path: Path, configure: Configure, *, timeout: float = 5.0) -> SqliteConnectionPool:
    """Pool do processo para ``path`` (o primeiro ``configure`` registrado vale)."""
    resolved = path.resolve()
    key = os.path.normcase(str(resolved))
    with _REGISTRY_LOCK:
        pool = _POOLS.get(key)
        if pool is None:
            pool = SqliteConnectionPool(resolved, configure, timeout=timeout)
            _POOLS[key] = pool
        return pool


def close_pool(path: Path) -> None:
    """Fecha as conexões deste processo para ``path``, se houver."""
    with _REGISTRY_LOCK:
        pool = _POOLS.get(_key(path))
    if pool is not None:
        pool.close()


def close_all_pools() -> None:
    """Fecha todas as conexões SQLite reutilizadas do processo (atexit, testes)."""
    with _REGISTRY_LOCK:
        pools = list(_POOLS.values())
    for pool in pools:
        pool.close()


def _after_fork_in_child() -> None:
    # Locks podem ter sido copiados no estado "adquirido" por uma thread que
    # não existe no filho; o registro é recriado vazio.
    global _REGISTRY_LOCK
    _REGISTRY_LOCK = threading.Lock()
    for pool in list(_POOLS.values()):
        pool._lock = threading.Lock()
        pool._forget_after_fork()


atexit.register(close_all_pools)
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_in_child)
