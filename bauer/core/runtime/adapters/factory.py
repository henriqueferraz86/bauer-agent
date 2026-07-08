"""Factory and registry for Bauer runtime adapters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import RuntimeAdapter, RuntimeAdapterError
from .bauer_native import BauerNativeRuntimeAdapter

AdapterFactory = Callable[[], RuntimeAdapter]

_ADAPTERS: dict[str, AdapterFactory] = {
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
        adapter_name = _normalize_name(getattr(runtime, "default_adapter", ""))
    adapter_name = adapter_name or BauerNativeRuntimeAdapter.name

    factory = _ADAPTERS.get(adapter_name)
    if factory is None:
        known = ", ".join(list_runtime_adapters())
        raise RuntimeAdapterError(f"Runtime adapter '{adapter_name}' is not registered. Known adapters: {known}")
    return factory()


def _normalize_name(name: str | None) -> str:
    return (name or "").strip().lower().replace("-", "_")
