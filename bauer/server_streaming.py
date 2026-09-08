"""Primitivas puras de streaming SSE usadas pelo servidor HTTP."""

from __future__ import annotations

import json
import re


def sse_frame(data: str, event: str | None = None) -> str:
    """Codifica um evento SSE preservando quebras de linha do payload."""
    lines = "".join(f"data: {line}\n" for line in data.split("\n"))
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}{lines}\n"


class StreamGate:
    """Retém trechos que podem ser um JSON de chamada de ferramenta."""

    _PROBE = 96

    def __init__(self) -> None:
        self.pending = ""
        self.sent_any = False

    def _candidate_idx(self) -> int:
        indexes = [index for index in (self.pending.find("{"), self.pending.find("```")) if index != -1]
        return min(indexes) if indexes else -1

    def feed(self, chunk: str) -> str:
        """Acumula um chunk e devolve a parte segura para enviar ao cliente."""
        self.pending += chunk
        output: list[str] = []
        while True:
            index = self._candidate_idx()
            if index == -1:
                output.append(self.pending)
                self.pending = ""
                break
            output.append(self.pending[:index])
            self.pending = self.pending[index:]
            probe = self.pending[: self._PROBE]
            if '"action"' in probe or len(self.pending) < self._PROBE:
                break
            output.append(self.pending[0])
            self.pending = self.pending[1:]
        text = "".join(output)
        self.sent_any = self.sent_any or bool(text)
        return text


def strip_action_blocks(text: str, available: set[str]) -> str:
    """Remove JSONs de action e fences vazios do texto que segue ao chat."""

    def is_action(value: object) -> bool:
        if not isinstance(value, dict) or not isinstance(value.get("action"), str):
            return False
        return value["action"] in available or "args" in value

    decoder = json.JSONDecoder()
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "{":
            try:
                value, end = decoder.raw_decode(text, index)
                if is_action(value):
                    index = end
                    continue
            except json.JSONDecodeError:
                pass
        output.append(text[index])
        index += 1
    result = "".join(output)
    result = re.sub(r"```(?:json)?\s*```", "", result)
    return re.sub(r"\n{3,}", "\n\n", result)
