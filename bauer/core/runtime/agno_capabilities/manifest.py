"""Reviewed Bauer policy for Agno toolkits.

The SDK catalog (``discovery``) says *what exists*. This manifest says *what
Bauer has reviewed*: the explicit list of actions per toolkit, their risk and
the Bauer policy operation each one is evaluated under. An SDK toolkit absent
from here stays visible as ``unreviewed`` and can never be materialized.

Rules that keep the manifest honest:

* It is code, reviewed like code. Configuration only *selects* ids/actions;
  it never supplies a module path, class or callable.
* Every action is classified individually. Toolkit-level "safe" does not
  exist: a mixed toolkit (read + write) exposes only the actions listed.
* ``write``/``execute``/``external`` actions start blocked
  (``approval_required=True`` without an approval flow ⇒ blocked) until a
  sprint wires their approval path and tests it.
* An Agno upgrade never widens permissions by itself: new SDK functions are
  not in ``actions`` until someone adds them here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActionRisk = Literal["read", "write", "execute", "external"]
_RISK_ORDER: dict[str, int] = {"read": 0, "external": 1, "write": 2, "execute": 3}


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    """One explicitly reviewed toolkit action."""

    name: str
    risk: ActionRisk
    #: Bauer ``PolicyEngine`` operation evaluated by the ToolRouter per call.
    operation: str
    description: str = ""
    #: Writes/side effects need an approval bound to the exact call.
    approval_required: bool = False


@dataclass(frozen=True, slots=True)
class ToolkitPolicy:
    """Reviewed policy for one catalog entry (SDK toolkit or Bauer adapter)."""

    id: str
    title: str
    category: str
    description: str
    actions: tuple[ActionPolicy, ...] = ()
    #: ``agno`` = SDK toolkit wrapped by Bauer; ``bauer`` = Bauer-native
    #: adapter that fills the SDK gap with Bauer's own guarded implementation.
    source: Literal["agno", "bauer"] = "agno"
    #: Fixed import location of the SDK toolkit (code constant, never config).
    module: str | None = None
    class_name: str | None = None
    #: Import names that must resolve for the capability to be usable.
    requires: tuple[str, ...] = ()
    #: Human hint for installing the optional dependency (never auto-run).
    install_hint: str = ""
    #: Settings section (``agno_capabilities.yaml``) that must be present.
    settings_section: str | None = None
    #: Credential names resolved through ``bauer credential``/env. Names only.
    credentials: tuple[str, ...] = ()
    #: Present ⇒ catalog shows ``incompatible`` with this reason.
    incompatible: str | None = None
    #: Free-form reviewer notes shown in doctor/UI (no secrets).
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def action(self, name: str) -> ActionPolicy | None:
        return next((item for item in self.actions if item.name == name), None)

    @property
    def max_risk(self) -> ActionRisk:
        if not self.actions:
            return "read"
        return max(self.actions, key=lambda item: _RISK_ORDER[item.risk]).risk

    @property
    def default_actions(self) -> tuple[ActionPolicy, ...]:
        """Actions granted when an agent names the toolkit without actions.

        Only ``read`` actions: anything with effect must be named explicitly.
        """
        return tuple(item for item in self.actions if item.risk == "read" and not item.approval_required)


def _read(name: str, operation: str, description: str = "") -> ActionPolicy:
    return ActionPolicy(name, "read", operation, description)


def _blocked_duplicate(capability_id: str, title: str, replacement: str) -> ToolkitPolicy:
    return ToolkitPolicy(
        id=capability_id,
        title=title,
        category="Duplica tool Bauer",
        description=f"Toolkit Agno que executa fora do sandbox Bauer; use {replacement}.",
        incompatible=(
            f"Executaria fora do ToolRouter/sandbox do Bauer. Use {replacement}, que "
            "aplica workspace, allowlist, policy e aprovação."
        ),
    )


_CALCULATOR_ACTIONS = tuple(
    _read(name, "compute.local")
    for name in (
        "add",
        "subtract",
        "multiply",
        "divide",
        "exponentiate",
        "factorial",
        "is_prime",
        "square_root",
    )
)


_POLICIES: list[ToolkitPolicy] = [
    ToolkitPolicy(
        id="agno.calculator",
        title="Calculadora",
        category="Utilidades",
        description="Operações aritméticas puras, sem rede nem arquivos.",
        actions=_CALCULATOR_ACTIONS,
        module="agno.tools.calculator",
        class_name="CalculatorTools",
        notes="Primeiro toolkit SDK ligado pela ponte governada (sprint 50).",
    ),
    # Toolkits that would bypass Bauer's own sandboxed implementation. They are
    # catalogued as incompatible on purpose — the Bauer tool is the path.
    _blocked_duplicate("agno.shell", "Shell (Agno)", "bauer.run_command"),
    _blocked_duplicate("agno.python", "Python (Agno)", "bauer.run_command / execute_code"),
    _blocked_duplicate("agno.local_file_system", "Sistema de arquivos local (Agno)", "bauer.write_file"),
    _blocked_duplicate("agno.file", "Arquivos (Agno)", "bauer.read_file / bauer.write_file"),
    _blocked_duplicate("agno.coding", "Coding (Agno)", "bauer.read_file / bauer.write_file / bauer.run_command"),
    _blocked_duplicate("agno.memory", "Memória (Agno)", "bauer.memory"),
]


def register_policy(policy: ToolkitPolicy) -> None:
    """Add a reviewed policy (used by the per-sprint capability modules)."""
    if any(existing.id == policy.id for existing in _POLICIES):
        raise ValueError(f"capability policy already registered: {policy.id}")
    _POLICIES.append(policy)


def reviewed_policies() -> dict[str, ToolkitPolicy]:
    _ensure_extensions_loaded()
    return {policy.id: policy for policy in _POLICIES}


def get_policy(capability_id: str) -> ToolkitPolicy | None:
    return reviewed_policies().get(capability_id.strip())


_EXTENSIONS_LOADED = False


def _ensure_extensions_loaded() -> None:
    """Load the per-family policy modules exactly once (pure Python, no SDK)."""
    global _EXTENSIONS_LOADED
    if _EXTENSIONS_LOADED:
        return
    _EXTENSIONS_LOADED = True
    from . import families  # noqa: F401  (registers policies on import)
