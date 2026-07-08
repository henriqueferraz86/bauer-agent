from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

import pytest

pytest.importorskip("agno")
pytest.importorskip("sqlalchemy")

from agno.models.base import Model
from agno.models.response import ModelResponse

from bauer.core.runtime.agent_spec import parse_agents_yaml
from bauer.core.runtime.adapters import get_runtime_adapter, list_runtime_adapters
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter


def add_numbers(a: int, b: int) -> str:
    return str(a + b)


class ToolCallingModel(Model):
    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        messages = kwargs.get("messages") or []
        if messages and messages[-1].role == "tool":
            return ModelResponse(role="assistant", content=f"tool result received: {messages[-1].content}")
        return ModelResponse(
            role="assistant",
            tool_calls=[
                {
                    "id": "call_add_numbers_1",
                    "type": "function",
                    "function": {
                        "name": "add_numbers",
                        "arguments": json.dumps({"a": 2, "b": 3}),
                    },
                }
            ],
        )

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


class NamedToolCallingModel(Model):
    def __init__(self, *args: Any, tool_name: str, tool_args: dict[str, Any], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.tool_name = tool_name
        self.tool_args = tool_args

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        messages = kwargs.get("messages") or []
        if messages and messages[-1].role == "tool":
            return ModelResponse(role="assistant", content=f"tool result received: {messages[-1].content}")
        return ModelResponse(
            role="assistant",
            tool_calls=[
                {
                    "id": f"call_{self.tool_name}_1",
                    "type": "function",
                    "function": {
                        "name": self.tool_name,
                        "arguments": json.dumps(self.tool_args),
                    },
                }
            ],
        )

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


def test_agno_adapter_is_registered():
    assert "agno" in list_runtime_adapters()
    assert get_runtime_adapter("agno").name == "agno"


def test_agno_adapter_runs_simple_agent(tmp_path):
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})

    result = adapter.run_agent(
        {
            "session_id": "session-1",
            "user_id": "user-1",
            "task": "hello from bauer",
        }
    )

    assert result["status"] == "completed"
    assert result["runtime_adapter"] == "agno"
    assert "user:hello from bauer" in result["output"]
    assert adapter.get_run(result["run_id"])["status"] == "completed"
    assert adapter.list_sessions()


def test_agno_adapter_streams_chunks(tmp_path):
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})

    events = list(
        adapter.stream_agent(
            {
                "session_id": "session-1",
                "user_id": "user-1",
                "task": "stream from bauer",
            }
        )
    )

    assert events[0]["event"] == "run.started"
    assert events[-1]["event"] == "run.completed"
    assert any(event["event"] == "message.delta" for event in events)


def test_agno_adapter_maps_callable_tools(tmp_path):
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})

    result = adapter.run_agent(
        {
            "session_id": "session-tools",
            "user_id": "user-1",
            "task": "use tool",
            "agent_spec": {
                "id": "tool-agent",
                "agno_model": ToolCallingModel(id="tool-calling-model"),
                "tools": [add_numbers],
            },
        }
    )

    assert result["status"] == "completed"
    assert result["output"] == "tool result received: 5"


def test_agents_yaml_specs_run_via_agno_with_bauer_tools(tmp_path):
    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(
        """
agents:
  - name: code-agent
    description: Reads project files.
    system: You are a code agent.
    model: offline-echo
    provider: local
    tools: [read_file, list_dir, write_file, run_command, memory]
  - name: research-agent
    description: Searches and remembers findings.
    system: You are a research agent.
    model: offline-echo
    provider: local
    tools: [web_search, memory, read_file]
""",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello from code agent", encoding="utf-8")
    specs = {spec.id: spec for spec in parse_agents_yaml(agents_file)}

    code_adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "code.db"), "workspace": str(workspace)}
    )
    code_result = code_adapter.run_agent(
        {
            "session_id": "code-session",
            "user_id": "user-1",
            "task": "read the file",
            "agent_spec": {
                **specs["code-agent"].to_dict(),
                "agno_model": NamedToolCallingModel(
                    id="read-file-model",
                    tool_name="read_file",
                    tool_args={"path": "README.md"},
                ),
            },
        }
    )

    research_adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "research.db"), "workspace": str(workspace)}
    )
    research_result = research_adapter.run_agent(
        {
            "session_id": "research-session",
            "user_id": "user-1",
            "task": "remember finding",
            "agent_spec": {
                **specs["research-agent"].to_dict(),
                "agno_model": NamedToolCallingModel(
                    id="memory-model",
                    tool_name="memory",
                    tool_args={"action": "set", "key": "finding", "value": "agno ok"},
                ),
            },
        }
    )

    assert code_result["status"] == "completed"
    assert "hello from code agent" in code_result["output"]
    assert research_result["status"] == "completed"
    assert "finding" in research_result["output"]


def test_agno_bauer_tool_fallback_respects_tool_policy(tmp_path):
    workspace = tmp_path / "workspace"
    policy_file = workspace / ".bauer" / "tool_policy.yaml"
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text(
        """
contexts:
  worker:
    mode: allowlist
    allow: [list_dir]
""",
        encoding="utf-8",
    )
    (workspace / "secret.txt").write_text("blocked", encoding="utf-8")
    adapter = AgnoRuntimeAdapter(
        adapter_config={
            "db_file": str(tmp_path / "policy.db"),
            "workspace": str(workspace),
            "tool_policy_path": str(policy_file),
            "tool_context": "worker",
        }
    )

    result = adapter.run_agent(
        {
            "session_id": "policy-session",
            "user_id": "user-1",
            "task": "try read_file",
            "agent_spec": {
                "id": "code-agent",
                "name": "code-agent",
                "tools": ["read_file"],
                "agno_model": NamedToolCallingModel(
                    id="policy-model",
                    tool_name="read_file",
                    tool_args={"path": "secret.txt"},
                ),
            },
        }
    )

    assert result["status"] == "completed"
    assert "tool denied" in result["output"]


def test_agno_adapter_returns_clean_error_when_agno_fails(monkeypatch, tmp_path):
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})

    def _raise_runtime_error(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("agno runtime unavailable")

    monkeypatch.setattr(adapter, "_agent_for_request", _raise_runtime_error)

    result = adapter.run_agent(
        {
            "session_id": "session-fail",
            "user_id": "user-1",
            "task": "hello",
        }
    )

    assert result["status"] == "failed"
    assert result["event"] == "run.failed"
    assert result["runtime_adapter"] == "agno"
    assert result["error"] == "agno runtime unavailable"
