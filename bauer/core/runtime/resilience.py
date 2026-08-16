"""Runtime resilience: worker heartbeats, kill switch and recovery."""

from __future__ import annotations

import contextlib
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .run_manager import RECOVERABLE_RUN_STATUSES, RunManager
from .state_store import JsonlStateStore

#: Intervalo entre batidas do pulso de um run. Tem de ser MUITO menor que o
#: `max_age_s` do recovery (900s default) — a margem é o que impede um atraso de
#: escalonamento de virar "run travado".
INTERVALO_PULSO_PADRAO_S = 30.0


@dataclass(slots=True)
class WorkerHeartbeat:
    id: str
    status: str
    pid: int
    started_at: str
    last_seen_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkerRegistry:
    def __init__(self, *, root: str | Path = "memory/runtime", store: JsonlStateStore | None = None):
        self.store = store or JsonlStateStore(root)

    def heartbeat(self, worker_id: str, *, status: str = "online", metadata: dict[str, Any] | None = None) -> WorkerHeartbeat:
        now = _now_iso()
        current = self.get(worker_id)
        record = WorkerHeartbeat(
            id=worker_id,
            status=status,
            pid=os.getpid(),
            started_at=current.started_at if current else now,
            last_seen_at=now,
            metadata=metadata or (current.metadata if current else {}),
        )
        self.store.upsert("workers", record)
        return record

    def get(self, worker_id: str) -> WorkerHeartbeat | None:
        data = self.store.latest("workers", worker_id)
        return WorkerHeartbeat(**data) if data else None

    def list(self, *, stale_after_s: int = 90) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
        workers = [WorkerHeartbeat(**record) for record in self.store.list_latest("workers")]
        result: list[dict[str, Any]] = []
        for worker in workers:
            status = worker.status
            if _parse_datetime(worker.last_seen_at) < cutoff:
                status = "offline"
            result.append({**_worker_to_dict(worker), "computed_status": status})
        return result


@dataclass(slots=True)
class RunLiveness:
    """Sinal de vida de um run em execução. ``id`` é o run_id."""

    id: str
    last_seen_at: str
    pid: int


