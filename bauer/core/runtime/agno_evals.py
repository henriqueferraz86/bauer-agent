"""Agno evals over Bauer traces + structured decision memory (plano 057, sprint 56).

Two pieces, both additive and opt-in:

1. **Reliability eval of tool use.** Agno's ``ReliabilityEval`` checks which
   tools a run actually executed (clean executions only) against expectations.
   ``trace_from_events`` rebuilds that evidence from Bauer's own EventBus
   (``tool.call.completed`` = clean, ``tool.call.failed``/``tool.denied`` =
   errored), so the eval runs over *governed* runs without an Agno db, without
   Agno telemetry and without re-running a model. Argument checks only work
   for tools whose events carry arguments — capability tools publish argument
   *names* only, by design (plan 057 invariant 6).

   An eval verdict is advisory: it never completes, fails or retries a run.
   Kernel gates stay the only terminal authority.

2. **Structured decisions** (``CapabilityDecisionLog``) on top of the existing
   ``DecisionMemory`` (SQLite): selected agent/tool, alternatives, outcome,
   short evidence and human feedback, with schema version, scope, expiry and
   deletion. No chain-of-thought: free-text fields are short, redacted and
   keys that smell like private reasoning are refused. Automatic recording is
   off unless ``bauer.decisions.auto_record`` is set in
   ``agno_capabilities.yaml``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from pydantic import Field, field_validator

from ...privacy import redact_text
from .agno_capabilities.settings import StrictSettings, register_settings_model

SCHEMA_VERSION = 1
_DECISION_TAG = "agno-decision"
_KINDS = frozenset({"agent_selection", "tool_use", "delegation", "final_answer"})
_OUTCOMES = frozenset({"good", "bad", "neutral"})
_PRIVATE_KEYS = ("reasoning", "thought", "thinking", "chain_of_thought", "cot", "scratchpad", "rationale_full")


# ── 1. reliability eval ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class TraceCall:
    tool_name: str
    ok: bool
    args: dict[str, Any] = field(default_factory=dict)


def trace_from_events(events: Iterable[Any], *, run_id: str | None = None) -> list[TraceCall]:
    """Tool evidence of one run from Bauer events (names; args when present)."""
    calls: list[TraceCall] = []
    for event in events:
        if run_id is not None and getattr(event, "run_id", None) != run_id:
            continue
        kind = getattr(event, "event_type", "")
        name = getattr(event, "tool_name", None)
        if not name:
            continue
        data = getattr(event, "data", None) or {}
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        if "arg_keys" in args:  # capability tool: values never recorded
            args = {}
        if kind == "tool.call.completed":
            calls.append(TraceCall(name, True, dict(args)))
        elif kind in {"tool.call.failed", "tool.denied"}:
            calls.append(TraceCall(name, False, dict(args)))
    return calls


@dataclass(frozen=True)
class ReliabilityVerdict:
    passed: bool
    passed_calls: tuple[str, ...]
    missing_calls: tuple[str, ...]
    additional_calls: tuple[str, ...]
    failed_calls: tuple[str, ...]
    failed_argument_checks: tuple[str, ...]


def evaluate_tool_use(
    calls: list[TraceCall],
    *,
    expected: list[str],
    allow_additional: bool = False,
    expected_arguments: dict[str, Any] | None = None,
) -> ReliabilityVerdict:
    """Run Agno's ``ReliabilityEval`` over a Bauer trace (offline, no db/telemetry)."""
    from agno.eval.reliability import ReliabilityEval
    from agno.models.response import ToolExecution
    from agno.run.agent import RunOutput

    output = RunOutput(
        run_id="bauer-trace",
        tools=[
            ToolExecution(tool_name=call.tool_name, tool_args=dict(call.args), tool_call_error=not call.ok)
            for call in calls
        ],
        messages=[],
    )
    evaluation = ReliabilityEval(
        name="bauer-tool-use",
        agent_response=output,
        expected_tool_calls=list(expected),
        allow_additional_tool_calls=allow_additional,
        expected_tool_call_arguments=expected_arguments,
        print_results=False,
        show_spinner=False,
        telemetry=False,
        db=None,
    )
    result = evaluation.run(print_results=False)
    if result is None:  # pragma: no cover - defensive
        raise RuntimeError("ReliabilityEval não produziu resultado")
    return ReliabilityVerdict(
        passed=result.eval_status == "PASSED",
        passed_calls=tuple(result.passed_tool_calls),
        missing_calls=tuple(result.missing_tool_calls),
        additional_calls=tuple(result.additional_tool_calls),
        failed_calls=tuple(result.failed_tool_calls),
        failed_argument_checks=tuple(result.failed_argument_checks),
    )


# ── 2. structured decisions ─────────────────────────────────────────────────


class DecisionSettings(StrictSettings):
    auto_record: bool = False
    scope: str = Field(default="default", pattern=r"^[a-z][a-z0-9_\-]{0,40}$")
    ttl_days: int = Field(default=90, ge=1, le=3650)

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: str) -> str:
        return value.lower()


