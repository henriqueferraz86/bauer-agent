from __future__ import annotations

import json
from typing import Any, AsyncIterator, Iterator

import pytest

pytest.importorskip("agno")
pytest.importorskip("sqlalchemy")

from agno.models.base import Model
from agno.models.message import Message
from agno.models.response import ModelResponse

from bauer.core.runtime.agent_spec import parse_agents_yaml
from bauer.core.runtime.adapters import get_runtime_adapter, list_runtime_adapters
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter, _ACTIVE_AGNO_SESSION_ID
from bauer.core.runtime.adapters.chatgpt_oauth_model import build_chatgpt_oauth_model
from bauer.tool_router import ToolError


def add_numbers(a: int, b: int) -> str:
    return str(a + b)


class OAuthClientStub:
    def chat_stream(self, model: str, messages: list[dict[str, Any]]) -> Iterator[str]:
        assert model == "gpt-5.6-luna"
        assert messages[-1]["content"] == "hello"
        yield "Olá"
        yield " do OAuth"


class OAuthToolClientStub:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def chat_stream_events(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append({"model": model, "messages": messages, "tools": tools, "tool_choice": tool_choice})
        if any(message.get("role") == "tool" for message in messages):
            yield {"type": "text_delta", "delta": "Tool executada"}
            return
        assert tools and tools[0]["function"]["name"] == "add_numbers"
        yield {
            "type": "tool_call",
            "tool_call": {
                "id": "call_add_numbers_1",
                "type": "function",
                "function": {"name": "add_numbers", "arguments": '{"a": 2, "b": 3}'},
            },
        }


class OAuthReadFileClientStub:
    def __init__(self):
        self.calls: list[list[dict[str, Any]]] = []

    def chat_stream_events(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(messages)
        if any(message.get("role") == "tool" for message in messages):
            tool_result = next(message["content"] for message in messages if message.get("role") == "tool")
            yield {"type": "text_delta", "delta": f"Li: {tool_result}"}
            return
        assert tools and tools[0]["function"]["name"] == "read_file"
        yield {
            "type": "tool_call",
            "tool_call": {
                "id": "call_read_file_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "note.txt"}'},
            },
        }


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


def test_agno_tool_router_can_use_chat_context(tmp_path):
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "chat-context.db"), "tool_context": "chat"},
    )
    router = adapter._build_tool_router()
    assert router.tool_context == "chat"
    assert "web_search" in router.available_tools()


def test_agno_file_tools_share_read_before_write_state_per_agent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "existing.md"
    target.write_text("before", encoding="utf-8")
    adapter = AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
    )

    tools = dict(zip(("read_file", "write_file"), adapter._map_tools(["read_file", "write_file"])))
    assert "before" in tools["read_file"](path="existing.md")
    tools["write_file"](path="existing.md", content="after", overwrite=True)
    assert target.read_text(encoding="utf-8") == "after"


def test_agno_read_before_write_state_is_isolated_per_session(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "existing.md"
    target.write_text("before", encoding="utf-8")
    adapter = AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
    )
    read_file, write_file = adapter._map_tools(["read_file", "write_file"])

    token = _ACTIVE_AGNO_SESSION_ID.set("session-a")
    try:
        assert "before" in read_file(path="existing.md")
    finally:
        _ACTIVE_AGNO_SESSION_ID.reset(token)

    token = _ACTIVE_AGNO_SESSION_ID.set("session-b")
    try:
        with pytest.raises(ToolError, match="nao foi lido nesta sessao"):
            write_file(path="existing.md", content="unauthorized", overwrite=True)
    finally:
        _ACTIVE_AGNO_SESSION_ID.reset(token)

    token = _ACTIVE_AGNO_SESSION_ID.set("session-a")
    try:
        write_file(path="existing.md", content="after", overwrite=True)
    finally:
        _ACTIVE_AGNO_SESSION_ID.reset(token)
    assert target.read_text(encoding="utf-8") == "after"


def test_agno_read_before_write_state_is_not_shared_between_agents(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "existing.md"
    target.write_text("before", encoding="utf-8")
    adapter = AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
    )

    reader = adapter._map_tools(["read_file"])[0]
    writer = adapter._map_tools(["write_file"])[0]
    assert "before" in reader(path="existing.md")
    with pytest.raises(ToolError, match="nao foi lido nesta sessao"):
        writer(path="existing.md", content="after", overwrite=True)
    assert target.read_text(encoding="utf-8") == "before"


def test_chatgpt_oauth_model_delegates_to_bauer_backend():
    model = build_chatgpt_oauth_model(OAuthClientStub(), "gpt-5.6-luna")
    result = model.invoke(
        [Message(role="user", content="hello")],
        Message(role="assistant"),
    )

    assert result.content == "Olá do OAuth"
    assert model.supports_native_tools is True
    assert [chunk.content for chunk in model.invoke_stream(
        [Message(role="user", content="hello")], Message(role="assistant")
    )] == ["Olá", " do OAuth"]


