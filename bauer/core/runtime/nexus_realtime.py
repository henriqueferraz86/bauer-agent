"""Provider-neutral realtime voice session state machine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Callable, Iterable, Protocol


class RealtimeState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class AudioFrame:
    pcm: bytes
    sample_rate: int = 16_000
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(UTC).isoformat())


class VoiceActivityDetector(Protocol):
    def __call__(self, frame: bytes) -> bool: ...


class SpeechRecognizer(Protocol):
    def __call__(self, frames: list[bytes]) -> str: ...


class StreamingLLM(Protocol):
    def __call__(self, text: str) -> Iterable[str]: ...


class StreamingTTS(Protocol):
    def __call__(self, text: str) -> Any: ...


class AudioStream:
    """Small in-memory stream contract useful for adapters and tests."""

    def __init__(self) -> None:
        self._frames: list[AudioFrame] = []

    def push(self, frame: AudioFrame) -> None:
        self._frames.append(frame)

    def drain(self) -> list[AudioFrame]:
        frames, self._frames = self._frames, []
        return frames


class InterruptionManager:
    def __init__(self) -> None:
        self.is_cancelled = False

    def begin(self) -> None:
        self.is_cancelled = False

    def cancel(self) -> bool:
        if self.is_cancelled:
            return False
        self.is_cancelled = True
        return True


class NexusRealtimeSession:
    def __init__(
        self,
        *,
        session_id: str,
        detect_voice: VoiceActivityDetector,
        recognize: SpeechRecognizer,
        stream_llm: StreamingLLM | None = None,
        synthesize: StreamingTTS | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        if not session_id.strip():
            raise ValueError("session_id is required")
        self.session_id = session_id
        self.detect_voice = detect_voice
        self.recognize = recognize
        self.stream_llm: StreamingLLM = stream_llm or (lambda _text: [])
        self.synthesize = synthesize or (lambda text: text)
        self.emit = emit or (lambda _event, _data: None)
        self.state = RealtimeState.IDLE
        self.interruption = InterruptionManager()
        self.audio_stream = AudioStream()

    def push_audio(self, frame: bytes) -> list[Any]:
        self.audio_stream.push(AudioFrame(frame))
        if not self.detect_voice(frame):
            return []
        frames = [item.pcm for item in self.audio_stream.drain()]
        self.state = RealtimeState.LISTENING
        self._emit("realtime.listening")
        try:
            self.state = RealtimeState.THINKING
            self._emit("realtime.thinking")
            text = self.recognize(frames)
            return self._speak_response(text)
        except Exception as exc:
            self.state = RealtimeState.IDLE
            self._emit("realtime.failed", error=str(exc))
            return []

    def start_speaking(self) -> None:
        self.interruption.begin()
        self.state = RealtimeState.SPEAKING
        self._emit("realtime.speaking")

    def interrupt(self) -> bool:
        if self.state not in {RealtimeState.SPEAKING, RealtimeState.THINKING}:
            return False
        changed = self.interruption.cancel()
        if changed:
            self.state = RealtimeState.INTERRUPTED
            self._emit("realtime.interrupted")
            self.state = RealtimeState.LISTENING
        return changed

    def _speak_response(self, text: str) -> list[Any]:
        self.start_speaking()
        output: list[Any] = []
        for chunk in self.stream_llm(text):
            if self.interruption.is_cancelled:
                return output
            output.append(self.synthesize(chunk))
        if self.interruption.is_cancelled:
            return output
        self.state = RealtimeState.IDLE
        self._emit("realtime.turn.completed")
        return output

    def _emit(self, event: str, **data: Any) -> None:
        self.emit(event, {"session_id": self.session_id, "state": self.state.value, **data})
