"""Capability state, agent references and fail-closed resolution.

Joins three sources, none of which imports a connector:

* ``discovery`` — what the installed SDK ships (AST metadata);
* ``manifest`` — what Bauer reviewed (actions, risk, operation);
* ``settings`` — what the operator scoped (connections, repos, regions).

States (public, stable strings):

``enabled``               reviewed, installed, configured, assigned to the agent
``ready``                 same, but not assigned to the agent being shown
``needs_configuration``   reviewed; missing settings section or credential
``dependency_missing``    reviewed; an optional package is not installed
``blocked_by_policy``     requested action is classified but not allowed yet
``incompatible``          reviewed and intentionally not offered (reason given)
``unreviewed``            exists in the SDK but has no Bauer policy: visible, inert
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .credentials import credential_available
from .discovery import SdkToolkitModule, discover_sdk_modules, module_spec_exists
from .manifest import ActionPolicy, ToolkitPolicy, reviewed_policies
from .settings import CapabilitySettings, CapabilitySettingsError, load_capability_settings

STATES = (
    "enabled",
    "ready",
    "needs_configuration",
    "dependency_missing",
    "blocked_by_policy",
    "incompatible",
    "unreviewed",
)

_REF = re.compile(r"^(?P<id>[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)(?::(?P<actions>[A-Za-z0-9_,\s]+))?$")


class CapabilityError(ValueError):
    """A capability reference cannot be granted. ``state`` says why."""

    def __init__(self, message: str, *, state: str, capability_id: str = "") -> None:
        super().__init__(message)
        self.state = state
        self.capability_id = capability_id


@dataclass(frozen=True, slots=True)
class CapabilityRef:
    id: str
    actions: tuple[str, ...] = ()

    def __str__(self) -> str:
        return self.id + (":" + ",".join(self.actions) if self.actions else "")


@dataclass(frozen=True, slots=True)
class ResolvedCapability:
    policy: ToolkitPolicy
    actions: tuple[ActionPolicy, ...]
    ref: CapabilityRef


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    state: str
    missing: tuple[str, ...] = ()
    reason: str = ""


def parse_capability_ref(value: str) -> CapabilityRef:
    text = str(value or "").strip()
    match = _REF.match(text)
    if not match:
        raise CapabilityError(
            f"referência de capability inválida: {text!r} (use 'id' ou 'id:acao1,acao2')",
            state="unreviewed",
        )
    actions = tuple(
        dict.fromkeys(item.strip() for item in (match.group("actions") or "").split(",") if item.strip())
    )
    return CapabilityRef(match.group("id"), actions)


def is_reviewed_capability(value: str) -> bool:
    """Whether ``value`` names a manifest capability (with or without actions)."""
    try:
        ref = parse_capability_ref(value)
    except CapabilityError:
        return False
    return ref.id in reviewed_policies()


def sdk_modules_by_id() -> dict[str, SdkToolkitModule]:
    return {item.id: item for item in discover_sdk_modules()}


def capability_status(
    policy: ToolkitPolicy,
    *,
    settings: CapabilitySettings | None,
    sdk: dict[str, SdkToolkitModule] | None = None,
) -> CapabilityStatus:
    if policy.incompatible:
        return CapabilityStatus("incompatible", reason=policy.incompatible)
    sdk = sdk if sdk is not None else sdk_modules_by_id()
    if policy.source == "agno":
        if not module_spec_exists("agno"):
            return CapabilityStatus("dependency_missing", ("agno",), "SDK Agno não instalado (extra 'agno').")
        if policy.id not in sdk:
            return CapabilityStatus(
                "incompatible",
                reason="Toolkit não existe na versão do Agno instalada; manifesto precisa de revisão.",
            )
    missing_deps = tuple(name for name in policy.requires if not module_spec_exists(name))
    if missing_deps:
        hint = f" Instale: {policy.install_hint}." if policy.install_hint else ""
        return CapabilityStatus(
            "dependency_missing", missing_deps, f"Dependência opcional ausente: {', '.join(missing_deps)}.{hint}"
        )
    missing_config: list[str] = []
    if policy.settings_section and (settings is None or policy.settings_section not in settings):
        missing_config.append(f"agno_capabilities.yaml: capabilities.{policy.settings_section}")
    missing_config.extend(name for name in policy.credentials if not credential_available(name))
    if missing_config:
        return CapabilityStatus(
            "needs_configuration",
            tuple(missing_config),
            "Configuração/credencial ausente; nada é habilitado por padrão.",
        )
    return CapabilityStatus("ready")


def resolve_capability(
    value: str,
    *,
    settings: CapabilitySettings | None = None,
    sdk: dict[str, SdkToolkitModule] | None = None,
) -> ResolvedCapability:
    """Fail-closed: return the exact actions an agent may receive, or raise."""
    ref = parse_capability_ref(value)
    policy = reviewed_policies().get(ref.id)
    if policy is None:
        known_sdk = (sdk if sdk is not None else sdk_modules_by_id()).get(ref.id)
        if known_sdk is not None:
            raise CapabilityError(
                f"capability '{ref.id}' existe no SDK Agno mas ainda não foi revisada pelo Bauer; "
                "permanece desabilitada até receber policy explícita.",
                state="unreviewed",
                capability_id=ref.id,
            )
        raise CapabilityError(
            f"capability '{ref.id}' não existe no catálogo Bauer/Agno.",
            state="unreviewed",
            capability_id=ref.id,
        )
    if settings is None:
        settings = load_capability_settings()
    status = capability_status(policy, settings=settings, sdk=sdk)
    if status.state != "ready":
        detail = status.reason or status.state
        if status.missing:
            detail += f" Faltando: {', '.join(status.missing)}."
        raise CapabilityError(
            f"capability '{ref.id}' indisponível ({status.state}): {detail}",
            state=status.state,
            capability_id=ref.id,
        )
    if ref.actions:
        actions: list[ActionPolicy] = []
        for name in ref.actions:
            action = policy.action(name)
            if action is None:
                raise CapabilityError(
                    f"ação '{name}' não está classificada para '{ref.id}'. "
                    f"Ações revisadas: {', '.join(a.name for a in policy.actions) or 'nenhuma'}.",
                    state="blocked_by_policy",
                    capability_id=ref.id,
                )
            actions.append(action)
    else:
        actions = list(policy.default_actions)
        if not actions:
            raise CapabilityError(
                f"capability '{ref.id}' não tem ações de leitura padrão; declare as ações explicitamente.",
                state="blocked_by_policy",
                capability_id=ref.id,
            )
    for action in actions:
        _check_action_allowed(policy, action)
    return ResolvedCapability(policy, tuple(actions), ref)


def _check_action_allowed(policy: ToolkitPolicy, action: ActionPolicy) -> None:
    if action.risk != "read" and not action.approval_required:
        # Manifest invariant: anything with effect must go through approval.
        raise CapabilityError(
            f"ação '{action.name}' de '{policy.id}' tem risco {action.risk} sem aprovação declarada.",
            state="blocked_by_policy",
            capability_id=policy.id,
        )
    if action.approval_required and not approval_flow_enabled(policy.id, action.name):
        raise CapabilityError(
            f"ação '{action.name}' de '{policy.id}' exige aprovação e ainda não tem fluxo de aprovação habilitado.",
            state="blocked_by_policy",
            capability_id=policy.id,
        )


_APPROVAL_FLOWS: set[tuple[str, str]] = set()


def enable_approval_flow(capability_id: str, action: str) -> None:
    """Called by a family module once its approval path is wired and tested."""
    _APPROVAL_FLOWS.add((capability_id, action))


def approval_flow_enabled(capability_id: str, action: str) -> bool:
    reviewed_policies()
    return (capability_id, action) in _APPROVAL_FLOWS


def validate_agent_tools(tools: Iterable[str], *, settings: CapabilitySettings | None = None) -> list[str]:
    """Return human-readable problems for an agent's Agno capability list."""
    problems: list[str] = []
    for tool in tools:
        text = str(tool or "").strip()
        if not text.startswith("agno.") and not is_reviewed_capability(text):
            continue
        try:
            resolve_capability(text, settings=settings)
        except CapabilityError as exc:
            problems.append(str(exc))
        except CapabilitySettingsError as exc:
            problems.append(str(exc))
    return problems


