from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

import httpx
import pytest

from bauer.voice_session import (
    CombinedStreamSink,
    StreamingVoiceOutput,
    VoiceOutputCancelled,
    VoiceOutputError,
    VoiceTurnCancelled,
    configured_tts_rate,
    configured_tts_voice,
    speak_response,
    synthesize_speech,
)
from bauer.voice_vad import EnergyVAD, VOICE_ACTIVE, VOICE_FINISHED, VOICE_STARTED
from bauer.voice_metrics import VoiceTurnMetrics


def _client() -> Mock:
    client = Mock()
    client._chat_url.return_value = "https://api.example.test/v1/chat/completions"
    client._headers = {
        "Authorization": "Bearer test-key",
        "Content-Type": "application/json",
    }
    return client


def test_synthesize_speech_posts_wav_request(tmp_path: Path):
    destination = tmp_path / "answer.wav"
    response = httpx.Response(200, content=b"RIFF fake wav")

    with patch("bauer.voice_session.httpx.post", return_value=response) as post:
        result = synthesize_speech("Olá, Henrique.", _client(), destination)

    assert result == destination
    assert destination.read_bytes() == b"RIFF fake wav"
    post.assert_called_once()
    request = post.call_args.kwargs
    assert request["json"] == {
        "model": "tts-1",
        "voice": "alloy",
        "input": "Olá, Henrique.",
        "response_format": "wav",
    }
    assert request["headers"]["Authorization"] == "Bearer test-key"


def test_synthesize_speech_reports_provider_error(tmp_path: Path):
    response = httpx.Response(401, text="unauthorized")

    with patch("bauer.voice_session.httpx.post", return_value=response):
        with pytest.raises(VoiceOutputError, match="HTTP 401"):
            synthesize_speech("Olá", _client(), tmp_path / "answer.wav")


def test_jarvis_tts_profile_selects_deeper_voice_and_slower_rate(monkeypatch):
    monkeypatch.setenv("BAUER_TTS_PROFILE", "jarvis")
    monkeypatch.delenv("BAUER_TTS_VOICE", raising=False)
    monkeypatch.delenv("BAUER_TTS_RATE", raising=False)

    assert configured_tts_voice() == "onyx"
    assert configured_tts_rate() == -1


def test_speak_response_cleans_temporary_file():
    with patch(
        "bauer.voice_session.synthesize_local_speech",
        side_effect=VoiceOutputError("local indisponível"),
    ), patch("bauer.voice_session.synthesize_speech") as synthesize, \
            patch("bauer.voice_session.play_audio") as play:
        speak_response("Resposta curta.", _client())

    synthesize.assert_called_once()
    play.assert_called_once()
    temporary_path = synthesize.call_args.args[2]
    assert not temporary_path.exists()


def test_streaming_voice_output_queues_sentences_in_order():
    calls: list[str] = []

    with patch("bauer.voice_session.speak_response", side_effect=lambda text, *_args, **_kwargs: calls.append(text)):
        output = StreamingVoiceOutput(_client())
        output.on_delta("Primeira frase. Segunda frase")
        output.on_delta(" também.")
        output.finish()

    assert calls == ["Primeira frase.", "Segunda frase também."]
    assert output.spoken_segments == 2
    assert output.error is None


def test_playback_cancelled_before_start(tmp_path: Path):
    audio = tmp_path / "answer.wav"
    audio.write_bytes(b"fake wav")
    cancelled = Event()
    cancelled.set()

    from bauer.voice_session import play_audio

    with pytest.raises(VoiceOutputCancelled, match="interrompido"):
        play_audio(audio, cancel_event=cancelled)


def test_streaming_voice_output_cancel_discards_pending_segments():
    with patch("bauer.voice_session.speak_response") as speak:
        output = StreamingVoiceOutput(_client())
        output.cancel()
        output.on_delta("Não deve ser falado.")
        output.finish()

    speak.assert_not_called()
    assert output.spoken_segments == 0


def test_barge_in_only_cancels_during_active_playback():
    from bauer.voice_session import BargeInController

    controller = BargeInController()
    controller._on_event("VOICE_STARTED")
    assert controller.cancel_event.is_set() is False

    controller._playback_active.set()
    controller._on_event("VOICE_STARTED")
    assert controller.cancel_event.is_set() is True


def test_combined_stream_sink_exposes_barge_in_cancellation():
    output = StreamingVoiceOutput(_client())
    sink = CombinedStreamSink(None, output)
    assert sink.cancelled is False
    output.cancel()
    assert sink.cancelled is True


def test_streaming_output_cancellation_is_a_turn_signal():
    from bauer.agent import _stream_to_sink
    from bauer.delta_stream import reset_sink, set_sink

    class _Client:
        def chat_stream(self, _model, _payload):
            yield "primeira frase"
            yield "segunda frase"

    output = StreamingVoiceOutput(_client())
    output.cancel()
    token = set_sink(CombinedStreamSink(None, output))
    try:
        with pytest.raises(VoiceTurnCancelled, match="interrompido"):
            _stream_to_sink(_Client(), "modelo", [])
    finally:
        reset_sink(token)


def test_energy_vad_emits_start_active_and_finished():
    vad = EnergyVAD(
        threshold_db=-30,
        min_voice_duration_s=0.04,
        silence_duration_s=0.04,
    )

    assert vad.process_level(-20, 0.02) == []
    assert vad.process_level(-20, 0.02) == [VOICE_STARTED]
    assert vad.process_level(-20, 0.02) == [VOICE_ACTIVE]
    assert vad.process_level(-50, 0.02) == [VOICE_ACTIVE]
    assert vad.process_level(-50, 0.02) == [VOICE_FINISHED]


def test_energy_vad_ignores_short_noise():
    vad = EnergyVAD(min_voice_duration_s=0.1)
    assert vad.process_level(-20, 0.02) == []
    assert vad.process_level(-50, 0.02) == []
    assert vad.speaking is False


def test_nlms_aec_keeps_signal_when_reference_is_silent():
    np = pytest.importorskip("numpy")
    from bauer.voice_aec import NLMSEchoCanceller

    microphone = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    reference = np.zeros(3, dtype=np.float32)
    cleaned = NLMSEchoCanceller().process(microphone, reference)

    assert np.allclose(cleaned, microphone)


def test_playback_reference_reads_pcm_frames(tmp_path: Path):
    np = pytest.importorskip("numpy")
    import wave

    from bauer.voice_aec import PlaybackReference

    audio = tmp_path / "reference.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(np.array([0, 16384, -16384], dtype=np.int16).tobytes())

    reference = PlaybackReference(audio)
    reference.start()
    frame = reference.next_frame(3)

    assert np.allclose(frame, [0.0, 0.5, -0.5], atol=1e-4)


def test_voice_turn_metrics_measure_pipeline_stages():
    metrics = VoiceTurnMetrics(turn_id="test-turn")
    metrics.mark("stt_start")
    metrics.mark("stt_end")
    metrics.mark("llm_start")
    metrics.mark("llm_first_delta")
    metrics.mark("llm_end")

    payload = metrics.finish()

    assert payload["turn_id"] == "test-turn"
    assert payload["status"] == "completed"
    assert payload["finished"] is True
    assert "stt_ms" in payload["durations_ms"]
    assert "llm_to_first_delta_ms" in payload["durations_ms"]
    assert "llm_ms" in payload["durations_ms"]
