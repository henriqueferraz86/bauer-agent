"""Factory and registry for Bauer runtime adapters."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from .base import RuntimeAdapter, RuntimeAdapterError
from .agno_adapter import AgnoRuntimeAdapter
from .bauer_native import BauerNativeRuntimeAdapter

AdapterFactory = Callable[..., RuntimeAdapter]

_ADAPTERS: dict[str, AdapterFactory] = {
    AgnoRuntimeAdapter.name: AgnoRuntimeAdapter.from_config,
    BauerNativeRuntimeAdapter.name: BauerNativeRuntimeAdapter,
}


def register_runtime_adapter(name: str, factory: AdapterFactory) -> None:
    normalized = _normalize_name(name)
    if not normalized:
        raise RuntimeAdapterError("Runtime adapter name cannot be empty.")
    _ADAPTERS[normalized] = factory


def list_runtime_adapters() -> list[str]:
    return sorted(_ADAPTERS)


def get_runtime_adapter(name: str | None = None, config: Any | None = None) -> RuntimeAdapter:
    adapter_name = _normalize_name(name)
    if not adapter_name and config is not None:
        runtime = getattr(config, "runtime", None)
        # Small test doubles and third-party callers from before RuntimeSection
        # existed have no runtime section; keep their native compatibility
        # path. A real Bauer config has RuntimeSection(default_adapter="agno").
        if runtime is not None:
            adapter_name = _normalize_name(getattr(runtime, "default_adapter", ""))
        else:
            adapter_name = BauerNativeRuntimeAdapter.name
    adapter_name = adapter_name or AgnoRuntimeAdapter.name

    factory = _ADAPTERS.get(adapter_name)
    if factory is None:
        known = ", ".join(list_runtime_adapters())
        raise RuntimeAdapterError(f"Runtime adapter '{adapter_name}' is not registered. Known adapters: {known}")
    signature = inspect.signature(factory)
    accepts_config = "config" in signature.parameters or any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )
    if accepts_config:
        return factory(config=config)
    return factory()


def _normalize_name(name: str | None) -> str:
    return (name or "").strip().lower().replace("-", "_")
