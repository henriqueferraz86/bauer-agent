"""Plano 057 / sprint 58 — cobertura do catálogo e contratos por família.

Nenhum toolkit com rede é chamado: os contratos usam metadados (AST), a
resolução fail-closed e factories com SDK monkeypatchado.
"""

from __future__ import annotations

import builtins
import json
import sys
import types

import pytest

from bauer.core.runtime.agno_capabilities import catalog, coverage
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability
from bauer.core.runtime.agno_capabilities.discovery import discover_sdk_modules
from bauer.core.runtime.agno_capabilities.manifest import reviewed_policies

pytest.importorskip("agno")


def test_every_sdk_module_has_explicit_state_and_justification():
    matrix = coverage.coverage_matrix()
    by_id = {row["id"]: row for row in matrix["rows"]}
    for module in discover_sdk_modules():
        row = by_id[module.id]
        assert row["state"] != "unreviewed", module.id
        assert row["justification"], module.id
    assert matrix["agno_version"]
    assert matrix["totals"]["unreviewed"] == 0
    assert sum(matrix["totals"].values()) == len(matrix["rows"])


def test_coverage_is_reproducible_and_grouped_by_category():
    first = coverage.coverage_matrix()
    second = coverage.coverage_matrix()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert {"Pesquisa e conteúdo web", "Mensageria e colaboração", "Dados"} <= set(first["by_category"])
    markdown = coverage.coverage_markdown(first)
    assert "| agno.slack |" in markdown and "blocked_by_policy" in markdown


def test_snapshot_matches_installed_sdk():
    diff = coverage.snapshot_diff()
    assert diff == {"added": [], "removed": [], "changed": []}, (
        "O catálogo do Agno instalado mudou. Revise e regenere o snapshot com "
        "`bauer tools capabilities --write-snapshot`."
    )


def test_sdk_bump_produces_reviewable_diff_without_enabling(monkeypatch):
    from bauer.core.runtime.agno_capabilities.discovery import SdkToolkitModule

    real = list(discover_sdk_modules())
    new = SdkToolkitModule(id="agno.brand_new", module="agno.tools.brand_new", toolkit_classes=("NewTools",),
                           functions=("delete_everything",))
    changed = [m for m in real if m.id != "agno.calculator"]
    calc = next(m for m in real if m.id == "agno.calculator")
    changed.append(SdkToolkitModule(id=calc.id, module=calc.module, toolkit_classes=calc.toolkit_classes,
                                    functions=calc.functions + ("launch_rocket",)))
    monkeypatch.setattr(coverage, "discover_sdk_modules", lambda: tuple(changed + [new]))
    monkeypatch.setattr(catalog, "sdk_modules_by_id", lambda: {m.id: m for m in changed + [new]})

    diff = coverage.snapshot_diff()
    assert "agno.brand_new" in diff["added"]
    assert any(item["id"] == "agno.calculator" and "launch_rocket" in item["functions_added"] for item in diff["changed"])
    row = next(r for r in catalog_entries() if r["id"] == "agno.brand_new")
    assert row["state"] == "unreviewed"
    with pytest.raises(CapabilityError):
        resolve_capability("agno.calculator:launch_rocket")


# ── contratos por família (todas as políticas SDK revisadas) ────────────────

_READ_SDK = sorted(
    policy.id for policy in reviewed_policies().values()
    if policy.source == "agno" and not policy.incompatible and not policy.blocked and policy.actions
)


