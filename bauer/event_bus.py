"""EventBus — pub/sub in-process com persistência SQLite e triggers externos.

Suporta 3 tipos de fonte de evento:
- webhook: HTTP POST recebido pelo bauer serve → dispara handler registrado
- file_watch: mudança em arquivo/diretório → dispara handler
- schedule: cron-style (compatível com cronjob existente) → dispara handler

Os handlers tipicamente spawnam tasks no TaskQueue ou invocam o orchestrator.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path.home() / ".bauer" / "event_bus.db"


@dataclass
class Event:
    """Evento publicado no bus."""
    topic: str
    payload: Dict[str, Any]
    source: str = "internal"
    ts: float = field(default_factory=time.time)
    id: str = ""

    def __post_init__(self):
        if not self.id:
            import uuid
            self.id = str(uuid.uuid4())[:8]


@dataclass
class Subscription:
    topic: str
    handler: Callable[[Event], None]
    sub_id: str = ""

    def __post_init__(self):
        if not self.sub_id:
            import uuid
            self.sub_id = str(uuid.uuid4())[:8]


class EventBus:
    """Bus pub/sub in-process com persistência SQLite de eventos.

    Thread-safe. Handlers são chamados no thread do publisher por padrão;
    use `async_dispatch=True` para chamar em thread separado.
    """

    # Retenção: o `events` crescia para SEMPRE. Combinado com o `ORDER BY ts
    # DESC` sem índice em `ts`, o history() degradava linearmente até virar
    # full scan + sort de um arquivo de centenas de MB. O audit_trail.py já
    # resolvia isso; aqui não havia nada.
    _RETENTION_DAYS = 30
    _MAX_EVENTS = 200_000
    # Poda a cada N escritas — rodar em todo publish custaria um DELETE por
    # evento no caminho quente, que é justamente o que se quer evitar.
    _PRUNE_EVERY = 500

    def __init__(
        self,
        db_path: Optional[Path] = None,
        async_dispatch: bool = False,
        persist: bool = True,
        retention_days: int | None = None,
        max_events: int | None = None,
    ) -> None:
        self._db_path = db_path or _DEFAULT_DB
        self._async_dispatch = async_dispatch
        self._persist = persist
        self._subs: Dict[str, List[Subscription]] = {}
        self._lock = threading.Lock()
        self._retention_days = (
            self._RETENTION_DAYS if retention_days is None else retention_days
        )
        self._max_events = self._MAX_EVENTS if max_events is None else max_events

        # Conexão PERSISTENTE. Antes abria-se uma conexão nova a CADA evento
        # (connect + insert + commit + close no caminho quente do publish) e a
        # cada history(). check_same_thread=False + _db_lock porque o bus é
        # explicitamente multi-thread (async_dispatch spawna threads).
        self._conn: Optional[sqlite3.Connection] = None
        self._db_lock = threading.Lock()
        self._writes_since_prune = 0

        if self._persist:
            self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Conexão configurada. WAL + busy_timeout eram a diferença entre este
        store e todos os outros do projeto (audit_trail, checkpoint, kanban_db,
        goal_tracker): sem eles, escritores concorrentes batem em
        `database is locked`."""
        con = sqlite3.connect(str(self._db_path), timeout=10.0, check_same_thread=False)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def close(self) -> None:
        """Fecha a conexão persistente. Idempotente."""
        with self._db_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception as exc:
                    logger.debug("event_bus: erro ao fechar conexao: %s", exc)
                self._conn = None

    # ------------------------------------------------------------------
    # DB
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db_lock:
            self._conn = self._connect()
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    ts REAL NOT NULL
                )
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic)")
            # Índice em `ts`: history() faz `ORDER BY ts DESC LIMIT ?` e não
            # havia índice nenhum — full scan + sort da tabela inteira, com
            # custo crescendo junto com o histórico.
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC)")
            self._conn.commit()

    def _prune_locked(self) -> None:
        """Aplica retenção. Chamador já segura `_db_lock`."""
        if self._conn is None:
            return
        if self._retention_days > 0:
            corte = time.time() - (self._retention_days * 86400)
            self._conn.execute("DELETE FROM events WHERE ts < ?", (corte,))
        if self._max_events > 0:
            # Mantém os N mais recentes. Usa o índice de ts para o OFFSET.
            self._conn.execute(
                "DELETE FROM events WHERE id IN ("
                "  SELECT id FROM events ORDER BY ts DESC LIMIT -1 OFFSET ?"
                ")",
                (self._max_events,),
            )
        self._conn.commit()

    def prune(self) -> None:
        """Aplica retenção sob demanda (usado por manutenção e testes)."""
        if not self._persist:
            return
        with self._db_lock:
            try:
                self._prune_locked()
            except Exception as exc:
                logger.debug("event_bus: falha ao podar eventos: %s", exc)

    def _save_event(self, evt: Event) -> None:
        if not self._persist:
            return
        with self._db_lock:
            if self._conn is None:
                return
            try:
                self._conn.execute(
                    "INSERT OR IGNORE INTO events VALUES (?,?,?,?,?)",
                    (evt.id, evt.topic, evt.source, json.dumps(evt.payload), evt.ts),
                )
                self._conn.commit()
                self._writes_since_prune += 1
                if self._writes_since_prune >= self._PRUNE_EVERY:
                    self._writes_since_prune = 0
                    self._prune_locked()
            except Exception as exc:
                logger.debug("event_bus: falha ao persistir evento %s: %s", evt.id, exc)

    def history(self, topic: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Retorna histórico de eventos do banco."""
        if not self._persist:
            return []
        try:
            with self._db_lock:
                if self._conn is None:
                    return []
                if topic:
                    rows = self._conn.execute(
                        "SELECT id,topic,source,payload,ts FROM events WHERE topic=? ORDER BY ts DESC LIMIT ?",
                        (topic, limit),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        "SELECT id,topic,source,payload,ts FROM events ORDER BY ts DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
            return [
                {"id": r[0], "topic": r[1], "source": r[2],
                 "payload": json.loads(r[3]), "ts": r[4]}
                for r in rows
            ]
        except Exception as exc:
            logger.debug("event_bus: history error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Pub/sub
    # ------------------------------------------------------------------

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> str:
        """Registra handler para um tópico. Retorna sub_id para unsubscribe."""
        sub = Subscription(topic=topic, handler=handler)
        with self._lock:
            self._subs.setdefault(topic, []).append(sub)
        logger.debug("event_bus: subscribe topic=%s sub_id=%s", topic, sub.sub_id)
        return sub.sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove um handler pelo sub_id. Retorna True se removido."""
        with self._lock:
            for topic, subs in self._subs.items():
                before = len(subs)
                self._subs[topic] = [s for s in subs if s.sub_id != sub_id]
                if len(self._subs[topic]) < before:
                    logger.debug("event_bus: unsubscribe sub_id=%s", sub_id)
                    return True
        return False

    def publish(self, topic: str, payload: Dict[str, Any], source: str = "internal") -> Event:
        """Publica um evento. Chama todos os handlers registrados para o tópico."""
        evt = Event(topic=topic, payload=payload, source=source)
        self._save_event(evt)

        with self._lock:
            handlers = list(self._subs.get(topic, []))

        if not handlers:
            logger.debug("event_bus: nenhum subscriber para topic=%s", topic)
            return evt

        if self._async_dispatch:
            for sub in handlers:
                t = threading.Thread(
                    target=self._safe_call, args=(sub.handler, evt),
                    daemon=True, name=f"event-{topic}-{sub.sub_id}"
                )
                t.start()
        else:
            for sub in handlers:
                self._safe_call(sub.handler, evt)

        return evt

    def _safe_call(self, handler: Callable[[Event], None], evt: Event) -> None:
        try:
            handler(evt)
        except Exception as exc:
            logger.warning("event_bus: handler error topic=%s: %s", evt.topic, exc)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def topics(self) -> List[str]:
        """Lista tópicos com pelo menos um subscriber."""
        with self._lock:
            return [t for t, subs in self._subs.items() if subs]

    def subscriber_count(self, topic: str) -> int:
        with self._lock:
            return len(self._subs.get(topic, []))


# ---------------------------------------------------------------------------
# File watcher (polling, sem dependência extra)
# ---------------------------------------------------------------------------

class FileWatcher:
    """Monitora arquivos/diretórios e publica eventos no EventBus ao detectar mudanças.

    Usa polling (stat mtime) para máxima compatibilidade — sem dependência de watchdog.
    """

    def __init__(
        self,
        bus: EventBus,
        paths: List[str | Path],
        topic: str = "file.changed",
        interval_sec: float = 2.0,
    ) -> None:
        self._bus = bus
        self._paths = [Path(p) for p in paths]
        self._topic = topic
        self._interval = interval_sec
        self._mtimes: Dict[Path, float] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = False

    def start(self) -> None:
        """Inicia o watcher em background thread (daemon).

        O snapshot inicial de mtime é tirado AQUI, na thread chamadora, antes
        de a thread subir. Quando ele ficava dentro de `_loop`, `start()`
        retornava com o watcher ainda desarmado: se a thread demorasse a ganhar
        CPU (runner de CI carregado), o baseline era colhido DEPOIS de uma
        escrita subsequente, o mtime já registrado batia com o atual e a
        mudança nunca era detectada — silenciosamente, sem erro. Agora
        `start()` retornar significa "armado".
        """
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._snapshot()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="file-watcher"
        )
        self._thread.start()

    def _snapshot(self) -> None:
        """Registra o mtime atual de cada path observado."""
        for p in self._paths:
            try:
                self._mtimes[p] = p.stat().st_mtime
            except OSError:
                # FileNotFoundError e amigos (permissao, path invalido): trata
                # como "nao existe" — a primeira aparicao do arquivo vira mudanca.
                self._mtimes[p] = 0.0

    def stop(self) -> None:
        self._stop = True

    def _loop(self) -> None:
        # Snapshot inicial ja foi tirado em start() (ver docstring de la).
        while not self._stop:
            time.sleep(self._interval)
            for p in self._paths:
                try:
                    mtime = p.stat().st_mtime
                except OSError:
                    mtime = 0.0

                prev = self._mtimes.get(p, 0.0)
                if mtime != prev:
                    self._mtimes[p] = mtime
                    self._bus.publish(
                        self._topic,
                        {"path": str(p), "prev_mtime": prev, "mtime": mtime},
                        source="file_watcher",
                    )


# ---------------------------------------------------------------------------
# Webhook bridge (integra com bauer serve via callback)
# ---------------------------------------------------------------------------

class WebhookHandler:
    """Converte requisições HTTP POST em eventos do EventBus.

    O bauer serve chama `handle_request(topic, payload)` ao receber
    POST /webhook/{topic}. O mapeamento de rota é feito no servidor.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    def handle_request(self, topic: str, payload: Dict[str, Any]) -> Event:
        """Chamado pelo servidor HTTP ao receber webhook."""
        return self._bus.publish(topic, payload, source="webhook")


# ---------------------------------------------------------------------------
# Singleton global
# ---------------------------------------------------------------------------

_global_bus: Optional[EventBus] = None


def get_event_bus(db_path: Optional[Path] = None) -> EventBus:
    """Retorna o EventBus global singleton (lazy init)."""
    global _global_bus
    if _global_bus is None:
        _global_bus = EventBus(db_path=db_path, async_dispatch=True)
    return _global_bus


def reset_event_bus() -> None:
    """Reseta o singleton (útil em testes)."""
    global _global_bus
    _global_bus = None
