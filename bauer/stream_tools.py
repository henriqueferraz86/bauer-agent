"""stream_tools.py — remonta tool calls que chegam fatiadas no stream (plano 028 F1).

Streaming com function calling não entrega a tool call pronta: ela vem em
pedaços, espalhados por dezenas de eventos SSE. O `arguments` é sempre fatiado
(chega caractere a caractere, formando um JSON que só é válido no fim), e os
providers discordam sobre o resto:

  - OpenAI manda `id` e `function.name` UMA vez, no primeiro fragmento;
  - vários compatíveis (OpenRouter e proxies) repetem `id` e `name` inteiros em
    TODO fragmento — concatenar cegamente produziria "read_fileread_fileread_file";
  - alguns fatiam até o `name` ("read_" + "file").

As três formas produzem a mesma chamada. Este acumulador é onde essa
divergência morre, em código puro — sem rede, sem cliente, testável.

`known_names` é o que desempata os dois últimos casos: o repetidor manda um
nome que já está completo; o fatiador manda um pedaço que só vira nome válido
depois de colado. Com a lista de tools em mãos dá para saber qual é qual em vez
de adivinhar.
"""

from __future__ import annotations

import json
from typing import Any


class ToolCallAccumulator:
    """Junta fragmentos de tool call e devolve a lista no formato OpenAI.

    Formato de saída (o que o agent consome):
        {"id": ..., "type": "function",
         "function": {"name": ..., "arguments": "<JSON como STRING>"}}
    """

    def __init__(self, known_names: "set[str] | None" = None) -> None:
        self.known_names = known_names or set()
        self._slots: "dict[int, dict[str, Any]]" = {}
        self._ordem: "list[int]" = []

    # ── entrada ──────────────────────────────────────────────────────────
    def add_fragment(self, frag: "dict[str, Any]") -> None:
        """Aplica um fragmento no formato `delta.tool_calls[i]` (OpenAI)."""
        if not isinstance(frag, dict):
            return
        idx = frag.get("index")
        if not isinstance(idx, int):
            # Provider sem `index`: assume que fragmentos sem índice pertencem à
            # última chamada aberta (ou à primeira, se nenhuma existe ainda).
            idx = self._ordem[-1] if self._ordem else 0
        slot = self._slot(idx)

        if frag.get("id"):
            slot["id"] = str(frag["id"])

        fn = frag.get("function")
        if not isinstance(fn, dict):
            return

        nome = fn.get("name")
        if nome:
            slot["name"] = self._fundir_nome(slot["name"], str(nome))

        args = fn.get("arguments")
        if args:
            slot["args"].append(args if isinstance(args, str) else json.dumps(args))

    def add_complete(self, tc: "dict[str, Any]") -> None:
        """Aplica uma tool call INTEIRA (o Ollama entrega assim, sem fatiar)."""
        if not isinstance(tc, dict):
            return
        fn = tc.get("function") or {}
        idx = len(self._ordem)
        slot = self._slot(idx)
        if tc.get("id"):
            slot["id"] = str(tc["id"])
        if fn.get("name"):
            slot["name"] = str(fn["name"])
        args = fn.get("arguments")
        if args is not None:
            # O Ollama manda `arguments` como OBJETO; o agent espera STRING.
            slot["args"] = [args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)]

    # ── saída ────────────────────────────────────────────────────────────
    def result(self) -> "list[dict[str, Any]]":
        saida: "list[dict[str, Any]]" = []
        for pos, idx in enumerate(self._ordem):
            slot = self._slots[idx]
            if not slot["name"]:
                continue  # fragmento órfão (só argumentos) — nada a chamar
            saida.append({
                "id": slot["id"] or f"call_{pos}",
                "type": "function",
                "function": {
                    "name": slot["name"],
                    # string vazia viraria JSONDecodeError no agent; "{}" é o
                    # equivalente honesto de "tool sem argumentos"
                    "arguments": "".join(slot["args"]) or "{}",
                },
            })
        return saida

    def __bool__(self) -> bool:
        return bool(self.result())

    # ── interno ──────────────────────────────────────────────────────────
    def _slot(self, idx: int) -> "dict[str, Any]":
        if idx not in self._slots:
            self._slots[idx] = {"id": "", "name": "", "args": []}
            self._ordem.append(idx)
        return self._slots[idx]

    def _fundir_nome(self, atual: str, novo: str) -> str:
        """Decide entre repetição e fatiamento do nome da tool."""
        if not atual:
            return novo
        if atual == novo:
            return atual                      # provider repete o nome inteiro
        if novo.startswith(atual):
            return novo                       # repete acumulando ("read" → "read_file")
        if atual + novo in self.known_names:
            return atual + novo               # provider fatiou o nome
        if atual in self.known_names:
            return atual                      # já está completo; ignora o ruído
        return atual + novo                   # sem lista de tools: colar é o palpite melhor
