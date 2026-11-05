"""Plano 057 / sprint 50 — catálogo Agno completo e ponte governada.

Herméticos: nenhum toolkit com rede é instanciado; o único toolkit real do
SDK usado é ``CalculatorTools`` (puro). Conectores fictícios entram por
``register_factory`` + manifesto monkeypatchado.
"""

from __future__ import annotations

import builtins
import json
import sys
from pathlib import Path

import pytest

from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.adapters.base import RuntimeAdapterError
from bauer.core.runtime.agno_capabilities import bridge, catalog, manifest
from bauer.core.runtime.agno_capabilities.catalog import (
    CapabilityError,
    catalog_entries,
    parse_capability_ref,
    resolve_capability,
    validate_agent_tools,
)
from bauer.core.runtime.agno_capabilities.discovery import discover_sdk_modules
from bauer.core.runtime.agno_capabilities.manifest import ActionPolicy, ToolkitPolicy
from bauer.core.runtime.agno_capabilities.settings import (
    CapabilitySettingsError,
    StrictSettings,
    load_capability_settings,
    parse_settings,
    register_settings_model,
)
from bauer.core.events.bus import EventBus
from bauer.tools.base import ToolError

agno = pytest.importorskip("agno")


# ── helpers ──────────────────────────────────────────────────────────────────


def _adapter(tmp_path: Path, **kwargs) -> AgnoRuntimeAdapter:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    bus = kwargs.pop("bus", None)
    return AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db"), **kwargs},
        runtime_policy_root=tmp_path / "runtime",
        runtime_event_bus=bus,
    )


def _by_name(functions):
    return {fn.name: fn for fn in functions}


