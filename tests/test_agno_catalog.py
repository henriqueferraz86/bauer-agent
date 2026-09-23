from __future__ import annotations

import builtins

from bauer.core.runtime.agno_catalog import AgnoCapabilityCatalog
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.adapters.base import RuntimeAdapterError


def test_catalog_lists_registered_bauer_capabilities_and_agent_assignment():
    catalog = AgnoCapabilityCatalog()

    entries = catalog.list(agent_tools=["web_search", "read_file"])
    by_id = {entry["id"]: entry for entry in entries}

    assert by_id["bauer.web_search"]["enabled_for_agent"] is True
    assert by_id["bauer.read_file"]["enabled_for_agent"] is True
    assert by_id["bauer.write_file"]["enabled_for_agent"] is False
    assert by_id["bauer.write_file"]["risk"] == "write"
    assert all("api_key" not in entry and "secret" not in entry for entry in entries)


def test_catalog_discovery_does_not_import_optional_agno_modules(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "agno" or name.startswith("agno."):
            raise AssertionError("catalog discovery must not import Agno modules")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    entries = AgnoCapabilityCatalog().list()

    assert any(entry["id"] == "bauer.read_file" for entry in entries)


def test_cataloged_sdk_family_requires_adapter_before_materialization():
    catalog = AgnoCapabilityCatalog()
    family = next((item for item in catalog.list() if item["module_family"]), None)

    if family is None:
        return
    assert family["state"] == "adapter_required"
    assert AgnoCapabilityCatalog.find(family["id"]) is not None
    assert AgnoCapabilityCatalog.resolve_bauer_tool(family["id"]) is None


def test_missing_agno_package_is_reported_without_hiding_catalog(monkeypatch):
    monkeypatch.setattr(AgnoCapabilityCatalog, "_agno_installed", staticmethod(lambda: False))

    entries = AgnoCapabilityCatalog().list()
    read_file = next(item for item in entries if item["id"] == "bauer.read_file")

    assert read_file["state"] == "disabled"
    assert read_file["installed"] is True
    assert read_file["package"] is None


def test_legacy_tool_names_and_namespaced_ids_resolve_same_capability():
    assert AgnoCapabilityCatalog.resolve_bauer_tool("web_search") == AgnoCapabilityCatalog.find("bauer.web_search")


def test_unknown_tool_name_is_rejected_before_provider_or_sdk_execution():
    adapter = AgnoRuntimeAdapter()

    try:
        adapter._map_tools(["not.registered"])
    except RuntimeAdapterError as exc:
        assert "not registered in the Bauer capability catalog" in str(exc)
    else:
        raise AssertionError("unknown tool was silently ignored")
