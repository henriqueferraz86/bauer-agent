"""Autonomy profile and runtime budget enforcement."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..events import EventBus
from .budget_ledger import BudgetLedger, BudgetLedgerExceeded
from .state_store import JsonlStateStore

AUTONOMY_MODES = {"manual", "supervised", "autonomous", "locked"}


@dataclass(slots=True)
class AutonomyProfile:
    mode: str = "supervised"
    daily_budget_usd: float = 2.0
    weekly_budget_usd: float | None = None
    monthly_budget_usd: float | None = None
    max_tool_calls_per_run: int = 100
    max_runtime_s_per_run: int = 600
    max_parallel_runs: int = 3
    agent_budgets_usd: dict[str, float] = field(default_factory=dict)
    company_budgets_usd: dict[str, float] = field(default_factory=dict)
    max_cost_usd_per_run: float | None = None
    updated_at: str = field(default_factory=lambda: _now_iso())

    def validate(self) -> None:
        if self.mode not in AUTONOMY_MODES:
            raise ValueError(f"invalid autonomy mode: {self.mode}")
        if self.daily_budget_usd < 0:
            raise ValueError("daily_budget_usd must be non-negative")
        if self.max_tool_calls_per_run < 0:
            raise ValueError("max_tool_calls_per_run must be non-negative")
        if self.max_runtime_s_per_run < 0:
            raise ValueError("max_runtime_s_per_run must be non-negative")
        if self.max_parallel_runs < 0:
            raise ValueError("max_parallel_runs must be non-negative")


class BudgetExceededError(RuntimeError):
    pass


class BudgetManager:
    #: Depois disso, um run não-terminal é TRAVADO, não concorrente — e para de
    #: contar contra `max_parallel_runs`. Mesmo limiar default de
    #: ``RuntimeRecovery.recover_stuck_runs``, que é quem de fato o marca failed.
    stale_run_after_s: int = 900

    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        store: JsonlStateStore | None = None,
        event_bus: EventBus | None = None,
        stale_run_after_s: int | None = None,
    ) -> None:
        self.root = Path(getattr(store, "root", root))
        self.store = store or JsonlStateStore(root)
        self.event_bus = event_bus or EventBus(store=self.store)
        self.ledger = BudgetLedger(self.root)
        if stale_run_after_s is not None:
            self.stale_run_after_s = max(0, int(stale_run_after_s))

    def get_profile(self) -> AutonomyProfile:
        record = self.store.latest("autonomy", "profile")
        if not record:
            return AutonomyProfile()
        data = dict(record)
        data.pop("id", None)
        return AutonomyProfile(**data)

    def set_profile(self, **changes: Any) -> AutonomyProfile:
        data = asdict(self.get_profile())
        data.update(changes)
        data["updated_at"] = _now_iso()
        profile = AutonomyProfile(**data)
        profile.validate()
        self.store.upsert("autonomy", {"id": "profile", **asdict(profile)})
        self.event_bus.publish(
            "autonomy.changed",
            status=profile.mode,
            data={"profile": asdict(profile)},
        )
        return profile

    def status(self) -> dict[str, Any]:
        profile = self.get_profile()
        return {
            "profile": asdict(profile),
            "daily": self._period_status("daily", profile.daily_budget_usd),
            "weekly": self._period_status("weekly", profile.weekly_budget_usd),
            "monthly": self._period_status("monthly", profile.monthly_budget_usd),
            "by_agent": self._entity_status("agent_id", profile.agent_budgets_usd),
            "by_company": self._entity_status("company_id", profile.company_budgets_usd),
        }

    def ensure_can_start(
        self,
        *,
        run_id: str | None = None,
        agent_id: str = "default",
        company_id: str | None = None,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        profile = self.get_profile()
        if profile.mode == "locked":
            raise BudgetExceededError("autonomy mode is locked")

        # max_parallel_runs (antes config MORTO — nunca era lido/enforçado):
        # conta os runs não-terminais e recusa se já está no teto. A run deste
        # check ainda NÃO existe (é criada depois), então nenhum self-count.
        # Também limita o overshoot do TOCTOU de budget a no máx. N× enquanto a
        # reserva de custo por-run (a outra metade do #11) não é wired.
        if profile.max_parallel_runs and profile.max_parallel_runs > 0:
            # "Paralelo" é o que está VIVO agora, não o que ficou não-terminal em
            # algum momento da história. Sem o corte por idade, três Ctrl+C (ou
            # três crashes, ou três threads órfãs de SSE) travavam TODA execução
            # permanentemente com "max parallel runs reached: 3/3" — e só saía
            # disso se alguém chamasse recover() à mão. Medido ao ligar o Kernel
            # por default (HARNESS-020): o bloqueio pegou 4 testes de `bauer run`.
            #
            # Mesmo limiar do RuntimeRecovery.recover_stuck_runs: passou disso,
            # é run travado (que o recover vai marcar failed), não concorrente.
            from datetime import UTC, datetime, timedelta

            from .resilience import _parse_datetime
            from .run_manager import TERMINAL_RUN_STATUSES

            cutoff = datetime.now(UTC) - timedelta(seconds=self.stale_run_after_s)
            active = 0
            for r in self.store.list_latest("runs"):
                if r.get("status") in TERMINAL_RUN_STATUSES:
                    continue
                marker = r.get("updated_at") or r.get("started_at") or r.get("created_at")
                if marker:
                    try:
                        if _parse_datetime(str(marker)) <= cutoff:
                            continue  # travado, não paralelo
                    except (ValueError, TypeError) as exc:
                        # timestamp ilegível: não dá para provar que está travado,
                        # então conta como paralelo — o lado seguro é barrar
                        from ...logging_config import log_suppressed
                        log_suppressed("autonomy.parallel_runs.timestamp", exc)
                active += 1
            if active >= profile.max_parallel_runs:
                message = f"max parallel runs reached: {active}/{profile.max_parallel_runs}"
                self.event_bus.publish(
                    "budget.exceeded",
                    run_id=run_id,
                    agent_id=agent_id,
                    status="exceeded",
                    message=message,
                    data={"scope": "parallel_runs", "active": active,
                          "limit": profile.max_parallel_runs},
                )
                raise BudgetExceededError(message)

        checks = [
            ("daily", profile.daily_budget_usd, None),
            ("weekly", profile.weekly_budget_usd, None),
            ("monthly", profile.monthly_budget_usd, None),
            ("run", profile.max_cost_usd_per_run, None),
            ("agent", profile.agent_budgets_usd.get(agent_id), agent_id),
            ("company", profile.company_budgets_usd.get(company_id or ""), company_id),
        ]
        # A run created by the Kernel has an identity already. Reserve its
        # estimate under SQLite's writer lock so two processes cannot both pass
        # a read-then-write budget check. A zero estimate is intentionally a
        # zero reservation: callers must enforce the cap while running.
        if run_id:
            limits = {scope: limit for scope, limit, _entity_id in checks}
            try:
                self.ledger.reserve(
                    run_id=run_id, agent_id=agent_id, company_id=company_id,
                    estimated_cost_usd=estimated_cost_usd, limits=limits,
                )
            except BudgetLedgerExceeded as exc:
                self.event_bus.publish(
                    "budget.exceeded", run_id=run_id, agent_id=agent_id,
                    status="exceeded", message=str(exc), data={"scope": "atomic"},
                )
                raise BudgetExceededError(str(exc)) from exc
            return

        for scope, limit, entity_id in checks:
            if limit is None:
                continue
            used = 0.0 if scope == "run" else self._used_for_scope(scope, entity_id=entity_id)
            if used + estimated_cost_usd > float(limit):
                message = f"budget exceeded for {scope}: used=${used:.4f} limit=${float(limit):.4f}"
                self.event_bus.publish(
                    "budget.exceeded",
                    run_id=run_id,
                    agent_id=agent_id,
                    status="exceeded",
                    message=message,
                    data={
                        "scope": scope,
                        "entity_id": entity_id,
                        "used_usd": used,
                        "limit_usd": float(limit),
                        "estimated_cost_usd": estimated_cost_usd,
                    },
                )
                raise BudgetExceededError(message)
            if limit and used / float(limit) >= 0.8:
                self.event_bus.publish(
                    "budget.warning",
                    run_id=run_id,
                    agent_id=agent_id,
                    status="warning",
                    message=f"budget warning for {scope}",
                    data={"scope": scope, "used_usd": used, "limit_usd": float(limit)},
                )

    def record_run_cost(
        self,
        *,
        run_id: str,
        agent_id: str,
        cost_usd: float,
        company_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": f"cost-{run_id}",
            "run_id": run_id,
            "agent_id": agent_id,
            "company_id": company_id,
            "cost_usd": max(0.0, float(cost_usd)),
            "timestamp": _now_iso(),
            "metadata": metadata or {},
        }
        changed = self.ledger.settle(
            run_id=run_id, actual_cost_usd=record["cost_usd"],
            agent_id=agent_id, company_id=company_id,
        )
        if changed:
            self.store.append("run_costs", record)
        return record

    def release_run_reservation(self, run_id: str) -> None:
        self.ledger.release(run_id)

    def _period_status(self, scope: str, limit: float | None) -> dict[str, Any]:
        used = self._used_for_scope(scope)
        return {
            "limit_usd": limit,
            "used_usd": round(used, 6),
            "remaining_usd": None if limit is None else round(max(0.0, float(limit) - used), 6),
            "exceeded": False if limit is None else used >= float(limit),
        }

    def _entity_status(self, field: str, limits: dict[str, float]) -> dict[str, Any]:
        return {
            entity_id: {
                "limit_usd": limit,
                "used_usd": round(self._used_entity(field, entity_id), 6),
            }
            for entity_id, limit in sorted(limits.items())
        }

    def _used_for_scope(self, scope: str, *, entity_id: str | None = None) -> float:
        # SQLite includes both settled cost and active reservations. It migrates
        # historic JSONL once, so the old audit trail remains compatible.
        if scope in {"daily", "weekly", "monthly", "agent", "company"}:
            kwargs = {"agent_id": entity_id} if scope == "agent" else {}
            if scope == "company":
                kwargs = {"company_id": entity_id}
            return self.ledger.used(scope, **kwargs)
        if scope == "daily":
            since = datetime.now(UTC) - timedelta(days=1)
            return self._used_since(since)
        if scope == "weekly":
            since = datetime.now(UTC) - timedelta(days=7)
            return self._used_since(since)
        if scope == "monthly":
            since = datetime.now(UTC) - timedelta(days=30)
            return self._used_since(since)
        if scope == "agent" and entity_id:
            return self._used_entity("agent_id", entity_id)
        if scope == "company" and entity_id:
            return self._used_entity("company_id", entity_id)
        return 0.0

    def _used_since(self, since: datetime) -> float:
        total = 0.0
        for record in self.store.list("run_costs"):
            try:
                timestamp = datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            if timestamp >= since:
                total += float(record.get("cost_usd", 0) or 0)
        return total

    def _used_entity(self, field: str, entity_id: str) -> float:
        total = 0.0
        for record in self.store.list("run_costs"):
            if record.get(field) == entity_id:
                total += float(record.get("cost_usd", 0) or 0)
        return total


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
