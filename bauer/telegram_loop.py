"""`/loop` autônomo no Telegram (sprint 44, plano 061 Etapa 5).

Reusa o MESMO motor que `bauer/server.py:_start_loop_impl` usa para o `/loop`
da web (`serve_loop.resolve_loop_limits`/`run_loop_rounds` + `core.kernel.
admit`/`continue_governed`) — admissão com custódia de duas fases, nunca o
padrão de turno único de `AgentBackend._execute_turn`.

Isolado do resto do bridge (não dentro de `telegram_bridge.py`) por dois
motivos: `telegram_bridge.py` já é grande, e esta sprint roda em paralelo a
outra sessão editando esse mesmo arquivo — menos superfície de merge.

Fechamento do gap de ordenação (ver SPEC "Problema"): o registro é persistido
em disco ANTES de qualquer resposta "iniciado" ser enviada, e antes de
`TelegramBridge._handle_update` devolver — mesmo que a RODADA em si rode numa
thread separada depois. Um crash exatamente depois da resposta não perde o
vínculo update_id -> run_id.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.telegram_loop")

#: campos aceitos por TelegramLoopRecord — usado para filtrar dict legado/
#: corrompido sem explodir em KeyError (from_dict).
_GOAL_TRUNCATE_CHARS = 400
_RECORD_TTL_DAYS = 7

#: estados que ainda esperam a thread do loop terminar (ou nem começaram a
#: executar ainda, mas já têm um run admitido no Kernel). `received` (
#: waiting_approval) fica de fora de propósito: nenhuma thread chegou a
#: existir para ele, então reconciliação não tem o que "detectar morto".
_RECONCILABLE_STATES = {"admitted", "running"}
_TERMINAL_STATES = {"completed", "failed", "stopped", "limit"}

#: tradução stop_reason -> estado do registro (mesma tabela que
#: `_start_loop_impl` usa para o LoopState da web).
_STATE_FOR_STOP_REASON = {
    "completed": "completed",
    "budget_exhausted": "limit",
    "max_rounds": "limit",
    "cancelled": "stopped",
    "kill_switch": "stopped",
}

#: tradução status real do Run (visto na reconciliação) -> estado do registro.
_STATE_FOR_RUN_STATUS = {
    "completed": "completed",
    "cancelled": "stopped",
    "failed": "failed",
}

#: tradução aproximada estado do registro -> stop_reason, usada só para
#: reconstruir o relatório de um registro TERMINAL-mas-não-entregue (ver
#: `list_terminal_undelivered`/`_redeliver_terminal_record`): o `stop_reason`
#: exato só existia nas variáveis locais da thread que morreu antes de
#: entregar; "stopped" e "limit" são ambíguos (cancelled/kill_switch,
#: budget_exhausted/max_rounds) — a melhor aproximação disponível é escolhida,
#: nunca um texto genérico de sucesso.
_STOP_REASON_FOR_STATE = {
    "completed": "completed",
    "limit": "budget_exhausted",
    "stopped": "cancelled",
    "failed": "failed",
}


@dataclass
class TelegramLoopRecord:
    """Só o necessário para retomar — sem transcript (vive no store de sessão)."""

    update_id: int
    chat_id: str
    session_id: str          # f"tg:{chat_id}" — identidade de entrega
    run_id: str | None
    workspace: str
    goal: str
    state: str                # received|admitted|running|interrupted|completed|failed|stopped|limit
    limits: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None
    delivered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TelegramLoopRecord":
        names = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in names})


class TelegramLoopStore:
    """Um arquivo JSON por processo (lista de registros), 0600, ao lado do
    offset. Thread-safe dentro do processo; não precisa ser entre processos —
    o gateway Telegram roda uma única instância por token (ver Telegram 409)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load_all_unlocked(self) -> list[TelegramLoopRecord]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except (OSError, ValueError):
            logger.warning("telegram_loops.json ilegível — tratando como vazio", exc_info=True)
            return []
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw:
            if isinstance(item, dict):
                try:
                    out.append(TelegramLoopRecord.from_dict(item))
                except TypeError:
                    continue
        return out

    def _save_all_unlocked(self, records: list[TelegramLoopRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2)
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            logger.debug("chmod do tmp de telegram_loops.json falhou", exc_info=True)
        tmp.replace(self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            logger.debug("chmod de telegram_loops.json falhou", exc_info=True)

    def put(self, record: TelegramLoopRecord) -> None:
        """Escrita SÍNCRONA — o caller (`start_loop`) depende de o arquivo
        estar em disco antes de responder "iniciado" (ver módulo docstring)."""
        with self._lock:
            records = [r for r in self._load_all_unlocked() if r.update_id != record.update_id]
            records.append(record)
            self._save_all_unlocked(records)

    def update(self, update_id: int, **changes: Any) -> TelegramLoopRecord | None:
        with self._lock:
            records = self._load_all_unlocked()
            updated = None
            for idx, rec in enumerate(records):
                if rec.update_id == update_id:
                    data = {**rec.to_dict(), **changes}
                    updated = TelegramLoopRecord.from_dict(data)
                    records[idx] = updated
                    break
            if updated is not None:
                self._save_all_unlocked(records)
            return updated

    def get_by_update_id(self, update_id: int) -> TelegramLoopRecord | None:
        with self._lock:
            for rec in self._load_all_unlocked():
                if rec.update_id == update_id:
                    return rec
        return None

    def active_for_chat(self, chat_id: str) -> list[TelegramLoopRecord]:
        with self._lock:
            return [r for r in self._load_all_unlocked()
                    if r.chat_id == chat_id and r.state in _RECONCILABLE_STATES]

    def list_non_terminal(self) -> list[TelegramLoopRecord]:
        with self._lock:
            return [r for r in self._load_all_unlocked() if r.state in _RECONCILABLE_STATES]

    def list_terminal_undelivered(self) -> list[TelegramLoopRecord]:
        """Registros já TERMINAIS cuja entrega nunca foi confirmada.

        Cobre o crash entre `store.update(..., state=<terminal>)` e a
        confirmação de `deliver_with_retry` (ou o esgotamento dos retries) —
        sem isto o relatório final fica perdido para sempre, já que
        `list_non_terminal` nunca mais revisita um registro terminal."""
        with self._lock:
            return [r for r in self._load_all_unlocked()
                    if r.state in _TERMINAL_STATES and not r.delivered]

    def purge_old(self, *, now: datetime | None = None) -> int:
        """Remove registros TERMINAIS com `finished_at` há mais de 7 dias."""
        cutoff = (now or datetime.now(UTC)) - timedelta(days=_RECORD_TTL_DAYS)
        with self._lock:
            records = self._load_all_unlocked()
            kept = []
            removed = 0
            for rec in records:
                if rec.state in _TERMINAL_STATES and rec.finished_at:
                    try:
                        finished = datetime.fromisoformat(rec.finished_at)
                    except ValueError:
                        kept.append(rec)
                        continue
                    if finished < cutoff:
                        removed += 1
                        continue
                kept.append(rec)
            if removed:
                self._save_all_unlocked(kept)
        return removed


def _store_for(backend: Any) -> TelegramLoopStore:
    """Uma TelegramLoopStore por `AgentBackend`, cacheada na instância — mesmo
    diretório de estado do offset do bridge (`sessions_dir` é compartilhado
    pelo processo do gateway)."""
    store = getattr(backend, "_telegram_loop_store", None)
    if store is not None:
        return store
    path = Path(backend.sessions_dir).parent / ".bauer_gateway" / "telegram_loops.json"
    store = TelegramLoopStore(path)
    backend._telegram_loop_store = store
    return store


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _status_text_for(record: TelegramLoopRecord) -> str:
    """Resposta para um `update_id` repetido (replay do Telegram) — nunca
    readmite, só descreve o estado já conhecido."""
    if record.state == "received":
        return (f"Este pedido já está aguardando aprovação (run_id={record.run_id}). "
                "Nenhum novo loop foi iniciado.")
    if record.state in ("admitted", "running"):
        return (f"Este /loop já está em andamento (run_id={record.run_id}). "
                f"Use /cancelar {record.run_id} para interromper.")
    return (f"Este /loop já terminou (run_id={record.run_id}, estado={record.state}). "
            "Nenhum novo loop foi iniciado para esta mensagem.")


def _usage_text() -> str:
    return ("Use /loop <objetivo> para iniciar um trabalho autônomo governado "
            "(o agente trabalha sozinho até concluir, atingir um limite de "
            "segurança ou ser cancelado).")


def start_loop(backend: Any, msg: Any, update_id: int, goal: str) -> str:
    """Admite e inicia um `/loop`, ou devolve por que não pôde.

    Contrato (ver ARCHITECTURE.md): nenhuma resposta "iniciado" sai antes do
    registro estar em disco; nenhum registro é persistido para `waiting_
    approval`/`deny`/erro de admissão (não há nada para retomar); replay do
    mesmo `update_id` nunca readmite.
    """
    from .channel_projects import private_telegram

    store = _store_for(backend)
    existing = store.get_by_update_id(update_id)
    if existing is not None:
        return _status_text_for(existing)

    goal = (goal or "").strip()
    if not goal:
        return _usage_text()
    if not private_telegram(msg):
        return "Use /loop em uma conversa privada autorizada com o bot."

    # Mesma inicialização lazy que `AgentBackend.process()` faz — os branches
    # síncronos (`/cancelar`, `/projetos`) nunca precisaram de `self._router`
    # diretamente; `/loop` precisa (via `_effective_router()`/`_resolver_
    # kernel()`), então garante a inicialização antes de entrar no project_scope.
    if not backend.is_ready:
        try:
            backend.initialize()
        except Exception:
            logger.warning("Backend não inicializou para /loop", exc_info=True)
            return "⚠️ O agent não conseguiu inicializar. Verifique o config.yaml e rode `bauer doctor`."
    try:
        backend._maybe_reload()
    except Exception:  # noqa: BLE001 — hot-reload é best-effort
        logger.debug("_maybe_reload falhou antes do /loop", exc_info=True)

    chat_id = msg.chat_id
    delivery_session_id = f"tg:{chat_id}"

    lock = None
    release_lock = True  # só vira False quando a thread assume a custódia
    try:
        with backend.project_scope(msg):
            router = backend._effective_router()
            workspace = Path(router.workspace).resolve()

            try:
                kernel = backend._resolver_kernel()
            except Exception as exc:  # noqa: BLE001 — KernelWiringError inclusa
                logger.warning("Kernel indisponível para /loop: %s", exc)
                return ("⚠️ O Kernel governado está configurado mas não consegui "
                        "inicializá-lo; /loop não pode iniciar sem admissão governada.")
            if kernel is None:
                return ("⚠️ O Kernel governado não está habilitado para este "
                        "workspace (kernel.enabled); /loop não pode iniciar sem "
                        "admissão governada.")

            key = backend.session_key(msg)
            # Mesma ordem de `AgentBackend.process()`: restaura o override de
            # modelo do projeto ANTES de `_get_session` construir o
            # ContextManager pela primeira vez (ele lê `_model_overrides`/
            # `_session_overrides` nessa construção).
            backend._restore_project_model(key)
            ctx, lock = backend._get_session(key)
            if not lock.acquire(blocking=False):
                lock = None  # não é nosso para liberar — nunca foi adquirido
                return ("⏳ Esta conversa ainda está ocupada com outra tarefa. "
                        "Aguarde terminar (ou /cancelar) antes de iniciar um /loop.")

            from .config_loader import LoopSection, load_config
            loop_section = LoopSection()
            try:
                loop_section = load_config(backend.config_path).loop
            except Exception:  # noqa: BLE001 — defaults do LoopSection seguram
                logger.debug("loop config load falhou — usando defaults", exc_info=True)

            from .serve_loop import resolve_loop_limits
            try:
                limits = resolve_loop_limits(loop_section, {}, clamp_to_config=True)
            except ValueError as exc:
                return f"⚠️ Limites de /loop inválidos na configuração: {exc}"

            from .core.kernel import KernelRequest
            run, early = kernel.admit(KernelRequest(
                task=goal, session_id=delivery_session_id,
                agent_id="channel.telegram.loop",
                input={"endpoint": "canal:telegram.loop",
                       "workspace": str(workspace), "limits": limits.to_dict()},
                # /loop do Telegram: o usuário dispara e sai, igual ao /loop
                # da web — ver KernelRequest.autonomous.
                autonomous=True,
            ))

            if early is not None:
                if early.status == "waiting_approval":
                    # Nenhuma thread vai assumir este run — persiste para
                    # idempotência do replay, mas a sessão fica livre: não há
                    # trabalho em andamento até alguém aprovar.
                    store.put(TelegramLoopRecord(
                        update_id=update_id, chat_id=chat_id,
                        session_id=delivery_session_id, run_id=early.run_id,
                        workspace=str(workspace), goal=goal[:_GOAL_TRUNCATE_CHARS],
                        state="received", limits=limits.to_dict(),
                    ))
                    return (f"⏳ Esta ação precisa de aprovação antes de iniciar "
                            f"(approval_id={early.approval_id}, run_id={early.run_id}). "
                            f"{early.policy_reason or ''}".strip())
                if early.policy_action == "deny":
                    return f"⛔ {early.error or early.policy_reason or 'Bloqueado pela política.'}"
                return f"⚠️ Não consegui iniciar o loop: {early.error or 'execução bloqueada.'}"

            # Persistência SÍNCRONA, ANTES de qualquer resposta "iniciado" —
            # fecha o gap de ordenação descrito no SPEC/módulo docstring.
            record = TelegramLoopRecord(
                update_id=update_id, chat_id=chat_id, session_id=delivery_session_id,
                run_id=run.id, workspace=str(workspace), goal=goal[:_GOAL_TRUNCATE_CHARS],
                state="admitted", limits=limits.to_dict(),
            )
            store.put(record)

            if key in backend._session_overrides:
                client, model, _provider = backend._session_overrides[key]
            else:
                client = backend._client
                model = backend._model_overrides.get(key, backend._model_name)
            fallback_clients = list(backend._fallback_clients)
            release_lock = False  # a thread do loop assume a custódia do lock
    except Exception:  # noqa: BLE001 — nunca traceback no chat
        logger.exception("Falha inesperada admitindo /loop no Telegram")
        return "⚠️ Não consegui iniciar o /loop. Nenhuma execução foi admitida."
    finally:
        if release_lock and lock is not None:
            lock.release()

    _spawn_loop_thread(
        backend=backend, store=store, update_id=update_id, run_id=run.id,
        delivery_session_id=delivery_session_id, workspace=workspace, router=router,
        session_key=key, ctx=ctx, lock=lock, client=client, model=model,
        fallback_clients=fallback_clients, goal=goal, limits=limits,
    )

    return (f"🔁 /loop iniciado. run_id={run.id}\n"
            f"Trabalho autônomo: {limits.banner_pt()}\n"
            f"Acompanhe com /status; interrompa com /cancelar {run.id}")


def _spawn_loop_thread(*, backend: Any, store: TelegramLoopStore, update_id: int,
                       run_id: str, delivery_session_id: str, workspace: Path,
                       router: Any, session_key: str, ctx: Any, lock: threading.Lock,
                       client: Any, model: str, fallback_clients: list, goal: str,
                       limits: Any) -> None:
    def _worker() -> None:
        from .agent import run_one_turn_with_fallback
        from .channel_execution import claim_delivery, deliver_with_retry
        from .core.kernel import continue_governed
        from .privacy import redact_text
        from .serve_loop import run_loop_rounds

        store.update(update_id, state="running")
        kernel = backend._resolver_kernel(workspace=workspace)
        budget = limits.budget()
        all_tools: list[dict] = []
        stop_reason, last_text, rounds_done = "error", "", 0

        def _turn():
            return run_one_turn_with_fallback(
                ctx, router, client, model, fallback_clients,
                budget=budget, interrupt_check=_should_stop,
            )

        def _should_stop() -> str | None:
            if kernel is not None and kernel.control is not None and kernel.control.kill_switch_enabled():
                return "kill_switch"
            if kernel is not None:
                current = kernel.runs.get_run(run_id)
                if current is not None and current.status == "cancelled":
                    return "cancelled"
            if budget.is_exhausted:
                return "budget_exhausted"
            return None

        # Custo/chamadas ao modelo do run inteiro. Um TurnMetrics por
        # invocação do executor (replan incluso), como nos turnos normais do
        # canal; sem isto o run saía com custo None e llm_calls 0, e
        # `loop.max_cost_usd` nunca era aplicado no Telegram (sprint 81).
        metrics_box: list[Any] = []
        synced_cost = [0.0]

        def _sync_cost() -> None:
            if not metrics_box:
                return
            total = metrics_box[-1].total_usd
            delta, synced_cost[0] = total - synced_cost[0], total
            try:
                if delta > 0:
                    budget.consume_cost(delta)
            except Exception as exc:  # noqa: BLE001 — esgotou: o laço encerra
                logger.debug("loop budget consume: %s", exc)

        def _on_round(_n: int, _text: str, _tool_log: list) -> None:
            _sync_cost()
            # Persiste a cada rodada (não só ao final) — um crash no meio do
            # loop não perde as rodadas já concluídas, mesmo padrão de
            # `_start_loop_impl`.
            try:
                backend._store.save(session_key, ctx.messages)
            except Exception:  # noqa: BLE001 — persistência não interrompe o loop
                logger.warning("Falha ao persistir sessão do /loop (run %s)", run_id)

        def _rodar_rodadas(payload: dict) -> dict:
            nonlocal stop_reason, last_text, all_tools, rounds_done
            feedback = (payload or {}).get("replan_feedback")
            if feedback:
                ctx.add_user(f"A validação reprovou o resultado anterior: {feedback}\n"
                             f"Corrija isso e conclua.")
            from .channel_governance import TurnMetrics
            metrics = TurnMetrics()
            metrics_box.append(metrics)
            synced_cost[0] = 0.0
            try:
                with metrics.context(delivery_session_id, run_id):
                    stop_reason, rounds_done, last_text, all_tools = run_loop_rounds(
                        goal=goal, ctx=ctx, turn_fn=_turn, budget=budget,
                        should_stop=_should_stop, on_round=_on_round,
                        max_rounds=limits.max_rounds,
                    )
            except Exception as exc:  # noqa: BLE001
                stop_reason = "error"
                last_text = redact_text(str(exc))
                all_tools = getattr(exc, "partial_tool_log", [])
            _sync_cost()
            try:
                backend._store.save(session_key, ctx.messages)
            except Exception:  # noqa: BLE001
                logger.warning("Falha ao persistir sessão do /loop (run %s)", run_id)
            out = metrics.result(last_text, all_tools)
            if stop_reason in ("kill_switch", "cancelled"):
                return {**out, "status": "cancelled",
                        "error": ("runtime kill switch ativo" if stop_reason == "kill_switch"
                                  else "cancelado pelo usuário")}
            if stop_reason in ("completed", "budget_exhausted", "max_rounds"):
                return out
            error = f"loop parou: {stop_reason}"
            if stop_reason == "objetivo_pendente":
                error = "o agente encerrou sem cumprir o objetivo"
            if stop_reason == "provider_error" and last_text.strip():
                # Sprint 78: o motivo real aparece no run (painel), não só no relatório.
                error = f"{error} — {last_text.strip()[:300]}"
            return {**out, "status": "failed", "error": error}

        validacao_reprovou = False
        try:
            gov = continue_governed(kernel, run_id, _rodar_rodadas)
            if kernel is not None and gov.blocked_before_start and gov.status == "cancelled":
                # K08: a continuação foi barrada (kill-switch/cancelamento entre
                # a admissão e a thread) — o relatório diz isso, não "erro".
                stop_reason = ("kill_switch" if "kill switch" in (gov.error or "")
                               else "cancelled")
            if kernel is not None and not gov.ok and stop_reason == "completed":
                validacao_reprovou = True
                stop_reason = "validacao_reprovou"
                last_text = last_text or (gov.error or "")
        except BaseException as exc:  # noqa: BLE001 — thread nunca morre muda
            stop_reason, last_text, all_tools = "error", redact_text(str(exc)), []
        finally:
            try:
                lock.release()
            except RuntimeError:
                pass

        new_state = _STATE_FOR_STOP_REASON.get(stop_reason, "failed")
        store.update(update_id, state=new_state, finished_at=_now_iso())

        if claim_delivery(f"loop:{run_id}:done"):
            text = _terminal_report_text(
                run_id=run_id, goal=goal, stop_reason=stop_reason, last_text=last_text,
                tool_calls=len(all_tools), rounds=rounds_done,
                validacao_reprovou=validacao_reprovou,
                cost_usd=(sum(m.total_usd for m in metrics_box) if metrics_box else None),
            )
            delivered = deliver_with_retry(delivery_session_id, text)
            if delivered:
                store.update(update_id, delivered=True)

    threading.Thread(target=_worker, name=f"bauer-tgloop:{run_id[:12]}", daemon=True).start()


def _terminal_report_text(*, run_id: str, goal: str, stop_reason: str, last_text: str,
                          tool_calls: int, rounds: int = 0,
                          validacao_reprovou: bool = False,
                          cost_usd: float | None = None) -> str:
    """Relatório final — reflete o `stop_reason` real, nunca um texto
    genérico de sucesso (critério de aceite do SPEC).

    `cost_usd` é o custo medido do run (sprint 81); `None` omite a linha.
    """
    headline = {
        "completed": "✅ /loop concluído",
        "budget_exhausted": "⏹ /loop interrompido — limite de custo/tempo/tools atingido",
        "max_rounds": "⏹ /loop interrompido — limite de rodadas atingido",
        "cancelled": "🛑 /loop cancelado",
        "kill_switch": "🛑 /loop interrompido pelo kill-switch",
        "validacao_reprovou": "⚠️ /loop encerrou mas a validação reprovou o resultado",
        "objetivo_pendente": "⚠️ /loop encerrou sem cumprir o objetivo",
        "provider_error": "⚠️ /loop interrompido por erro do provedor",
        "empty_response": "⚠️ /loop interrompido — resposta vazia do modelo",
    }.get(stop_reason, "⚠️ /loop encerrou com falha")
    lines = [
        headline,
        f"run_id={run_id}",
        f"objetivo: {goal[:200]}",
        f"stop_reason={stop_reason}  rodadas={rounds}  tool_calls={tool_calls}",
    ]
    if cost_usd is not None:
        lines.append(f"custo estimado: US$ {cost_usd:.4f}")
    if last_text.strip():
        lines.append("")
        lines.append(last_text.strip()[:3500])
    return "\n".join(lines)


def _interrupted_report_text(record: TelegramLoopRecord) -> str:
    return (
        "🛑 /loop interrompido — o gateway Telegram reiniciou durante a "
        f"execução autônoma e o trabalho não pôde continuar sozinho.\n"
        f"run_id={record.run_id}\n"
        f"objetivo: {record.goal[:200]}\n"
        "Nenhuma nova execução foi iniciada automaticamente; use /loop de novo "
        "se quiser retomar."
    )


def _redeliver_terminal_record(backend: Any, record: TelegramLoopRecord) -> bool:
    """Re-tenta a entrega de um registro já TERMINAL que nunca confirmou
    `delivered` (ver `TelegramLoopStore.list_terminal_undelivered`).

    Best-effort: o `last_text`/`tool_calls` exatos só existiam nas variáveis
    locais da thread `_worker` que morreu (ou do processo que caiu) antes de
    confirmar a entrega — o que sobra é o que o Kernel ainda tiver sobre o
    Run (se o processo do Kernel sobreviveu) e o próprio registro. Mesma
    chave de claim que `_worker`/`reconcile_pending` usariam para este
    `run_id` — processo novo, `claim_delivery` começa vazio, então não há
    risco de bloquear numa claim que "já foi usada" antes do crash.
    """
    from .channel_execution import claim_delivery, deliver_with_retry

    last_text, tool_calls = "", 0
    if record.run_id:
        run = None
        try:
            kernel = backend._resolver_kernel(workspace=Path(record.workspace))
            if kernel is not None:
                run = kernel.runs.get_run(record.run_id)
        except Exception:
            logger.warning("Re-entrega de /loop: Kernel/get_run falhou para %s",
                           record.run_id, exc_info=True)
        if run is not None:
            last_text = str((run.output or {}).get("response", "") or "")
            tool_calls = getattr(run, "tool_calls_count", 0) or 0

    text = _terminal_report_text(
        run_id=record.run_id or "?", goal=record.goal,
        stop_reason=_STOP_REASON_FOR_STATE.get(record.state, record.state),
        last_text=last_text, tool_calls=tool_calls,
    )
    if not claim_delivery(f"loop:{record.run_id}:done"):
        return False
    return deliver_with_retry(record.session_id, text)


def reconcile_pending(bridge: Any) -> None:
    """Chamado em `TelegramBridge.start()`, antes do polling.

    Nunca reemite `/loop` sozinho: só corrige o registro local contra o que o
    Kernel realmente sabe, e entrega (uma única vez) o relatório pendente.

    Guarda de execução única por instância (`bridge._loop_reconciled`): a
    premissa "o registro não-terminal está morto porque threads não
    sobrevivem a um restart de processo" só é verdadeira para um restart DE
    PROCESSO real. `GatewayRuntime._run_bridge` também chama `bridge.start()`
    de novo, na MESMA instância e MESMO processo, após qualquer exceção não
    tratada por `start()` (ex.: algo fora de `(httpx.HTTPError, RuntimeError)`
    no loop de polling) — nesse caso uma thread de `/loop` pode estar
    genuinamente viva. Sem a guarda, esse retry in-process re-executaria
    `reconcile_pending`, marcaria o run `failed` por engano e mandaria um
    "interrompido" falso enquanto a thread real ainda roda e depois tentaria
    entregar um segundo relatório contraditório ("completed"). A guarda
    garante que isso só roda uma vez por vida real do processo desta
    instância de bridge — `bridge.reset()` (chamado entre retries) não a
    limpa de propósito.
    """
    if getattr(bridge, "_loop_reconciled", False):
        return
    bridge._loop_reconciled = True

    from .channel_execution import claim_delivery, deliver_with_retry

    backend = bridge.backend
    store = _store_for(backend)

    for record in store.list_non_terminal():
        if not record.run_id:
            continue
        try:
            kernel = backend._resolver_kernel(workspace=Path(record.workspace))
        except Exception:
            logger.warning("Reconciliação de /loop: Kernel indisponível para %s",
                           record.run_id, exc_info=True)
            continue
        if kernel is None:
            continue
        try:
            run = kernel.runs.get_run(record.run_id)
        except Exception:
            logger.warning("Reconciliação de /loop: get_run falhou para %s",
                           record.run_id, exc_info=True)
            continue
        if run is None:
            continue

        if run.status in _STATE_FOR_RUN_STATUS:
            new_state = _STATE_FOR_RUN_STATUS[run.status]
            store.update(record.update_id, state=new_state,
                        finished_at=run.finished_at or _now_iso())
            if claim_delivery(f"loop:{record.run_id}:done"):
                text = _terminal_report_text(
                    run_id=record.run_id, goal=record.goal,
                    stop_reason=("completed" if run.status == "completed"
                                else ("cancelled" if run.status == "cancelled" else "error")),
                    last_text=str((run.output or {}).get("response", "") or ""),
                    tool_calls=run.tool_calls_count, validacao_reprovou=False,
                )
                if deliver_with_retry(record.session_id, text):
                    store.update(record.update_id, delivered=True)
            continue

        # Run ainda não-terminal: a thread que o executava morreu com o
        # processo (threads não sobrevivem a restart) — nunca reemite o
        # /loop, só marca o desfecho honesto e avisa.
        try:
            kernel.runs.fail_run_se_nao_terminal(
                record.run_id, "gateway Telegram reiniciou durante a execução autônoma",
            )
        except Exception:
            logger.warning("Reconciliação de /loop: fail_run_se_nao_terminal falhou para %s",
                           record.run_id, exc_info=True)
            continue
        store.update(record.update_id, state="stopped", finished_at=_now_iso())
        if claim_delivery(f"loop:{record.run_id}:interrupted"):
            if deliver_with_retry(record.session_id, _interrupted_report_text(record)):
                store.update(record.update_id, delivered=True)

    # Varredura dos registros TERMINAIS que nunca confirmaram entrega — um
    # crash exatamente entre `store.update(..., state=<terminal>)` e a
    # confirmação de `deliver_with_retry` (ou o esgotamento dos retries do
    # próprio `_worker`) deixaria esse relatório perdido para sempre, já que
    # `list_non_terminal` acima nunca revisita um registro terminal. Loops
    # não têm `/atividade` (fora de escopo do SPEC) — esta é a única outra
    # chance do usuário descobrir o desfecho.
    for record in store.list_terminal_undelivered():
        try:
            if _redeliver_terminal_record(backend, record):
                store.update(record.update_id, delivered=True)
        except Exception:
            logger.warning("Reconciliação de /loop: re-entrega falhou para %s",
                           record.run_id, exc_info=True)

    store.purge_old()


def cancel_loop(backend: Any, chat_id: str, run_id: str | None) -> str | None:
    """Fallback do `/cancelar` para loops (que não usam ChannelExecutionRegistry).

    `None` = nenhum loop correspondente — o caller cai no texto genérico de
    `cancel_channel_execution`. Mesma regra de ambiguidade: 0 -> None (texto
    genérico do caller), 1 -> cancela, 2+ -> lista de escolhas.
    """
    store = _store_for(backend)
    candidates = store.active_for_chat(chat_id)
    if run_id is not None:
        candidates = [r for r in candidates if r.run_id == run_id]
    if not candidates:
        return None
    if len(candidates) > 1:
        choices = "\n".join(f"/cancelar {r.run_id}" for r in candidates)
        return f"Escolha o /loop que deseja interromper:\n{choices}"

    record = candidates[0]
    try:
        kernel = backend._resolver_kernel(workspace=Path(record.workspace))
    except Exception:
        logger.warning("Cancelamento de /loop: Kernel indisponível", exc_info=True)
        return "Não consegui localizar o Kernel deste /loop; nenhuma ação foi realizada."
    if kernel is None:
        return "Não consegui localizar o Kernel deste /loop; nenhuma ação foi realizada."

    kernel.runs.cancel_run(record.run_id)
    return (f"Cancelamento solicitado para o /loop {record.run_id}. "
            "O trabalho para na próxima verificação entre rodadas; "
            "efeitos e custo já consumidos são preservados.")
