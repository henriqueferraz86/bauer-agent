"""Leitura e resumo das métricas persistidas de voz."""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any


def _average(values: list[float]) -> float | None:
    return round(mean(values), 3) if values else None


def collect_voice_metrics(
    *,
    limit: int = 20,
    runtime_root: str | Path = "memory/runtime",
) -> dict[str, Any]:
    """Lê eventos ``voice.turn.completed`` e calcula um resumo seguro."""
    from .core.events.bus import EventBus

    events = EventBus(root=runtime_root).list_events(limit=max(1, limit * 3))
    voice_events = [event for event in events if event.event_type == "voice.turn.completed"]
    voice_events = voice_events[-max(1, limit) :]
    total_values: list[float] = []
    first_delta_values: list[float] = []
    synthesis_values: list[float] = []
    playback_values: list[float] = []
    for event in voice_events:
        data = event.data or {}
        durations = data.get("durations_ms", {})
        for target, source in (
            (total_values, "total_ms"),
            (first_delta_values, "llm_to_first_delta_ms"),
            (synthesis_values, "tts_synthesis_ms"),
            (playback_values, "tts_playback_ms"),
        ):
            value = data.get(source) if source == "total_ms" else durations.get(source)
            if isinstance(value, (int, float)):
                target.append(float(value))
    return {
        "turns": len(voice_events),
        "completed": sum((event.data or {}).get("status") == "completed" for event in voice_events),
        "errors": sum((event.data or {}).get("status") == "error" for event in voice_events),
        "averages_ms": {
            "total": _average(total_values),
            "llm_to_first_delta": _average(first_delta_values),
            "tts_synthesis": _average(synthesis_values),
            "tts_playback": _average(playback_values),
        },
        "recent": [
            {
                "turn_id": (event.data or {}).get("turn_id", event.id),
                "status": (event.data or {}).get("status", "unknown"),
                "total_ms": (event.data or {}).get("total_ms"),
            }
            for event in reversed(voice_events)
        ],
    }