class RunHeartbeat:
    """Pulso de vida de um run — a diferença entre "parado" e "trabalhando".

    O recovery julga um run travado pela idade de ``updated_at``, que só se move
    em MUDANÇA DE ESTADO. Um run pode passar meia hora legitimamente dentro de
    ``running`` (turno longo) ou de ``evaluating`` (AcceptanceGate 600s +
    TestsGate 600s, ambos ligados por default, já passam dos 900s sozinhos) sem
    nenhuma transição. Idade sem pulso mede SILÊNCIO, não travamento — e o
    desfecho era o pior possível: o recovery marcava `failed` um run vivo, que
    depois escrevia `completed` por cima.

    Coleção PRÓPRIA, não a de `runs`: ``update_run`` reescreve o Run inteiro
    (com o ``input``) e publica evento de status — bater a cada 30s ali inflaria
    o JSONL de auditoria e emitiria ``run.started`` em looping.

    O ``pid`` é gravado para diagnóstico e NÃO entra na decisão: PID é reciclado
    pelo SO e não significa nada entre máquinas ou containers. Quem decide é a
    frescura da última batida — se o processo morre, a thread morre junto, o
    pulso envelhece e o recovery recupera, que é exatamente o certo.
    """

    COLECAO = "run_heartbeats"

    def __init__(self, *, root: str | Path = "memory/runtime",
                 store: JsonlStateStore | None = None):
        self.store = store or JsonlStateStore(root)

    def bater(self, run_id: str) -> RunLiveness:
        record = RunLiveness(id=run_id, last_seen_at=_now_iso(), pid=os.getpid())
        self.store.upsert(self.COLECAO, record)
        return record

    def ultimo(self, run_id: str) -> RunLiveness | None:
        data = self.store.latest(self.COLECAO, run_id)
        return RunLiveness(**data) if data else None

    @contextlib.contextmanager
    def pulso(self, run_id: str, *, intervalo_s: float = INTERVALO_PULSO_PADRAO_S):
        """Bate o pulso do run enquanto o bloco roda, numa thread daemon.

        Thread porque o Kernel está BLOQUEADO dentro de código que não é dele
        (``executor(payload)``, ``adapter.run_agent``, os gates) — não há ponto
        de retorno onde ele pudesse bater sozinho.

        NÃO bate na entrada: quem acabou de transicionar para ``running`` já tem
        ``updated_at`` fresco. Assim um run mais curto que o intervalo não
        escreve nada, e o arquivo de pulsos só existe para runs que de fato
        duram. ``intervalo_s <= 0`` desliga (útil em teste).
        """
        if intervalo_s <= 0:
            yield
            return
        parar = threading.Event()

        def _laco() -> None:
            while not parar.wait(intervalo_s):
                try:
                    self.bater(run_id)
                except Exception as exc:  # noqa: BLE001 — pulso nunca derruba o run
                    from ...logging_config import log_suppressed
                    log_suppressed("runtime.pulso", exc)
                    return

        thread = threading.Thread(target=_laco, name=f"pulso-{run_id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            parar.set()
            thread.join(timeout=2.0)


class RuntimeControl:
    def __init__(self, *, root: str | Path = "memory/runtime", store: JsonlStateStore | None = None):
        self.store = store or JsonlStateStore(root)

    def set_kill_switch(self, enabled: bool) -> dict[str, Any]:
        record = {
            "id": "kill_switch",
            "enabled": bool(enabled),
            "updated_at": _now_iso(),
        }
        self.store.upsert("runtime_control", record)
        return record

    def kill_switch_enabled(self) -> bool:
        record = self.store.latest("runtime_control", "kill_switch")
        return bool(record and record.get("enabled"))


class RuntimeRecovery:
    def __init__(self, *, root: str | Path = "memory/runtime", store: JsonlStateStore | None = None):
        self.store = store or JsonlStateStore(root)
        self.run_manager = RunManager(store=self.store)
        self.heartbeats = RunHeartbeat(store=self.store)

    def recover_stuck_runs(self, *, max_age_s: int = 900) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_s)
        recovered: list[dict[str, Any]] = []
        # Pulsos lidos UMA vez: `ultimo()` varre o arquivo inteiro a cada
        # chamada, e chamá-lo por run faria o recovery custar
        # O(runs × batidas) — com batida a cada 30s por run vivo, o arquivo é a
        # coleção que mais cresce no store.
        pulsos = {str(p.get("id")): p for p in self.store.list_latest(RunHeartbeat.COLECAO)}
        for run in self.run_manager.list_runs():
            # Só recupera estados genuinamente "travados" — pula terminais E
            # estados de espera intencional (waiting_approval/paused), que não
            # devem virar failed por idade.
            if run.status not in RECOVERABLE_RUN_STATUSES:
                continue
            marker = run.updated_at or run.started_at
            if _parse_datetime(marker) > cutoff:
                continue
            # Sem transição há muito tempo NÃO é o mesmo que travado: o run pode
            # estar dentro de um turno longo ou dos gates. Quem tem pulso fresco
            # está vivo — e matar um run vivo é pior que deixar um morto na
            # fila, porque o trabalho já feito vira `failed` e o Kernel escreve
            # `completed` por cima depois. Run sem pulso nenhum cai na regra
            # antiga (idade pura): caminhos que não passam pelo Kernel e runs
            # gravados antes disto continuam recuperáveis.
            vivo = pulsos.get(run.id)
            if vivo is not None and _parse_datetime(str(vivo["last_seen_at"])) > cutoff:
                continue
            mensagem = f"runtime recovery: run stuck for more than {max_age_s}s"
            # `fail_run_se_nao_terminal`: entre o list_runs() acima e esta linha
            # o dono do run pode ter concluído. Um fail_run cru apagaria o
            # desfecho — mesma corrida que o /stream já pagou.
            failed = self.run_manager.fail_run_se_nao_terminal(run.id, mensagem)
            if failed.error != mensagem:
                continue  # outro escritor chegou ao terminal primeiro
            recovered.append({"run_id": failed.id, "status": failed.status, "error": failed.error})
        return recovered


def _worker_to_dict(worker: WorkerHeartbeat) -> dict[str, Any]:
    return {
        "id": worker.id,
        "status": worker.status,
        "pid": worker.pid,
        "started_at": worker.started_at,
        "last_seen_at": worker.last_seen_at,
        "metadata": dict(worker.metadata),
    }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
