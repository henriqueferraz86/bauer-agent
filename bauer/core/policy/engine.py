"""Policy Engine MVP."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..runtime.autonomy import BudgetExceededError, BudgetManager
from .risk import RiskClassifier


@dataclass(slots=True)
class PolicyDecision:
    action: str
    reason: str
    risk_level: str
    matched_rules: list[str] = field(default_factory=list)


DEFAULT_RULES: list[dict[str, Any]] = [
    {"id": "os.open_app.allow", "operation": "os.open_app", "action": "allow"},
    {"id": "network.http.allow", "operation": "network.http", "action": "allow"},
    {"id": "agent.delegate.allow", "operation": "agent.delegate", "action": "allow"},
    {"id": "shell.execute.ask", "operation": "shell.execute", "action": "ask"},
    {"id": "filesystem.delete.ask", "operation": "filesystem.delete", "action": "ask"},
    {"id": "social.publish.ask", "operation": "social.publish", "action": "ask"},
    {"id": "os.ui_control.ask", "operation": "os.ui_control", "action": "ask"},
    {"id": "filesystem.read.allow", "operation": "filesystem.read", "action": "allow"},
    {
        "id": "filesystem.write.outside_workspace.ask",
        "operation": "filesystem.write",
        "action": "ask",
        "when": {"outside_workspace": True},
    },
]


class PolicyEngine:
    def __init__(
        self,
        *,
        workspace: str | Path = "workspace",
        rules_path: str | Path | None = None,
        rules: list[dict[str, Any]] | None = None,
        runtime_root: str | Path = "memory/runtime",
    ) -> None:
        self.workspace = Path(workspace)
        self.risk = RiskClassifier(self.workspace)
        self.rules = rules if rules is not None else self._load_rules(rules_path)
        self.budget_manager = BudgetManager(root=runtime_root)

    def evaluate(self, operation: str, payload: dict[str, Any] | None = None) -> PolicyDecision:
        payload = payload or {}
        if operation == "runtime.execute":
            try:
                self.budget_manager.ensure_can_start(
                    agent_id=str(payload.get("agent_id") or "default"),
                    company_id=str(payload.get("company_id") or "") or None,
                    estimated_cost_usd=float(payload.get("estimated_cost_usd") or 0),
                )
            except BudgetExceededError as exc:
                return PolicyDecision(
                    action="deny",
                    reason=str(exc),
                    risk_level="high",
                    matched_rules=["budget.exceeded"],
                )
        if operation == "skill.execute":
            permissions = payload.get("permissions") or []
            for permission in permissions:
                decision = self.evaluate(str(permission), payload)
                if decision.action in {"deny", "ask"}:
                    return PolicyDecision(
                        action=decision.action,
                        reason=f"skill permission {permission}: {decision.reason}",
                        risk_level=decision.risk_level,
                        matched_rules=decision.matched_rules,
                    )
            return PolicyDecision(
                action="allow",
                reason="skill permissions allowed",
                risk_level="low",
                matched_rules=[],
            )
        risk_level = self.risk.classify(operation, payload)
        matched_rules: list[str] = []
        for rule in self.rules:
            if rule.get("operation") != operation:
                continue
            if not self._matches_when(rule.get("when"), payload):
                continue
            rule_id = str(rule.get("id") or operation)
            matched_rules.append(rule_id)
            action = str(rule.get("action") or "allow").lower()
            reason = str(rule.get("reason") or f"matched policy rule {rule_id}")
            return PolicyDecision(action=action, reason=reason, risk_level=risk_level, matched_rules=matched_rules)
        return PolicyDecision(
            action="allow",
            reason="no blocking policy rule matched",
            risk_level=risk_level,
            matched_rules=matched_rules,
        )

    def _load_rules(self, rules_path: str | Path | None) -> list[dict[str, Any]]:
        candidates = []
        if rules_path is not None:
            candidates.append(Path(rules_path))
        candidates.extend([self.workspace / ".bauer" / "policy.yaml", Path("config") / "policy.yaml"])
        for candidate in candidates:
            if not candidate.exists():
                continue
            raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and isinstance(raw.get("rules"), list):
                return [rule for rule in raw["rules"] if isinstance(rule, dict)]
        return list(DEFAULT_RULES)

    def _matches_when(self, when: Any, payload: dict[str, Any]) -> bool:
        if not when:
            return True
        if not isinstance(when, dict):
            return True
        if "outside_workspace" in when:
            expected = bool(when["outside_workspace"])
            return self.risk._outside_workspace(payload.get("path")) is expected
        return True
