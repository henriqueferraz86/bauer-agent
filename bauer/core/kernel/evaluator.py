"""Evaluator do Bauer Kernel — quality gates antes de concluir um run.

A única peça genuinamente NOVA do plano de consolidação: tudo o mais o Kernel
compõe de módulos existentes. O Evaluator roda no estado ``evaluating`` (entre
``running`` e ``completed``) e decide se o resultado merece concluir:

- gates plugáveis (callables ou objetos com ``check``), todos precisam passar;
- reprovou e ainda há orçamento de replan → o Kernel volta a ``planning`` e
  re-executa com ``replan_feedback`` no payload;
- esgotou o orçamento → run ``failed`` com o motivo do gate.

Falha de INFRA de um gate (exceção) não reprova o run — gate quebrado é
problema do gate, não do resultado; conta como passed com ressalva no reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class GateResult:
    gate: str
    passed: bool
    reason: str = ""


@dataclass
class Verdict:
    passed: bool
    reason: str = ""
    gates: list[GateResult] = field(default_factory=list)


class QualityGate(Protocol):
    name: str

    def check(self, *, request: Any, result: dict[str, Any]) -> GateResult:
        ...


# ── gates embutidos ───────────────────────────────────────────────────────────


class NonEmptyOutputGate:
    """Output vazio nunca deveria concluir como sucesso silencioso."""

    name = "non_empty_output"

    def check(self, *, request: Any, result: dict[str, Any]) -> GateResult:
        output = str(result.get("output") or "").strip()
        if output:
            return GateResult(self.name, True)
        return GateResult(self.name, False, "output vazio")


class NoTracebackGate:
    """Stacktrace no output = executor 'completou' engolindo um erro."""

    name = "no_traceback"
    _MARKERS = ("Traceback (most recent call last)", "\nSyntaxError:", "\nTypeError:")

    def check(self, *, request: Any, result: dict[str, Any]) -> GateResult:
        output = str(result.get("output") or "")
        for marker in self._MARKERS:
            if marker in output:
                return GateResult(self.name, False, f"output contém erro: {marker.strip()}")
        return GateResult(self.name, True)


class CallableGate:
    """Adapta um callable (request, result) -> bool|str em gate.

    Retorno truthy = passou; string não-vazia = reprovou com aquele motivo."""

    def __init__(self, name: str, fn: Callable[[Any, dict[str, Any]], Any]):
        self.name = name
        self._fn = fn

    def check(self, *, request: Any, result: dict[str, Any]) -> GateResult:
        outcome = self._fn(request, result)
        if isinstance(outcome, str):
            # convenção de string: não-vazia = motivo de reprovação; vazia = ok
            if outcome:
                return GateResult(self.name, False, outcome)
            return GateResult(self.name, True)
        return GateResult(self.name, bool(outcome), "" if outcome else "gate reprovou")


DEFAULT_GATES: tuple[Any, ...] = (NonEmptyOutputGate(), NoTracebackGate())


class Evaluator:
    """Roda os gates em ordem; o primeiro reprovado define o motivo do veredito.

    ``max_replans`` é lido pelo Kernel para limitar o loop evaluating→planning.
    """

    def __init__(self, gates: "list[Any] | tuple[Any, ...] | None" = None,
                 *, max_replans: int = 1):
        self.gates = list(gates) if gates is not None else list(DEFAULT_GATES)
        self.max_replans = max(0, int(max_replans))

    def evaluate(self, *, run_id: str, request: Any, result: dict[str, Any]) -> Verdict:
        results: list[GateResult] = []
        for gate in self.gates:
            try:
                outcome = gate.check(request=request, result=result or {})
            except Exception as exc:  # noqa: BLE001 — gate quebrado não reprova o run
                outcome = GateResult(getattr(gate, "name", "gate"), True,
                                     f"gate falhou ao rodar (ignorado): {exc}")
            results.append(outcome)
            if not outcome.passed:
                return Verdict(False, f"{outcome.gate}: {outcome.reason}", results)
        return Verdict(True, "todos os gates passaram", results)
