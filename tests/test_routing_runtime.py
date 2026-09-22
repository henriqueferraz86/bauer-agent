from __future__ import annotations

from types import SimpleNamespace

from bauer.routing_runtime import route_event_data


def test_route_event_data_has_stable_observability_contract():
    decision = SimpleNamespace(
        task_type="coding",
        complexity="high",
        profile="coding",
        provider="local",
        model="qwen",
        source="heuristic",
        confidence=0.8,
        orchestrate=False,
    )

    assert route_event_data(decision) == {
        "task_type": "coding",
        "complexity": "high",
        "tier": "coding",
        "provider": "local",
        "model": "qwen",
        "source": "heuristic",
        "confidence": 0.8,
        "orchestrate": False,
    }
