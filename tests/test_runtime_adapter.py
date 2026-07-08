from __future__ import annotations

import pytest

from bauer.core.runtime.adapters import RuntimeAdapterError
from bauer.core.runtime.adapters.bauer_native import BauerNativeRuntimeAdapter
from bauer.core.runtime.adapters.factory import (
    get_runtime_adapter,
    list_runtime_adapters,
    register_runtime_adapter,
)


class _FakeClient:
    def chat_stream(self, model, messages):
        assert model == "fake-model"
        assert messages == [{"role": "user", "content": "hello"}]
        return iter(["hel", "lo"])


def test_factory_returns_bauer_native_by_default():
    adapter = get_runtime_adapter()

    assert adapter.name == "bauer_native"
    assert "bauer_native" in list_runtime_adapters()


def test_factory_uses_registered_adapter():
    class _CustomAdapter(BauerNativeRuntimeAdapter):
        name = "custom_runtime"

    register_runtime_adapter("custom-runtime", _CustomAdapter)

    adapter = get_runtime_adapter("custom_runtime")

    assert isinstance(adapter, _CustomAdapter)
    assert adapter.name == "custom_runtime"


def test_bauer_native_create_agent_returns_normalized_spec():
    adapter = BauerNativeRuntimeAdapter()

    result = adapter.create_agent({"name": "dev-agent", "model": "fake-model"})

    assert result["status"] == "created"
    assert result["runtime_adapter"] == "bauer_native"
    assert result["agent_id"] == "dev-agent"
    assert result["spec"]["model"] == "fake-model"


def test_bauer_native_run_agent_streams_to_completion():
    adapter = BauerNativeRuntimeAdapter()

    result = adapter.run_agent(
        {
            "client": _FakeClient(),
            "model": "fake-model",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )

    assert result["status"] == "completed"
    assert result["runtime_adapter"] == "bauer_native"
    assert result["output"] == "hello"
    assert result["run_id"].startswith("run-")


def test_bauer_native_missing_client_fails_clearly():
    adapter = BauerNativeRuntimeAdapter()

    with pytest.raises(RuntimeAdapterError, match="chat_stream"):
        list(adapter.stream_agent({"model": "fake-model", "task": "hello"}))


def test_unknown_runtime_adapter_fails_clearly():
    with pytest.raises(RuntimeAdapterError, match="not registered"):
        get_runtime_adapter("missing")