def test_chatgpt_oauth_model_round_trips_tool_calls():
    client = OAuthToolClientStub()
    model = build_chatgpt_oauth_model(client, "gpt-5.6-luna")
    response = model.invoke(
        [Message(role="user", content="some")],
        Message(role="assistant"),
        tools=[{
            "type": "function",
            "function": {
                "name": "add_numbers",
                "description": "adds numbers",
                "parameters": {"type": "object"},
            },
        }],
    )
    assert response.tool_calls[0]["function"]["name"] == "add_numbers"
    assert client.calls[0]["tools"][0]["function"]["name"] == "add_numbers"


def test_agno_oauth_executes_bauer_tool_and_returns_result(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("conteudo seguro", encoding="utf-8")
    client = OAuthReadFileClientStub()
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "agno.db"), "workspace": str(tmp_path)},
        chatgpt_client=client,
    )
    result = adapter.run_agent({
        "session_id": "oauth-tools-session",
        "user_id": "user-1",
        "task": "leia note.txt",
        "agent_spec": {
            "id": "oauth-tools-agent",
            "provider": "openai",
            "model": "gpt-5.6-luna",
            "tools": ["read_file"],
        },
    })
    assert result["status"] == "completed"
    assert "conteudo seguro" in result["output"]
    assert len(client.calls) == 2
    assert any(message.get("role") == "tool" for message in client.calls[1])


def test_agno_adapter_uses_injected_chatgpt_oauth_client(tmp_path):
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "oauth.db")},
        chatgpt_client=OAuthClientStub(),
    )
    result = adapter.run_agent(
        {
            "session_id": "oauth-session",
            "user_id": "user-1",
            "task": "hello",
            "agent_spec": {
                "id": "oauth-agent",
                "provider": "openai",
                "model": "gpt-5.6-luna",
            },
        }
    )

    assert result["status"] == "completed"
    assert result["output"] == "Olá do OAuth"


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


def test_agno_adapter_loads_formal_team_specs_from_package_data(tmp_path, monkeypatch):
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})

    class FakeTeamMode:
        coordinate = "coordinate"

    class FakeTeam:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    monkeypatch.setattr(adapter, "_require_agno_team", lambda: (FakeTeam, FakeTeamMode))
    monkeypatch.setattr(
        adapter,
        "_build_agent",
        lambda spec: type("Member", (), {"id": spec["id"], "model": object()})(),
    )
    monkeypatch.setattr(adapter, "_build_db", lambda: None)

    team = adapter._team_for_request(
        {"decision": {"requested_agents": 3}},
        team_id="bauer.software_team",
        session_id="team-session",
        user_id="user-1",
    )

    assert team.id == "bauer.software_team"
    assert len(team.members) == 3
    assert [member.id for member in team.members] == ["bauer.product", "bauer.dev", "bauer.qa"]
    assert team.delegate_to_all_members is True
    assert team.stream_member_events is True


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
        adapter_config={"db_file": str(tmp_path / "research.db"), "workspace": str(workspace), "tool_context": "chat"}
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


def test_unknown_agno_tool_fails_instead_of_being_silently_dropped(tmp_path):
    from bauer.core.runtime.adapters.base import RuntimeAdapterError

    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(tmp_path)})

    with pytest.raises(RuntimeAdapterError, match="not registered in the Bauer capability catalog"):
        adapter._map_tools(["unknown_toolkit.operation"])


def test_cataloged_but_unimplemented_agno_family_fails_clearly():
    from bauer.core.runtime.adapters.base import RuntimeAdapterError
    from bauer.core.runtime.agno_catalog import AgnoCapabilityCatalog

    adapter = AgnoRuntimeAdapter()
    families = AgnoCapabilityCatalog().list()
    family = next((item for item in families if item["id"].startswith("agno.family.")), None)
    if family is None:
        pytest.skip("Agno SDK extra is not installed")

    with pytest.raises(RuntimeAdapterError, match="cataloged but has no Bauer adapter yet"):
        adapter._map_tools([family["id"]])


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

    assert result["status"] == "failed"
    assert "not authorized" in result["error"]
    assert "blocked" not in result.get("output", "")