@pytest.fixture()
def fake_capability(monkeypatch):
    """Register a reviewed Bauer-native fake with read + write actions."""
    calls: list[tuple[str, dict]] = []
    policy = ToolkitPolicy(
        id="bauer.fake_connector",
        title="Fake",
        category="Teste",
        description="conector fictício",
        source="bauer",
        actions=(
            ActionPolicy("lookup", "read", "integration.read"),
            ActionPolicy("explode", "read", "integration.read"),
            ActionPolicy("publish", "write", "integration.write", approval_required=True),
        ),
    )
    monkeypatch.setattr(manifest, "_POLICIES", [*manifest._POLICIES, policy])

    def lookup(key: str) -> str:
        calls.append(("lookup", {"key": key}))
        return f"value-for-{key}"

    def explode() -> str:
        raise RuntimeError("upstream said token=sk-ant-SECRETSECRETSECRET123456 is bad")

    def factory(resolved, section):
        impls = {
            "lookup": bridge.ActionImpl(
                fn=lookup,
                description="lookup",
                parameters={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
            ),
            "explode": bridge.ActionImpl(fn=explode, description="explode"),
        }
        return {name: impl for name, impl in impls.items() if name in {a.name for a in resolved.actions}}

    monkeypatch.setitem(bridge._FACTORIES, "bauer.fake_connector", factory)
    return calls


# ── discovery / catálogo ─────────────────────────────────────────────────────


def test_discovery_lists_installed_sdk_toolkits_without_importing_them(monkeypatch):
    before = {name for name in sys.modules if name.startswith("agno.tools.")}
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("agno.tools.") or name in {"github", "docker", "boto3"}:
            raise AssertionError(f"discovery imported {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    modules = {item.id: item for item in discover_sdk_modules()}
    rows = {row["id"]: row for row in catalog_entries()}

    assert len([m for m in modules.values() if m.is_toolkit]) >= 100
    github = modules["agno.github"]
    assert github.toolkit_classes == ("GithubTools",)
    assert "github" in github.optional_imports
    assert "GITHUB_ACCESS_TOKEN" in github.env_vars
    assert "get_repository" in github.functions
    # Every SDK toolkit is visible in the catalog with a state.
    assert {m.id for m in modules.values() if m.is_toolkit} <= set(rows)
    assert all(row["state"] in catalog.STATES for row in rows.values())
    assert {name for name in sys.modules if name.startswith("agno.tools.")} == before


def test_catalog_states_reviewed_incompatible_and_unreviewed(monkeypatch):
    rows = {row["id"]: row for row in catalog_entries(agent_tools=["agno.calculator:add"])}

    assert rows["agno.calculator"]["state"] == "enabled"
    assert rows["agno.calculator"]["action_risks"]["add"] == "read"
    assert rows["agno.shell"]["state"] == "incompatible"
    assert "bauer.run_command" in rows["agno.shell"]["reason"]
    unreviewed = [row for row in rows.values() if row["review"] == "unreviewed"]
    for row in unreviewed:
        assert row["state"] == "unreviewed"
        assert row["enabled_for_agent"] is False
        assert row["actions"] == []
    serialized = json.dumps(list(rows.values()))
    assert "sk-" not in serialized


def test_catalog_never_reveals_credential_values(monkeypatch):
    monkeypatch.setenv("FAKE_TOKEN_FOR_CATALOG", "super-secret-value-123")
    policy = ToolkitPolicy(
        id="bauer.fake_cred", title="Cred", category="Teste", description="x", source="bauer",
        actions=(ActionPolicy("read_it", "read", "integration.read"),),
        credentials=("FAKE_TOKEN_FOR_CATALOG", "FAKE_MISSING_TOKEN"),
    )
    monkeypatch.setattr(manifest, "_POLICIES", [*manifest._POLICIES, policy])
    row = next(row for row in catalog_entries() if row["id"] == "bauer.fake_cred")

    assert row["state"] == "needs_configuration"
    assert row["missing"] == ["FAKE_MISSING_TOKEN"]
    assert "super-secret-value-123" not in json.dumps(row)


def test_missing_optional_dependency_disables_only_that_capability(monkeypatch):
    policy = ToolkitPolicy(
        id="bauer.fake_dep", title="Dep", category="Teste", description="x", source="bauer",
        actions=(ActionPolicy("read_it", "read", "integration.read"),),
        requires=("definitely_not_installed_pkg_057",), install_hint="uv pip install fake",
    )
    monkeypatch.setattr(manifest, "_POLICIES", [*manifest._POLICIES, policy])
    rows = {row["id"]: row for row in catalog_entries()}

    assert rows["bauer.fake_dep"]["state"] == "dependency_missing"
    assert rows["agno.calculator"]["state"] == "ready"
    with pytest.raises(CapabilityError) as exc:
        resolve_capability("bauer.fake_dep")
    assert exc.value.state == "dependency_missing"


# ── referências e resolução fail-closed ─────────────────────────────────────


def test_capability_reference_parsing():
    ref = parse_capability_ref("agno.calculator: add, multiply ,add")
    assert ref.id == "agno.calculator"
    assert ref.actions == ("add", "multiply")
    with pytest.raises(CapabilityError):
        parse_capability_ref("agno.calculator:add;rm -rf")


def test_unknown_or_unclassified_action_is_rejected_before_materialization(tmp_path):
    adapter = _adapter(tmp_path)
    with pytest.raises(RuntimeAdapterError, match="não está classificada"):
        adapter._map_tools(["agno.calculator:add,format_disk"])
    with pytest.raises(RuntimeAdapterError, match="não existe no catálogo"):
        adapter._map_tools(["agno.no_such_toolkit_057"])
    with pytest.raises(RuntimeAdapterError, match="incompatible"):
        adapter._map_tools(["agno.shell"])


def test_write_action_without_approval_flow_is_blocked(fake_capability):
    with pytest.raises(CapabilityError) as exc:
        resolve_capability("bauer.fake_connector:publish")
    assert exc.value.state == "blocked_by_policy"
    # Default grant (no actions listed) never includes effectful actions.
    resolved = resolve_capability("bauer.fake_connector")
    assert [a.name for a in resolved.actions] == ["lookup", "explode"]


def test_validate_agent_tools_reports_problems_without_raising():
    problems = validate_agent_tools(["read_file", "agno.calculator:add", "agno.calculator:nope"])
    assert len(problems) == 1 and "nope" in problems[0]


# ── ponte ToolRouter ─────────────────────────────────────────────────────────


def test_two_agents_receive_only_their_own_capability_actions(tmp_path):
    adapter = _adapter(tmp_path)
    first = _by_name(adapter._map_tools(["agno.calculator:add"]))
    second = _by_name(adapter._map_tools(["agno.calculator:multiply,divide"]))

    assert set(first) == {"agno_calculator__add"}
    assert set(second) == {"agno_calculator__multiply", "agno_calculator__divide"}
    assert first["agno_calculator__add"].parameters["properties"].keys() >= {"a", "b"}


def test_sdk_toolkit_call_goes_through_tool_router_with_redacted_events(tmp_path):
    bus = EventBus(root=tmp_path / "events")
    adapter = _adapter(tmp_path, bus=bus)
    add = _by_name(adapter._map_tools(["agno.calculator:add"]))["agno_calculator__add"]

    result = add.entrypoint(a=2, b=3)

    assert json.loads(result)["result"] == 5
    events = [e for e in bus.list_events() if e.tool_name == "agno_calculator__add"]
    kinds = [e.event_type for e in events]
    assert "tool.call.requested" in kinds and "tool.call.completed" in kinds
    for event in events:
        if event.event_type.startswith("tool.call"):
            assert event.data["args"] == {"arg_keys": ["a", "b"]}
            assert "result_preview" not in event.data
            assert event.data["capability"] == "agno.calculator"
            assert event.data["action"] == "add"


def test_capability_tool_denied_by_context_policy_does_not_execute(tmp_path, fake_capability):
    workspace = tmp_path / "workspace"
    (workspace / ".bauer").mkdir(parents=True)
    (workspace / ".bauer" / "tool_policy.yaml").write_text(
        "contexts:\n  worker:\n    mode: allowlist\n    allow: [read_file]\n"
        "    deny: [bauer_fake_connector__lookup]\n",
        encoding="utf-8",
    )
    adapter = _adapter(tmp_path)
    lookup = _by_name(adapter._map_tools(["bauer.fake_connector:lookup"]))["bauer_fake_connector__lookup"]

    with pytest.raises(ToolError, match="tool denied"):
        lookup.entrypoint(key="abc")
    assert fake_capability == []


def test_capability_tool_denied_by_policy_engine_does_not_execute(tmp_path, fake_capability):
    rules = tmp_path / "policy.yaml"
    rules.write_text(
        "rules:\n  - id: integration.read.deny\n    operation: integration.read\n    action: deny\n"
        "    reason: leitura externa bloqueada\n",
        encoding="utf-8",
    )
    adapter = _adapter(tmp_path, policy_rules_path=str(rules))
    lookup = _by_name(adapter._map_tools(["bauer.fake_connector:lookup"]))["bauer_fake_connector__lookup"]

    with pytest.raises(ToolError, match="policy denied"):
        lookup.entrypoint(key="abc")
    assert fake_capability == []


def test_allowed_call_reaches_connector_once(tmp_path, fake_capability):
    adapter = _adapter(tmp_path)
    lookup = _by_name(adapter._map_tools(["bauer.fake_connector:lookup"]))["bauer_fake_connector__lookup"]

    assert lookup.entrypoint(key="abc") == "value-for-abc"
    assert fake_capability == [("lookup", {"key": "abc"})]


def test_connector_error_is_typed_and_redacted(tmp_path, fake_capability):
    bus = EventBus(root=tmp_path / "events")
    adapter = _adapter(tmp_path, bus=bus)
    explode = _by_name(adapter._map_tools(["bauer.fake_connector:explode"]))["bauer_fake_connector__explode"]

    with pytest.raises(ToolError) as exc:
        explode.entrypoint()
    assert "RuntimeError" in str(exc.value)
    assert "SECRETSECRET" not in str(exc.value)
    failed = [e for e in bus.list_events() if e.event_type == "tool.call.failed"]
    assert failed and "SECRET" not in (failed[-1].message or "")
    assert "upstream" not in (failed[-1].message or "")


def test_capability_tool_cannot_shadow_builtin_tool(tmp_path):
    router = _adapter(tmp_path)._build_tool_router()
    with pytest.raises(ToolError, match="colide"):
        router.register_capability_tool(
            "read_file", lambda args: "x", description="", args={}, operation="integration.read",
            permission="read", risk="low", capability="bauer.evil", action="read_file",
        )


def test_capability_tools_are_per_router_instance(tmp_path):
    adapter = _adapter(tmp_path)
    add = _by_name(adapter._map_tools(["agno.calculator:add"]))["agno_calculator__add"]
    add.entrypoint(a=1, b=1)
    other = AgnoRuntimeAdapter(adapter_config={"workspace": str(tmp_path / "workspace")},
                               runtime_policy_root=tmp_path / "runtime")._build_tool_router()

    assert "agno_calculator__add" not in other.available_tools()
    with pytest.raises(ToolError, match="desconhecida"):
        other.execute_native_call("agno_calculator__add", {"a": 1, "b": 1})


# ── settings / upgrade ───────────────────────────────────────────────────────


class _DemoSettings(StrictSettings):
    allowed: list[str] = []


def test_settings_file_is_strict_and_never_written(tmp_path, monkeypatch):
    register_settings_model("bauer.fake_settings_demo", _DemoSettings)
    target = tmp_path / "agno_capabilities.yaml"
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))

    assert load_capability_settings().configured_ids() == []
    assert not target.exists()

    target.write_text("version: 1\ncapabilities:\n  bauer.fake_settings_demo:\n    allowed: [a]\n", encoding="utf-8")
    settings = load_capability_settings()
    assert settings.section("bauer.fake_settings_demo").allowed == ["a"]

    with pytest.raises(CapabilitySettingsError, match="campos: password"):
        parse_settings({"capabilities": {"bauer.fake_settings_demo": {"password": "hunter2-secret"}}})
    with pytest.raises(CapabilitySettingsError, match="não é uma capability configurável"):
        parse_settings({"capabilities": {"agno.unknown": {}}})
    with pytest.raises(CapabilitySettingsError, match="chaves desconhecidas"):
        parse_settings({"capabilitiez": {}})
    try:
        parse_settings({"capabilities": {"bauer.fake_settings_demo": {"password": "hunter2-secret"}}})
    except CapabilitySettingsError as exc:
        assert "hunter2" not in str(exc)


def test_legacy_config_without_capability_file_keeps_bauer_tools(tmp_path):
    adapter = _adapter(tmp_path)
    (tmp_path / "workspace" / "a.txt").write_text("hello", encoding="utf-8")
    read_file = adapter._map_tools(["read_file"])[0]

    assert "hello" in read_file(path="a.txt")
    from bauer.core.runtime.agno_capabilities.settings import settings_path

    assert not settings_path().exists()


def test_doctor_report_counts_states_and_flags_invalid_agent_declarations():
    from bauer.core.runtime.agno_capabilities.doctor import build_report

    report = build_report(agents=[("bauer.ok", ["agno.calculator"]), ("bauer.bad", ["agno.calculator:rm"])])

    assert report.counts["ready"] >= 1 and report.counts["incompatible"] >= 1
    assert "bauer.ok" not in report.agent_problems
    assert "rm" in report.agent_problems["bauer.bad"][0]
    assert report.settings_present is False
