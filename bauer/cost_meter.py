"""Cost meter — canaliza custo real de cada LLM call para quem precisa medir.

Problema que resolve (Fase 3.3): `AutonomousBudget.consume_llm_call()` existia
mas NUNCA era chamado — o daemon tinha cap de custo sem medição real. Este
módulo é a ponte: o agent loop reporta cada call aqui; quem quiser medir
(daemon, goal tracker, benchmark) registra um sink no seu contexto.

Uso — lado consumidor (daemon/worker)::

    from bauer.cost_meter import cost_sink

    def my_sink(provider, model, usage, cost_usd):
        budget.consume_llm_call(cost_usd=cost_usd,
                                output_tokens=usage.get("completion_tokens", 0))

    token = cost_sink.set(my_sink)
    try:
        ... roda a task (qualquer LLM call dentro reporta no sink) ...
    finally:
        cost_sink.reset(token)

Uso — lado produtor (agent loop, automático)::

    report_llm_cost(provider="openai", model="gpt-4o", usage=client.last_usage)

ContextVar propaga corretamente por threads criadas com contexto copiado e
por asyncio tasks — cada worker do daemon mede só as suas próprias calls.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Protocol

logger = logging.getLogger("bauer.cost_meter")


class CostSink(Protocol):
    def __call__(
        self, provider: str, model: str, usage: dict[str, Any], cost_usd: float
    ) -> None: ...


# Sink ativo no contexto atual. None → ninguém medindo (no-op barato).
cost_sink: ContextVar[CostSink | None] = ContextVar("bauer_cost_sink", default=None)


def report_llm_cost(provider: str, model: str, usage: dict[str, Any] | None) -> float:
    """Calcula o custo da call e entrega ao sink ativo (se houver).

    Retorna o custo estimado em USD (0.0 quando não há usage ou preço
    desconhecido). NUNCA levanta exceção — medição não pode quebrar o loop.
    """
    if not usage:
        return 0.0
    try:
        from .usage_pricing import estimate_cost_usd
        cost = float(estimate_cost_usd(provider, model, usage) or 0.0)
    except Exception:
        cost = 0.0

    sink = cost_sink.get()
    if sink is not None:
        try:
            sink(provider, model, dict(usage), cost)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cost sink falhou (ignorado): %s", exc)
    return cost


def provider_from_client(client: Any) -> str:
    """Inferência leve do provider a partir do cliente (para o report)."""
    host = str(getattr(client, "host", "")).lower()
    if "ollama" in host or ":11434" in host:
        return "ollama"
    if "anthropic" in host:
        return "anthropic"
    if "opencode" in host:
        return "opencode"
    if "groq" in host:
        return "groq"
    if "openai" in host:
        return "openai"
    cls = type(client).__name__.lower()
    if "anthropic" in cls:
        return "anthropic"
    if "ollama" in cls:
        return "ollama"
    return "openai"  # OpenAI-compat genérico
