"""Continuous duplex conversation primitives built on the Nexus contracts."""

from __future__ import annotations

import re
from collections import deque
from enum import StrEnum
from statistics import fmean
from typing import Any, Callable


class DuplexMode(StrEnum):
    PUSH_TO_TALK = "push-to-talk"
    WAKE_WORD = "wake-word"
    CONTINUOUS = "continuous"
    MEETING = "meeting"
    HANDS_FREE = "hands-free"


class SentenceBuffer:
    def __init__(self, *, max_chars: int = 240):
        self.max_chars = max(1, max_chars)
        self._text = ""

    def feed(self, delta: str) -> list[str]:
        self._text += delta
        sentences: list[str] = []
        while True:
            match = re.search(r"(.+?[.!?](?:\s|$))", self._text, flags=re.DOTALL)
            if not match:
                break
            sentences.append(match.group(1).strip())
            self._text = self._text[match.end():]
        while len(self._text) >= self.max_chars:
            cut = self._text.rfind(" ", 0, self.max_chars)
            cut = cut if cut > 0 else self.max_chars
            sentences.append(self._text[:cut].strip())
            self._text = self._text[cut:].lstrip()
        return [sentence for sentence in sentences if sentence]

    def flush(self) -> str:
        text, self._text = self._text.strip(), ""
        return text


class AdaptiveLatency:
    def __init__(self, *, target_ms: float = 250.0, window: int = 8):
        self.target_ms = target_ms
        self._samples: deque[float] = deque(maxlen=max(1, window))

    def observe(self, latency_ms: float) -> float:
        self._samples.append(max(0.0, float(latency_ms)))
        average = fmean(self._samples)
        self.target_ms = min(1000.0, max(80.0, average * 0.75 + self.target_ms * 0.25))
        return self.target_ms


class JarvisDuplexSession:
    def __init__(
        self,
        *,
        mode: DuplexMode = DuplexMode.CONTINUOUS,
        wake_word: Callable[[bytes], bool] | None = None,
        detect_voice: Callable[[bytes], bool] | None = None,
        synthesize: Callable[[str], Any] | None = None,
        emit: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.mode = DuplexMode(mode)
        self.wake_word = wake_word or (lambda _frame: False)
        self.detect_voice = detect_voice or (lambda _frame: True)
        self.synthesize = synthesize or (lambda sentence: sentence)
        self.emit = emit or (lambda _event, _data: None)
        self.state = "idle"
        self.context: list[dict[str, str]] = []
        self.buffer = SentenceBuffer()
        self.latency = AdaptiveLatency()
        self._assistant_text = ""
        self._push_to_talk = False
        self._awake = self.mode is not DuplexMode.WAKE_WORD
        self._interrupted = False

    def accept_audio(self, frame: bytes) -> bool:
        if self.mode is DuplexMode.PUSH_TO_TALK and not self._push_to_talk:
            return False
        if self.mode is DuplexMode.WAKE_WORD and not self._awake:
            if not self.wake_word(frame):
                return False
            self._awake = True
            self._emit("duplex.wake_word")
            return True
        if self.state == "speaking" and self.detect_voice(frame):
            return self.barge_in()
        return True

    def push_to_talk_start(self) -> None:
        self._push_to_talk = True
        self._emit("duplex.listening")

    def push_to_talk_end(self) -> None:
        self._push_to_talk = False

    def receive_transcript(self, text: str, *, final: bool = False) -> None:
        if not text.strip() or not final:
            return
        self._interrupted = False
        self._assistant_text = ""
        self.buffer.flush()
        self.context.append({"role": "user", "content": text.strip()})
        self.state = "thinking"
        self._emit("duplex.user_turn", text=text.strip())

    def receive_llm_delta(self, delta: str) -> list[Any]:
        if self.state not in {"thinking", "speaking"} or self._interrupted:
            return []
        if self.state != "speaking":
            self.state = "speaking"
            self._emit("duplex.speaking")
        self._assistant_text += delta
        output: list[Any] = []
        for sentence in self.buffer.feed(delta):
            if self._interrupted:
                return output
            output.append(self.synthesize(sentence))
        return output

    def finish_response(self) -> list[Any]:
        if not self._assistant_text:
            return []
        output: list[Any] = []
        remainder = self.buffer.flush()
        if remainder and not self._interrupted:
            output.append(self.synthesize(remainder))
        self._save_assistant_context()
        self.state = "listening" if self.mode in {DuplexMode.CONTINUOUS, DuplexMode.HANDS_FREE} else "idle"
        self._emit("duplex.turn.completed")
        return output

    def barge_in(self) -> bool:
        if self.state not in {"speaking", "thinking"} or self._interrupted:
            return False
        self._interrupted = True
        self._save_assistant_context()
        self.buffer.flush()
        self.state = "listening"
        self._emit("duplex.interrupted")
        return True

    def _save_assistant_context(self) -> None:
        if self._assistant_text and (
            not self.context or self.context[-1].get("role") != "assistant"
        ):
            self.context.append({"role": "assistant", "content": self._assistant_text})

    def _emit(self, event: str, **data: Any) -> None:
        self.emit(event, {"mode": self.mode.value, "state": self.state, **data})

