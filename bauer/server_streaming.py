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
    """Retém uma rodada até saber se ela é texto final ou chamada de tool."""

    def __init__(self) -> None:
        self.pending = ""
        self.sent_any = False

    def feed(self, chunk: str) -> str:
        """Acumula um chunk; o chamador libera ou descarta no fim da rodada."""
        self.pending += chunk
        return ""

    def flush(self) -> str:
        """Libera uma rodada confirmada como resposta textual."""
        text = self.pending
        self.pending = ""
        self.sent_any = self.sent_any or bool(text)
        return text

    def discard(self) -> None:
        """Descarta narração e JSON de uma rodada que chamou uma ferramenta."""
        self.pending = ""


def strip_action_blocks(text: str, available: set[str]) -> str:
    """Remove JSONs de action e fences vazios do texto que segue ao chat."""

    def is_action(value: object) -> bool:
        if not isinstance(value, dict) or not isinstance(value.get("action"), str):
            return (
                isinstance(value, dict)
                and "query" in value
                and "web_search" in available
                and isinstance(value.get("query"), str)
                and bool(value["query"].strip())
                and set(value).issubset({"query", "max_results"})
            )
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