def test_agno_internal_router_resumes_same_approval_once(tmp_path):
    """O router lazy do Agno compartilha a aprovação do turno do servidor."""
    from threading import Thread
    from time import sleep

    from bauer.core.events import EventBus
    from bauer.core.kernel import BauerKernel
    from bauer.core.policy import ApprovalManager
    from bauer.core.runtime.run_manager import RunManager
    from bauer.tool_router import reset_runtime_ids, set_runtime_ids

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_root = tmp_path / "runtime"
    bus = EventBus(root=runtime_root)
    runs = RunManager(root=runtime_root, event_bus=bus)
    run = runs.create_run(
        session_id="approval-session", agent_id="serve-user",
        runtime_adapter="agno", status="running",
    )
    adapter = AgnoRuntimeAdapter(
        adapter_config={
            "db_file": str(tmp_path / "agno.db"),
            "workspace": str(workspace),
        },
        runtime_event_bus=bus,
        runtime_policy_root=runtime_root,
        runtime_policy_mode="enforce",
    )
    router = adapter._build_tool_router()
    result: dict[str, str] = {}

    def execute() -> None:
        token = set_runtime_ids("approval-session", run.id)
        try:
            result["value"] = router.execute_native_call(
                "run_command", {"command": "python -c \"print('approved')\""}
            )
        finally:
            reset_runtime_ids(token)

    thread = Thread(target=execute, daemon=True)
    thread.start()
    approvals = ApprovalManager(root=runtime_root, event_bus=bus)
    approval = None
    for _ in range(40):
        records = approvals.list(status="pending")
        if records:
            approval = records[0]
            break
        sleep(0.05)
    assert approval is not None
    assert runs.get_run(run.id).status == "waiting_approval"

    BauerKernel(runs=runs, bus=bus, approvals=approvals).approve(approval.id)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert "approved" in result["value"]
    assert len(approvals.list()) == 1


def test_agno_internal_router_uses_global_shell_configuration(tmp_path):
    from types import SimpleNamespace

    tools = SimpleNamespace(
        safe_mode=False,
        timeout_seconds=47,
        max_output_kb=73,
        extra_allowed_commands=["docker", "docker-compose"],
    )
    adapter = AgnoRuntimeAdapter(
        config=SimpleNamespace(tools=tools),
        adapter_config={
            "db_file": str(tmp_path / "agno.db"),
            "workspace": str(tmp_path),
        },
    )

    router = adapter._build_tool_router()
    runner = router._shell_runner

    assert runner.safe_mode is False
    assert runner.timeout == 47
    assert runner.max_output_bytes == 73 * 1024
    assert runner.extra_allowed_commands == frozenset({"docker", "docker-compose"})
    assert runner.validate("docker ps") == ["docker", "ps"]


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


class OAuthSchemaToolClientStub:
    def __init__(self, tool_name, arguments):
        self.tool_name = tool_name
        self.arguments = arguments
        self.calls = []

    def chat_stream_events(self, model, messages, *, tools=None, tool_choice=None):
        self.calls.append({"messages": messages, "tools": tools})
        results = [message for message in messages if message.get("role") == "tool"]
        if results:
            yield {"type": "text_delta", "delta": str(results[-1]["content"])}
            return
        yield {"type": "tool_call", "tool_call": {
            "id": "extra-tool-call", "type": "function", "function": {
                "name": self.tool_name, "arguments": json.dumps(self.arguments),
            },
        }}


@pytest.mark.parametrize("tool_name,arguments", [
    ("datetime_now", {"format": "br", "tz": "utc"}),
    ("patch", {"path": "note.txt", "old_string": "before", "new_string": "after"}),
    ("process", {"action": "list"}),
])
def test_generic_bauer_tools_keep_schema_and_execute_real_router(tmp_path, monkeypatch, tool_name, arguments):
    from agno.tools.function import Function
    from bauer.tool_router import ToolRouter

    (tmp_path / "note.txt").write_text("before", encoding="utf-8")
    router = ToolRouter(workspace=tmp_path)
    schema = next(item["function"] for item in router.get_tool_schemas()
                  if item["function"]["name"] == tool_name)
    executed = []
    original = router.execute_native_call
    def execute(name, args):
        executed.append((name, args.copy()))
        return original(name, args)
    monkeypatch.setattr(router, "execute_native_call", execute)
    client = OAuthSchemaToolClientStub(tool_name, arguments)
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "schema.db")},
        tool_router=router, chatgpt_client=client,
    )
    mapped = adapter._map_tools([f"bauer.{tool_name}"])[0]
    assert isinstance(mapped, Function)
    assert mapped.name == tool_name
    assert mapped.description == schema["description"]
    assert mapped.parameters == schema["parameters"]
    assert mapped.skip_entrypoint_processing is True
    result = adapter.run_agent({
        "task": "execute authorized tool", "agent_spec": {
            "id": "generic-agent", "provider": "openai", "model": "gpt-5.6-luna",
            "tools": [tool_name],
        },
    })
    assert result["status"] == "completed", result
    assert executed == [(tool_name, arguments)]
    sent = client.calls[0]["tools"][0]["function"]
    assert sent["name"] == tool_name
    assert sent["description"] == schema["description"]
    assert sent["parameters"] == schema["parameters"]
    assert any(message.get("role") == "tool" for message in client.calls[-1]["messages"])
    assert result["metadata"]["tools"][0]["tool_name"] == tool_name
    if tool_name == "patch":
        assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "after"


