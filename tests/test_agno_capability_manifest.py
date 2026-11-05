"""Contrato do manifesto Agno (plano 057): vale para todas as famílias.

Cada ação revisada de um toolkit do SDK precisa existir na classe da versão
instalada; risco e operação precisam ser coerentes; IDs são únicos.
"""

from __future__ import annotations

import importlib

import pytest

from bauer.core.runtime.agno_capabilities.catalog import _REF
from bauer.core.runtime.agno_capabilities.discovery import discover_sdk_modules, module_spec_exists
from bauer.core.runtime.agno_capabilities.manifest import reviewed_policies

pytest.importorskip("agno")

_KNOWN_OPERATIONS_PREFIXES = (
    "network.http", "compute.local", "integration.", "mcp.", "data.", "browser.", "infra.",
    "workflow.", "memory.", "filesystem.", "os.ui_control",
)


def test_reviewed_ids_are_valid_references():
    for capability_id in reviewed_policies():
        assert _REF.match(capability_id), capability_id


def test_every_reviewed_sdk_action_exists_in_installed_agno():
    modules = {item.id: item for item in discover_sdk_modules()}
    for policy in reviewed_policies().values():
        if policy.source != "agno" or policy.incompatible:
            continue
        assert policy.id in modules, f"{policy.id} não existe no SDK instalado"
        assert policy.module and policy.class_name
        discovered = set(modules[policy.id].functions)
        missing = [a.name for a in policy.actions if a.name not in discovered]
        if not missing:
            continue
        # Inherited tools (e.g. DuckDuckGoTools ← WebSearchTools): check the class.
        if not all(module_spec_exists(dep) for dep in policy.requires):
            pytest.fail(f"{policy.id}: ações {missing} não encontradas por AST e dependência ausente")
        cls = getattr(importlib.import_module(policy.module), policy.class_name)
        assert all(callable(getattr(cls, name, None)) for name in missing), (policy.id, missing)


def test_effectful_actions_always_require_approval():
    for policy in reviewed_policies().values():
        for action in policy.actions:
            if action.risk != "read":
                assert action.approval_required, (policy.id, action.name)
            assert action.operation.startswith(_KNOWN_OPERATIONS_PREFIXES), (policy.id, action.operation)


def test_incompatible_entries_explain_why():
    for policy in reviewed_policies().values():
        if policy.incompatible:
            assert len(policy.incompatible) > 20
            assert not policy.actions
