"""Governed Agno capability layer (plano 057).

* ``discovery`` — static (AST) view of the toolkits shipped by the SDK;
* ``manifest``  — Bauer-reviewed actions, risks and policy operations;
* ``settings``  — operator scope in ``$BAUER_HOME/agno_capabilities.yaml``;
* ``catalog``   — states, agent references and fail-closed resolution;
* ``bridge``    — materialization routed through the agent's ToolRouter.
"""

from __future__ import annotations

from .catalog import (
    STATES,
    CapabilityError,
    CapabilityRef,
    ResolvedCapability,
    catalog_entries,
    is_reviewed_capability,
    parse_capability_ref,
    resolve_capability,
    validate_agent_tools,
)

__all__ = [
    "STATES",
    "CapabilityError",
    "CapabilityRef",
    "ResolvedCapability",
    "catalog_entries",
    "is_reviewed_capability",
    "parse_capability_ref",
    "resolve_capability",
    "validate_agent_tools",
]