register_settings_model("bauer.decisions", DecisionSettings)


class DecisionPrivacyError(ValueError):
    """Refused: the decision carried private reasoning or an invalid field."""


def _short(text: Any, limit: int) -> str:
    value = str(text or "")
    try:
        from ...secrets_scanner import scan

        value = scan(value, redact=True).redacted_text
    except Exception:  # noqa: BLE001 - redact_text below still applies
        value = str(text or "")
    return redact_text(value)[:limit]


@dataclass(frozen=True)
class CapabilityDecision:
    id: str
    kind: str
    agent_id: str
    selected: str
    alternatives: tuple[str, ...]
    outcome: str
    evidence: str
    feedback: str
    scope: str
    version: int
    expires_at: float
    created_at: float


class CapabilityDecisionLog:
    """Structured, scoped, expiring decisions stored in ``DecisionMemory``."""

    def __init__(self, memory: Any) -> None:
        self.memory = memory

    def record(
        self,
        *,
        kind: str,
        agent_id: str,
        selected: str,
        alternatives: Iterable[str] = (),
        outcome: str = "neutral",
        evidence: str = "",
        feedback: str = "",
        scope: str = "default",
        ttl_days: int = 90,
        extra: dict[str, Any] | None = None,
    ) -> str:
        if kind not in _KINDS:
            raise DecisionPrivacyError(f"tipo de decisão inválido: {kind}")
        if outcome not in _OUTCOMES:
            raise DecisionPrivacyError("outcome deve ser good, bad ou neutral")
        for key in (extra or {}):
            if any(marker in str(key).lower() for marker in _PRIVATE_KEYS):
                raise DecisionPrivacyError("raciocínio privado não é persistido; use evidência observável curta")
        now = time.time()
        payload = {
            "v": SCHEMA_VERSION,
            "kind": kind,
            "agent_id": _short(agent_id, 120),
            "selected": _short(selected, 200),
            "alternatives": [_short(item, 200) for item in list(alternatives)[:10]],
            "evidence": _short(evidence, 300),
            "feedback": _short(feedback, 300),
            "expires_at": now + ttl_days * 86_400,
        }
        tags = [_DECISION_TAG, f"scope:{scope}", f"kind:{kind}", f"v:{SCHEMA_VERSION}"]
        return self.memory.record(
            f"{kind}:{payload['agent_id']}",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            outcome=outcome,
            tags=tags,
            score={"good": 0.9, "bad": 0.1}.get(outcome, 0.5),
        )

    def list(self, *, scope: str, include_expired: bool = False, limit: int = 200) -> list[CapabilityDecision]:
        now = time.time()
        result = []
        for record in self.memory.list_recent(limit=max(limit * 5, 500)):
            decision = self._parse(record)
            if decision is None or decision.scope != scope:
                continue
            if not include_expired and decision.expires_at < now:
                continue
            result.append(decision)
            if len(result) >= limit:
                break
        return result

    def delete(self, decision_id: str) -> bool:
        return bool(self.memory.delete(decision_id))

    def purge_expired(self) -> int:
        now = time.time()
        removed = 0
        for record in self.memory.list_recent(limit=100_000):
            decision = self._parse(record)
            if decision is not None and decision.expires_at < now and self.memory.delete(decision.id):
                removed += 1
        return removed

    def purge_scope(self, scope: str) -> int:
        removed = 0
        for decision in self.list(scope=scope, include_expired=True, limit=100_000):
            if self.memory.delete(decision.id):
                removed += 1
        return removed

    @staticmethod
    def _parse(record: Any) -> CapabilityDecision | None:
        tags = list(getattr(record, "tags", []) or [])
        if _DECISION_TAG not in tags:
            return None
        scope = next((tag.split(":", 1)[1] for tag in tags if tag.startswith("scope:")), "")
        try:
            payload = json.loads(record.decision)
        except (TypeError, ValueError):
            return None
        return CapabilityDecision(
            id=record.id,
            kind=str(payload.get("kind")),
            agent_id=str(payload.get("agent_id")),
            selected=str(payload.get("selected")),
            alternatives=tuple(payload.get("alternatives") or ()),
            outcome=record.outcome,
            evidence=str(payload.get("evidence") or ""),
            feedback=str(payload.get("feedback") or ""),
            scope=scope,
            version=int(payload.get("v") or 0),
            expires_at=float(payload.get("expires_at") or 0),
            created_at=float(record.created_at),
        )


def auto_record_tool_use(
    log: CapabilityDecisionLog,
    *,
    agent_id: str,
    calls: list[TraceCall],
    settings: DecisionSettings | None,
) -> list[str]:
    """Record one ``tool_use`` decision per call — only when opted in."""
    if settings is None or not settings.auto_record:
        return []
    ids = []
    for call in calls:
        ids.append(log.record(
            kind="tool_use",
            agent_id=agent_id,
            selected=call.tool_name,
            outcome="good" if call.ok else "bad",
            evidence="executada" if call.ok else "erro ou negada",
            scope=settings.scope,
            ttl_days=settings.ttl_days,
        ))
    return ids
