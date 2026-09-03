from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from bauer.voice_metrics_report import collect_voice_metrics


def test_collect_voice_metrics_summarizes_persisted_events(tmp_path):
    events = [
        SimpleNamespace(
            event_type="voice.turn.completed",
            id="evt-1",
            data={
                "turn_id": "turn-1",
                "status": "completed",
                "total_ms": 1000,
                "durations_ms": {
                    "llm_to_first_delta_ms": 100,
                    "tts_synthesis_ms": 200,
                    "tts_playback_ms": 700,
                },
            },
        ),
        SimpleNamespace(
            event_type="voice.turn.completed",
            id="evt-2",
            data={"turn_id": "turn-2", "status": "error", "total_ms": 500, "durations_ms": {}},
        ),
        SimpleNamespace(event_type="run.completed", id="evt-3", data={}),
    ]
    fake_bus = SimpleNamespace(list_events=lambda **_kwargs: events)

    with patch("bauer.core.events.bus.EventBus", return_value=fake_bus):
        report = collect_voice_metrics(runtime_root=tmp_path)

    assert report["turns"] == 2
    assert report["completed"] == 1
    assert report["errors"] == 1
    assert report["averages_ms"]["total"] == 750.0
    assert report["averages_ms"]["llm_to_first_delta"] == 100.0
    assert report["recent"][0]["turn_id"] == "turn-2"
