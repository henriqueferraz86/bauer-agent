"""Primitivas compartilhadas do roteamento por turno."""

from __future__ import annotations

from typing import Any

from .decision_router import RouteDecision, decide_with_fallback


def decide_route(message: str, profiles: dict[str, Any], config: Any) -> RouteDecision:
    """Calcula uma rota usando Jev quando ativo e fallback local quando necessário."""
    return decide_with_fallback(message, profiles, config)


def route_event_data(decision: RouteDecision) -> dict[str, Any]:
    """Normaliza o payload público de ``model.route.selected``."""
    return {
        "task_type": decision.task_type,
        "complexity": decision.complexity,
        "tier": decision.profile,
        "provider": decision.provider,
        "model": decision.model,
        "source": decision.source,
        "confidence": decision.confidence,
        "orchestrate": decision.orchestrate,
    }
