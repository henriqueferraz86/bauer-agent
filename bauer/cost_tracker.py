"""Cost tracker — rastreia tokens consumidos e custo USD por sessão.

Integra com catalog_models() para obter preços atualizados.
Alimenta métricas Prometheus e alertas de budget.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_COST_FILE = Path.home() / ".bauer" / "cost_history.jsonl"


@dataclass
class UsageRecord:
    """Registro de uso de tokens numa chamada LLM."""
    session_id: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    ts: float = field(default_factory=time.time)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 8),
            "ts": self.ts,
        }


class CostTracker:
    """Rastreia tokens e custo USD por sessão, com alertas de budget.

    Thread-safe. Persiste histórico em JSONL.
    """

    def __init__(
        self,
        session_id: str = "",
        budget_usd: float = 0.0,
        file_path: Optional[Path] = None,
        alert_callback=None,
    ) -> None:
        self._session_id = session_id or f"s-{int(time.time())}"
        self._budget = budget_usd
        self._file = file_path or _DEFAULT_COST_FILE
        self._alert_callback = alert_callback
        self._lock = threading.Lock()
        self._records: List[UsageRecord] = []
        self._price_cache: Dict[str, Dict[str, float]] = {}
        self._file.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> UsageRecord:
        """Registra uso de tokens e calcula custo."""
        cost = self._calc_cost(model, provider, prompt_tokens, completion_tokens)
        rec = UsageRecord(
            session_id=self._session_id,
            model=model,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
        )
        with self._lock:
            self._records.append(rec)
        self._persist(rec)
        self._check_budget()
        return rec

    def session_totals(self) -> Dict[str, Any]:
        """Resumo da sessão atual."""
        with self._lock:
            recs = list(self._records)
        return {
            "session_id": self._session_id,
            "total_tokens": sum(r.total_tokens for r in recs),
            "prompt_tokens": sum(r.prompt_tokens for r in recs),
            "completion_tokens": sum(r.completion_tokens for r in recs),
            "cost_usd": round(sum(r.cost_usd for r in recs), 6),
            "calls": len(recs),
            "budget_usd": self._budget,
            "budget_remaining_usd": max(0.0, self._budget - sum(r.cost_usd for r in recs)) if self._budget > 0 else None,
        }

    def budget_exceeded(self) -> bool:
        if self._budget <= 0:
            return False
        totals = self.session_totals()
        return totals["cost_usd"] >= self._budget

    def format_status(self) -> str:
        """Linha de status compacta para exibir no CLI/Telegram."""
        t = self.session_totals()
        cost = t["cost_usd"]
        tokens = t["total_tokens"]
        budget_str = ""
        if self._budget > 0:
            pct = int(cost / self._budget * 100)
            budget_str = f" / ${self._budget:.3f} ({pct}%)"
        return f"${cost:.4f}{budget_str} | {tokens:,} tokens | {t['calls']} calls"

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    @classmethod
    def load_history(
        cls,
        file_path: Optional[Path] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Carrega histórico de custo do arquivo JSONL."""
        fp = file_path or _DEFAULT_COST_FILE
        if not fp.exists():
            return []
        results = []
        try:
            with open(fp, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if session_id and d.get("session_id") != session_id:
                            continue
                        results.append(d)
                        if len(results) >= limit:
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.debug("cost_tracker: load error: %s", exc)
        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _calc_cost(
        self,
        model: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Calcula custo USD usando catalog_models(). Fallback para 0 se indisponível."""
        price = self._get_price(model, provider)
        cost_in = price.get("cost_in", 0.0)
        cost_out = price.get("cost_out", 0.0)
        # Preços estão em $/M tokens
        return (prompt_tokens * cost_in + completion_tokens * cost_out) / 1_000_000

    def _get_price(self, model: str, provider: str) -> Dict[str, float]:
        key = f"{provider}/{model}"
        if key in self._price_cache:
            return self._price_cache[key]
        try:
            from .models_dev import catalog_models
            results = catalog_models(provider=provider)
            for entry in results:
                if entry["id"] == model:
                    self._price_cache[key] = entry
                    return entry
        except Exception:
            pass
        # Fallback: preços zero
        self._price_cache[key] = {"cost_in": 0.0, "cost_out": 0.0}
        return self._price_cache[key]

    def _persist(self, rec: UsageRecord) -> None:
        try:
            with open(self._file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict()) + "\n")
        except Exception as exc:
            logger.debug("cost_tracker: persist error: %s", exc)

    def _check_budget(self) -> None:
        if self._budget <= 0:
            return
        totals = self.session_totals()
        if totals["cost_usd"] >= self._budget and self._alert_callback:
            try:
                self._alert_callback(totals)
            except Exception as exc:
                logger.debug("cost_tracker: alert callback error: %s", exc)


def record_llm_usage(
    session_id: str,
    provider: str,
    model: str,
    usage: Dict[str, Any] | None,
    cost_usd: float,
    file_path: Optional[Path] = None,
) -> Optional[UsageRecord]:
    """Persiste uma chamada LLM no cost_history.jsonl com custo JÁ calculado.

    Pensado para o sink do cost_meter (serve/gateway): o custo vem do
    usage_pricing no momento da call — sem lookup de catálogo (rede) no
    caminho quente. Aceita usage no formato OpenAI (prompt/completion_tokens)
    ou Anthropic (input/output_tokens). Nunca levanta exceção."""
    if not usage:
        return None
    try:
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        rec = UsageRecord(
            session_id=session_id,
            model=model,
            provider=provider,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cost_usd=float(cost_usd or 0.0),
        )
        fp = file_path or _DEFAULT_COST_FILE
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict()) + "\n")
        return rec
    except Exception as exc:  # noqa: BLE001 — medição não pode quebrar o turno
        logger.debug("cost_tracker: record_llm_usage error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Singleton global por sessão
# ---------------------------------------------------------------------------

_trackers: Dict[str, CostTracker] = {}
_lock = threading.Lock()


def get_cost_tracker(session_id: str, budget_usd: float = 0.0) -> CostTracker:
    with _lock:
        if session_id not in _trackers:
            _trackers[session_id] = CostTracker(session_id=session_id, budget_usd=budget_usd)
        return _trackers[session_id]


def reset_cost_trackers() -> None:
    with _lock:
        _trackers.clear()
