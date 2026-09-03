from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

np = pytest.importorskip("numpy")

from bauer.voice_stt_stream import (  # noqa: E402
    STT_FINAL,
    STT_PARTIAL,
    StreamingSTTSession,
    merge_transcript,
)


def test_merge_transcript_removes_boundary_overlap():
    assert merge_transcript("Bauer verifica", "verifica meus containers") == (
        "Bauer verifica meus containers"
    )
    assert merge_transcript("Olá", "Olá Bauer") == "Olá Bauer"


def test_streaming_stt_emits_partial_and_final(monkeypatch, tmp_path: Path):
    partials: list[tuple[str, str]] = []
    finals: list[tuple[str, str]] = []
    events: list[tuple[str, str]] = []
    responses = iter(
        [
            {"success": True, "transcript": "Olá"},
            {"success": True, "transcript": "Bauer"},
        ]
    )

    chunk_path = tmp_path / "chunk.wav"
    chunk_path.write_bytes(b"wav")
    monkeypatch.setattr("bauer.voice_stt_stream._write_audio", lambda *_args: chunk_path)

    session = StreamingSTTSession(
        sample_rate=10,
        segment_duration_s=0.5,
        transcriber=lambda _path: next(responses),
        on_partial=lambda text: partials.append((STT_PARTIAL, text)),
        on_final=lambda text: finals.append((STT_FINAL, text)),
        on_event=lambda event, text: events.append((event, text)),
    )
    session.push_frame(np.ones(5, dtype=np.float32))
    session.push_frame(np.ones(2, dtype=np.float32))

    assert session.finish() == "Olá Bauer"
    assert partials == [(STT_PARTIAL, "Olá"), (STT_PARTIAL, "Olá Bauer")]
    assert finals == [(STT_FINAL, "Olá Bauer")]
    assert events == [(STT_PARTIAL, "Olá"), (STT_PARTIAL, "Olá Bauer"), (STT_FINAL, "Olá Bauer")]
    assert session.error is None


def test_agent_capture_can_select_streaming_stt(monkeypatch):
    from rich.console import Console
    from bauer.agent import _capture_listen_input

    monkeypatch.setenv("BAUER_STT_STREAMING", "1")
    with patch(
        "bauer.voice_stt_stream.capture_voice_input_streaming",
        return_value="texto parcial final",
    ) as streaming, patch("bauer.audio_capture.capture_voice_input") as legacy:
        result = _capture_listen_input(Console())

    assert result == "texto parcial final"
    streaming.assert_called_once()
    legacy.assert_not_called()
