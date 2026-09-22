from bauer.core.runtime import DuplexMode, JarvisDuplexSession


def test_continuous_mode_synthesizes_sentence_deltas():
    spoken: list[str] = []
    session = JarvisDuplexSession(
        mode=DuplexMode.CONTINUOUS,
        synthesize=lambda sentence: spoken.append(sentence) or sentence,
    )

    session.receive_transcript("verifique o deployment", final=True)
    session.receive_llm_delta("O deployment possui ")
    session.receive_llm_delta("duas réplicas.")
    session.finish_response()

    assert spoken == ["O deployment possui duas réplicas."]
    assert session.context[-1] == {"role": "assistant", "content": "O deployment possui duas réplicas."}


def test_barge_in_cancels_tts_and_preserves_partial_context():
    events: list[str] = []
    session = JarvisDuplexSession(mode=DuplexMode.HANDS_FREE, emit=lambda event, data: events.append(event))
    session.receive_transcript("fale sobre o deploy", final=True)
    session.receive_llm_delta("O deployment possui problemas")

    assert session.barge_in() is True
    assert session.barge_in() is False
    assert session.state == "listening"
    assert session.context[-1]["content"] == "O deployment possui problemas"
    assert "duplex.interrupted" in events


def test_push_to_talk_and_wake_word_gate_audio():
    session = JarvisDuplexSession(mode=DuplexMode.PUSH_TO_TALK)
    assert session.accept_audio(b"voice") is False
    session.push_to_talk_start()
    assert session.accept_audio(b"voice") is True
    session.push_to_talk_end()
    assert session.accept_audio(b"voice") is False

    wake = JarvisDuplexSession(mode=DuplexMode.WAKE_WORD, wake_word=lambda frame: frame == b"bauer")
    assert wake.accept_audio(b"voice") is False
    assert wake.accept_audio(b"bauer") is True
    assert wake.accept_audio(b"voice") is True