def catalog_entries(
    *,
    agent_tools: Iterable[str] | None = None,
    settings: CapabilitySettings | None = None,
) -> list[dict[str, Any]]:
    """Public, secret-free catalog rows: SDK toolkits + Bauer-native adapters."""
    assigned: dict[str, tuple[str, ...]] = {}
    for tool in agent_tools or []:
        try:
            ref = parse_capability_ref(str(tool))
        except CapabilityError:
            continue
        assigned[ref.id] = ref.actions
    settings_error = ""
    if settings is None:
        try:
            settings = load_capability_settings()
        except CapabilitySettingsError as exc:
            settings_error = str(exc)
            settings = CapabilitySettings()
    sdk = sdk_modules_by_id()
    policies = reviewed_policies()
    rows: list[dict[str, Any]] = []
    for capability_id in sorted(set(policies) | {item.id for item in sdk.values() if item.is_toolkit}):
        policy = policies.get(capability_id)
        module = sdk.get(capability_id)
        if policy is None:
            assert module is not None
            rows.append(_unreviewed_row(module))
            continue
        status = capability_status(policy, settings=settings, sdk=sdk)
        state = status.state
        if state == "ready" and capability_id in assigned:
            state = "enabled"
        rows.append(
            {
                "id": policy.id,
                "name": policy.title,
                "category": policy.category,
                "description": policy.description,
                "source": policy.source,
                "review": "incompatible" if policy.incompatible else "reviewed",
                "actions": [action.name for action in policy.actions],
                "action_risks": {action.name: action.risk for action in policy.actions},
                "default_actions": [action.name for action in policy.default_actions],
                "risk": policy.max_risk,
                "factory_registered": policy.incompatible is None,
                "package": policy.install_hint or (module.pip_hints[0] if module and module.pip_hints else None),
                "module_family": False,
                "required_config": [
                    *( [f"capabilities.{policy.settings_section}"] if policy.settings_section else [] ),
                    *policy.credentials,
                ],
                "missing": list(status.missing),
                "reason": status.reason or settings_error,
                "notes": policy.notes,
                "state": state,
                "enabled_for_agent": state == "enabled",
                "installed": state != "dependency_missing",
            }
        )
    return rows


def _unreviewed_row(module: SdkToolkitModule) -> dict[str, Any]:
    return {
        "id": module.id,
        "name": module.toolkit_classes[0] if module.toolkit_classes else module.id,
        "category": "Agno SDK (não revisado)",
        "description": "Toolkit presente no SDK instalado; aguarda revisão de ações e risco antes de poder ser habilitado.",
        "source": "agno",
        "review": "unreviewed",
        "actions": [],
        "action_risks": {},
        "default_actions": [],
        "sdk_functions": list(module.functions),
        "risk": "external",
        "factory_registered": False,
        "package": module.pip_hints[0] if module.pip_hints else None,
        "module_family": False,
        "required_config": list(module.env_vars),
        "missing": [],
        "reason": "Sem policy Bauer revisada.",
        "notes": "",
        "state": "unreviewed",
        "enabled_for_agent": False,
        "installed": all(module_spec_exists(name) for name in module.optional_imports),
    }