def test_generic_tool_denied_by_router_is_not_advertised(tmp_path):
    from bauer.core.runtime.adapters.base import RuntimeAdapterError
    from bauer.tool_router import ToolRouter

    router = ToolRouter(workspace=tmp_path, tool_allowlist=["list_dir"])
    adapter = AgnoRuntimeAdapter(tool_router=router)
    with pytest.raises(RuntimeAdapterError, match="authorized"):
        adapter._map_tools(["datetime_now"])
    assert adapter._bauer_tool_callable("datetime_now") is None
    with pytest.raises(RuntimeAdapterError, match="not registered"):
        adapter._map_tools(["unknown_toolkit.operation"])
    assert router._tool_call_count == 0


def test_generic_tool_policy_change_is_enforced_and_kept_in_sdk_log(tmp_path, monkeypatch):
    from bauer.tool_router import ToolRouter

    router = ToolRouter(workspace=tmp_path)
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "denied.db")}, tool_router=router,
    )
    mapped = adapter._map_tools(["datetime_now"])[0]
    monkeypatch.setattr(router, "_is_tool_allowed_in_context", lambda *args, **kwargs: False)
    result = adapter.run_agent({
        "task": "try tool after revocation", "agent_spec": {
            "id": "revoked-agent", "tools": [mapped],
            "agno_model": NamedToolCallingModel(id="revoked-model", tool_name="datetime_now", tool_args={}),
        },
    })
    assert result["status"] == "completed"
    assert "tool denied" in result["output"]
    assert "tool denied" in str(result["metadata"]["tools"])
    assert router._tool_call_count == 0


def test_shell_allowlist_block_stays_in_sdk_tool_result(tmp_path, monkeypatch):
    from bauer.shell_runner import ShellRunner
    from bauer.tool_router import ToolRouter

    def unexpected_process(*args, **kwargs):
        pytest.fail("unapproved command started a process")
    monkeypatch.setattr("subprocess.Popen", unexpected_process)
    router = ToolRouter(workspace=tmp_path, shell_runner=ShellRunner(workspace=tmp_path))
    adapter = AgnoRuntimeAdapter(
        adapter_config={"db_file": str(tmp_path / "shell.db")}, tool_router=router,
    )
    result = adapter.run_agent({
        "task": "try unapproved command", "agent_spec": {
            "id": "shell-agent", "tools": ["run_command"],
            "agno_model": NamedToolCallingModel(id="shell-model", tool_name="run_command", tool_args={"command": "unapproved_bauer_command"}),
        },
    })
    assert result["status"] == "completed"
    assert "Antes de continuar, pergunte ao usuario" in result["output"]
    assert "Antes de continuar, pergunte ao usuario" in str(result["metadata"]["tools"])


@pytest.mark.parametrize("run_fails", [False, True])
def test_tool_event_log_deduplicates_aggregates_and_preserves_partial_failure(tmp_path, monkeypatch, run_fails):
    from agno.models.response import ToolExecution
    from agno.run.agent import RunCompletedEvent, RunErrorEvent, ToolCallCompletedEvent, ToolCallErrorEvent, ToolCallStartedEvent

    first = ToolExecution(tool_call_id="call-1", tool_name="datetime_now", tool_args={}, result="first")
    second = ToolExecution(tool_call_id="call-2", tool_name="datetime_now", tool_args={}, result="blocked", tool_call_error=True)
    class ControlledAgent:
        def run(self, *args, **kwargs):
            yield ToolCallStartedEvent(tool=first)
            yield ToolCallCompletedEvent(tool=first)
            yield ToolCallErrorEvent(tool=second, error="blocked")
            if run_fails:
                yield RunErrorEvent(content="model failure", tools=[first, second])
            else:
                yield RunCompletedEvent(tools=[first, second])
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "events.db")})
    monkeypatch.setattr(adapter, "_runtime_for_request", lambda *args, **kwargs: ControlledAgent())
    result = adapter.run_agent({"task": "controlled tools"})
    assert result["status"] == ("failed" if run_fails else "completed")
    log = result["metadata"]["tools"]
    assert len(log) == 2
    assert [item["tool_call_id"] for item in log] == ["call-1", "call-2"]
    assert log[1]["status"] == "failed" and log[1]["result"] == "blocked"
