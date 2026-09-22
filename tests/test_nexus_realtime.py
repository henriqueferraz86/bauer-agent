from bauer.core.runtime import NexusRealtimeSession, RealtimeState


def test_realtime_session_processes_turn_and_emits_ordered_events():
    events: list[str] = []
    session = NexusRealtimeSession(
        session_id="voice-1",
        detect_voice=lambda frame: frame == b"voice",
        recognize=lambda frames: "qual e o status?",
        stream_llm=lambda text: ["O status ", "esta verde."],
        synthesize=lambda text: f"audio:{text}",
        emit=lambda event, data: events.append(event),
    )

    session.push_audio(b"silence")
    assert session.state is RealtimeState.IDLE
    result = session.push_audio(b"voice")

    assert result == ["audio:O status ", "audio:esta verde."]
    assert session.state is RealtimeState.IDLE
    assert events == [
        "realtime.listening",
        "realtime.thinking",
        "realtime.speaking",
        "realtime.turn.completed",
    ]


def test_interruption_is_idempotent_and_returns_to_listening():
    spoken: list[str] = []
    session = NexusRealtimeSession(
        session_id="voice-2",
        detect_voice=lambda _frame: True,
        recognize=lambda _frames: "continue",
        stream_llm=lambda _text: ["one", "two"],
        synthesize=lambda text: spoken.append(text) or text,
    )
    session.start_speaking()
    assert session.state is RealtimeState.SPEAKING

    assert session.interrupt() is True
    assert session.interrupt() is False
    assert session.state is RealtimeState.LISTENING
    assert session.interruption.is_cancelled is True


def test_callback_failure_enters_safe_idle_and_emits_failure():
    events: list[str] = []
    session = NexusRealtimeSession(
        session_id="voice-3",
        detect_voice=lambda _frame: True,
        recognize=lambda _frames: (_ for _ in ()).throw(RuntimeError("stt down")),
        emit=lambda event, data: events.append(event),
    )

    assert session.push_audio(b"voice") == []
    assert session.state is RealtimeState.IDLE
    assert events[-1] == "realtime.failed"