@pytest.mark.parametrize("capability_id", _READ_SDK)
def test_family_contract_lazy_schema_allowlist_and_risk(capability_id, monkeypatch):
    policy = reviewed_policies()[capability_id]
    # lazy: listing never imports the toolkit module
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == policy.module:
            raise AssertionError(f"catálogo importou {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert any(row["id"] == capability_id for row in catalog_entries())
    monkeypatch.setattr(builtins, "__import__", original_import)

    # schema: every reviewed action exists in the SDK (AST) or is inherited
    module = {m.id: m for m in discover_sdk_modules()}[capability_id]
    assert module.toolkit_classes
    # risk: reviewed SDK actions in this catalog are read-only network/compute
    assert all(action.risk == "read" for action in policy.actions)
    # allowlist: an unclassified action is rejected regardless of state
    with pytest.raises(CapabilityError):
        resolve_capability(f"{capability_id}:definitely_not_an_action")
    # credential kwargs only name constructor parameters seen by AST
    for kwarg, _credential in policy.credential_kwargs:
        assert kwarg in module.init_args, (capability_id, kwarg)


@pytest.mark.parametrize("capability_id", [cid for cid in _READ_SDK if reviewed_policies()[cid].credentials])
def test_family_contract_missing_credential_is_not_ready(capability_id, monkeypatch):
    policy = reviewed_policies()[capability_id]
    for name in policy.credentials:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(catalog, "credential_available", lambda name: False)
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: True)
    row = next(r for r in catalog_entries() if r["id"] == capability_id)
    assert row["state"] == "needs_configuration"
    assert set(policy.credentials) <= set(row["missing"])


def test_credential_from_vault_is_injected_and_never_published(tmp_path, monkeypatch):
    """Factory contract: credential kwarg injected, dependency error typed, redaction kept."""
    from agno.tools import Toolkit

    from bauer.core.events.bus import EventBus
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    seen: dict = {}

    class FakeWeather(Toolkit):
        def __init__(self, api_key=None, **kwargs):
            seen["api_key"] = api_key
            super().__init__(name="openweather", tools=[self.get_current_weather], **kwargs)

        def get_current_weather(self, location: str) -> str:
            return json.dumps({"location": location, "temp": 21})

    fake = types.ModuleType("agno.tools.openweather")
    fake.OpenWeatherTools = FakeWeather
    monkeypatch.setitem(sys.modules, "agno.tools.openweather", fake)
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: True)
    monkeypatch.setattr(catalog, "credential_available", lambda name: True)
    monkeypatch.setattr("bauer.core.runtime.agno_capabilities.bridge.resolve_credential",
                        lambda name: "vault-secret-value-123")
    bus = EventBus(root=tmp_path / "events")
    (tmp_path / "ws").mkdir()
    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(tmp_path / "ws")},
                                 runtime_policy_root=tmp_path / "rt", runtime_event_bus=bus)
    fn = {f.name: f for f in adapter._map_tools(["agno.openweather:get_current_weather"])}["agno_openweather__get_current_weather"]

    assert json.loads(fn.entrypoint(location="Recife"))["temp"] == 21
    assert seen["api_key"] == "vault-secret-value-123"
    dump = json.dumps([e.data for e in bus.list_events()] + [e.message for e in bus.list_events()])
    assert "vault-secret-value" not in dump and "Recife" not in dump


def test_missing_optional_dependency_raises_typed_error(monkeypatch, tmp_path):
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from bauer.core.runtime.adapters.base import RuntimeAdapterError

    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: True)
    monkeypatch.setattr(catalog, "credential_available", lambda name: True)
    monkeypatch.setitem(sys.modules, "agno.tools.yfinance", None)  # import fails
    (tmp_path / "ws").mkdir()
    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(tmp_path / "ws")}, runtime_policy_root=tmp_path / "rt")
    with pytest.raises(RuntimeAdapterError, match="dependency_missing|dependência"):
        adapter._map_tools(["agno.yfinance:get_current_stock_price"])


def test_blocked_connectors_are_visible_but_never_granted():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in ("agno.slack", "agno.google.gmail", "agno.whatsapp", "agno.dalle"):
        assert rows[capability_id]["state"] == "blocked_by_policy"
        assert rows[capability_id]["reason"]
        with pytest.raises(CapabilityError) as exc:
            resolve_capability(capability_id)
        assert exc.value.state == "blocked_by_policy"


def test_cli_lists_filters_and_writes_nothing(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from bauer.commands.tools_cmd import tools_app

    result = CliRunner().invoke(tools_app, ["capabilities", "--state", "blocked_by_policy", "--search", "slack", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [row["id"] for row in data] == ["agno.slack"]
    coverage_out = CliRunner().invoke(tools_app, ["capabilities", "--coverage"])
    assert coverage_out.exit_code == 0 and "unreviewed" in coverage_out.output
