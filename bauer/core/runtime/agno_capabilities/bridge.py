"""Materialize reviewed capabilities as Agno functions governed by ToolRouter.

The single rule of this module: **the model never calls a connector
directly**. For each granted action the bridge

1. builds the implementation lazily (SDK toolkit restricted with
   ``include_tools`` to the granted actions, or a Bauer-native adapter),
2. registers it on the agent's own ``ToolRouter`` instance under a namespaced
   tool name with the manifest's policy operation, risk and timeout,
3. hands Agno a ``Function`` whose entrypoint only calls
   ``router.execute_native_call`` — so context allowlist, PolicyEngine,
   approvals, run/session binding, tool budget, timeouts, secret scanning and
   (redacted) tool events all apply exactly as for built-in Bauer tools.

Factories live in a code allowlist (``register_factory``); configuration can
never point at a module path or callable.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ....execution_control import ExecutionControlError
from ....privacy import redact_text
from ....provider_retry import ProviderRetryInterrupted
from ....tools.base import ToolError
from .catalog import CapabilityError, ResolvedCapability, resolve_capability
from .settings import CapabilitySettings, StrictSettings, load_capability_settings

logger = logging.getLogger("bauer.agno_capabilities")

#: Output cap per call; protects context window and telemetry previews.
MAX_RESULT_CHARS = 20_000
DEFAULT_TIMEOUT_SECONDS = 60

_RISK_TO_SECURITY = {
    "read": ("read", "low"),
    "external": ("network", "medium"),
    "write": ("write", "high"),
    "execute": ("execute", "high"),
}


@dataclass(slots=True)
class ActionImpl:
    """Concrete callable for one granted action plus its JSON schema."""

    fn: Callable[..., Any]
    description: str = ""
    parameters: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []}
    )
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class FactoryContext:
    """Per-agent facts a factory may scope itself to (never from the model)."""

    workspace: Path | None = None


#: ``factory(resolved, section, context=FactoryContext)`` → implementations.
Factory = Callable[..., dict[str, ActionImpl]]
_FACTORIES: dict[str, Factory] = {}


def register_factory(capability_id: str, factory: Factory) -> None:
    """Register the code factory for a reviewed capability (code allowlist)."""
    _FACTORIES[capability_id] = factory


def factory_for(resolved: ResolvedCapability) -> Factory:
    factory = _FACTORIES.get(resolved.policy.id)
    if factory is not None:
        return factory
    if resolved.policy.module and resolved.policy.class_name:
        return sdk_toolkit_factory
    raise CapabilityError(
        f"capability '{resolved.policy.id}' não tem factory Bauer registrada.",
        state="incompatible",
        capability_id=resolved.policy.id,
    )


def sdk_toolkit_factory(
    resolved: ResolvedCapability,
    section: StrictSettings | None,
    context: FactoryContext | None = None,
    *,
    init_kwargs: dict[str, Any] | None = None,
) -> dict[str, ActionImpl]:
    """Instantiate the reviewed SDK toolkit restricted to the granted actions."""
    policy = resolved.policy
    assert policy.module and policy.class_name
    if not policy.module.startswith("agno.tools."):
        raise CapabilityError(
            f"módulo fora de agno.tools recusado para '{policy.id}'",
            state="incompatible",
            capability_id=policy.id,
        )
    module = importlib.import_module(policy.module)
    toolkit_cls = getattr(module, policy.class_name)
    names = [action.name for action in resolved.actions]
    toolkit = toolkit_cls(include_tools=names, **(init_kwargs or {}))
    functions = dict(toolkit.get_functions())
    impls: dict[str, ActionImpl] = {}
    for name in names:
        function = functions.get(name)
        if function is None or function.entrypoint is None:
            raise CapabilityError(
                f"ação '{name}' não existe em {policy.class_name} na versão instalada do Agno.",
                state="incompatible",
                capability_id=policy.id,
            )
        impls[name] = ActionImpl(
            fn=function.entrypoint,
            description=_describe(function),
            parameters=_parameters_for(function.entrypoint),
        )
    return impls


def _describe(function: Any) -> str:
    description = getattr(function, "description", None)
    if description:
        return str(description)
    doc = getattr(function.entrypoint, "__doc__", None) or ""
    return doc.strip().split("\n\n")[0].strip()


def _parameters_for(entrypoint: Callable[..., Any]) -> dict[str, Any]:
    try:
        from agno.tools.function import Function

        processed = Function.from_callable(entrypoint)
        params = deepcopy(processed.parameters or {})
        if params.get("type") == "object":
            return params
    except Exception as exc:  # noqa: BLE001 - schema derivation is best-effort
        logger.debug("schema derivation failed: %s", type(exc).__name__)
    return {"type": "object", "properties": {}, "required": []}


def tool_name_for(capability_id: str, action: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", capability_id.lower()).strip("_")
    return f"{slug}__{action}"


def stringify_result(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f"\n[... truncado em {MAX_RESULT_CHARS} caracteres]"
    return text


@dataclass(slots=True)
class GovernedAction:
    """A granted action ready to become an Agno ``Function``."""

    tool_name: str
    capability_id: str
    action: str
    description: str
    parameters: dict[str, Any]
    entrypoint: Callable[..., str]


def materialize(
    value: str,
    *,
    router_factory: Callable[[], Any],
    settings: CapabilitySettings | None = None,
    workspace: Path | None = None,
) -> list[GovernedAction]:
    """Resolve ``value`` and return governed actions bound to the router.

    Raises ``CapabilityError`` before anything is instantiated when the
    reference is unknown, unreviewed, not configured or not allowed.
    """
    if settings is None:
        settings = load_capability_settings()
    resolved = resolve_capability(value, settings=settings)
    section = settings.section(resolved.policy.settings_section or resolved.policy.id)
    try:
        impls = factory_for(resolved)(resolved, section, context=FactoryContext(workspace=workspace))
    except CapabilityError:
        raise
    except ImportError as exc:
        raise CapabilityError(
            f"capability '{resolved.policy.id}' não pôde carregar dependência opcional ({exc.name or 'desconhecida'}).",
            state="dependency_missing",
            capability_id=resolved.policy.id,
        ) from None
    except Exception as exc:  # noqa: BLE001 - never leak connector error text
        raise CapabilityError(
            f"capability '{resolved.policy.id}' falhou ao inicializar ({type(exc).__name__}).",
            state="needs_configuration",
            capability_id=resolved.policy.id,
        ) from None

    governed: list[GovernedAction] = []
    for action in resolved.actions:
        impl = impls.get(action.name)
        if impl is None:
            raise CapabilityError(
                f"factory de '{resolved.policy.id}' não implementa a ação '{action.name}'.",
                state="incompatible",
                capability_id=resolved.policy.id,
            )
        tool_name = tool_name_for(resolved.policy.id, action.name)
        permission, risk = _RISK_TO_SECURITY[action.risk]
        registration = {
            "description": impl.description,
            "args": {
                name: str(spec.get("description") or spec.get("type") or "")
                for name, spec in (impl.parameters.get("properties") or {}).items()
                if isinstance(spec, dict)
            },
            "operation": action.operation,
            "permission": permission,
            "risk": risk,
            "capability": resolved.policy.id,
            "action": action.name,
            "timeout_seconds": impl.timeout_seconds,
        }
        governed.append(
            GovernedAction(
                tool_name=tool_name,
                capability_id=resolved.policy.id,
                action=action.name,
                description=(impl.description or action.description or action.name)[:1024],
                parameters=deepcopy(impl.parameters),
                entrypoint=_governed_entrypoint(tool_name, impl.fn, registration, router_factory),
            )
        )
    logger.info(
        "agno capability granted id=%s actions=%s",
        resolved.policy.id,
        ",".join(action.name for action in resolved.actions),
    )
    return governed


#: Operations whose arguments leave the machine: refuse secret material in
#: them (the ``network.http`` "contains_secret" policy rule has no producer).
_OUTBOUND_OPERATIONS = frozenset({"network.http", "integration.write", "mcp.call"})


def _refuse_secret_material(call_args: dict[str, Any]) -> None:
    try:
        from ....secrets_scanner import scan

        text = json.dumps(call_args, ensure_ascii=False, default=str)
        found = scan(text, redact=False).found
    except Exception:  # noqa: BLE001 - scanner failure must not open the gate silently
        found = False
    if found:
        raise ToolError("argumentos contêm material secreto; envio externo bloqueado")


def _governed_entrypoint(
    tool_name: str,
    fn: Callable[..., Any],
    registration: dict[str, Any],
    router_factory: Callable[[], Any],
) -> Callable[..., str]:
    """Closure (no defaulted kwargs a model could override) routing via ToolRouter."""

    outbound = str(registration.get("operation") or "") in _OUTBOUND_OPERATIONS

    def invoke(call_args: dict[str, Any]) -> str:
        if outbound:
            _refuse_secret_material(call_args)
        try:
            return stringify_result(fn(**call_args))
        except ToolError:
            raise
        except (ProviderRetryInterrupted, ExecutionControlError):
            raise
        except TypeError as exc:
            raise ToolError(f"{tool_name} argumentos inválidos (TypeError): {redact_text(str(exc))[:300]}") from None
        except Exception as exc:  # noqa: BLE001 - connector errors become typed tool errors
            raise ToolError(
                f"{tool_name} falhou ({type(exc).__name__}): {redact_text(str(exc))[:300]}"
            ) from None

    def entrypoint(**kwargs: Any) -> str:
        router = router_factory()
        if not router.has_capability_tool(tool_name):
            router.register_capability_tool(tool_name, invoke, **registration)
        return router.execute_native_call(tool_name, kwargs)

    return entrypoint


def to_agno_functions(actions: list[GovernedAction], *, guard: Callable[[Callable[..., Any]], Callable[..., Any]]) -> list[Any]:
    from agno.tools.function import Function

    functions = []
    for item in actions:
        functions.append(
            Function(
                name=item.tool_name,
                description=item.description,
                parameters=deepcopy(item.parameters),
                entrypoint=guard(item.entrypoint),
                skip_entrypoint_processing=True,
            )
        )
    return functions
