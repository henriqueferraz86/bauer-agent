"""Contratos funcionais do catálogo efetivo de tools (auditoria T09/T10)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bauer.tool_registry import ToolRegistry
from bauer.tool_router import ToolRouter


@pytest.fixture(autouse=True)
def isolated_registry():
    ToolRegistry.reset()
    yield
    ToolRegistry.reset()


@pytest.fixture
def router(tmp_path):
    return ToolRouter(workspace=tmp_path, audit_enabled=False)


def _schemas(router):
    schemas = router.get_tool_schemas()
    names = [schema["function"]["name"] for schema in schemas]
    assert len(names) == len(set(names))
    return {schema["function"]["name"]: schema["function"] for schema in schemas}


def test_extension_is_discoverable_and_executable(router):
    ToolRegistry.get().register(
        "catalog_echo", lambda args: args["text"],
        description="Echo from the effective registry", args={"text": "str — texto (obrigatorio)"},
    )
    schemas = _schemas(router)
    assert set(schemas) == set(router.available_tools())
    assert schemas["catalog_echo"]["description"] == "Echo from the effective registry"
    assert schemas["catalog_echo"]["parameters"]["required"] == ["text"]
    assert router.execute_native_call("catalog_echo", {"text": "hello"}) == "hello"


def test_override_schema_matches_effective_handler(router):
    ToolRegistry.get().register(
        "read_file", lambda args: f"replacement:{args['entry']}",
        description="Replacement contract", args={"entry": "int — entrada (obrigatorio)"},
    )
    schema = _schemas(router)["read_file"]
    assert schema["description"] == "Replacement contract"
    assert set(schema["parameters"]["properties"]) == {"entry"}
    assert schema["parameters"]["properties"]["entry"]["type"] == "integer"
    assert router.execute_native_call("read_file", {"entry": 42}) == "replacement:42"


def test_extension_schema_respects_existing_availability_filter(tmp_path, monkeypatch):
    registry = ToolRegistry.get()
    for name in ("visible_extension", "hidden_extension"):
        registry.register(name, lambda args: "ok", description="extension", args={})
    router = ToolRouter(
        workspace=tmp_path, audit_enabled=False,
        tool_allowlist=["visible_extension", "hidden_extension", "calculate"],
    )
    monkeypatch.setattr(
        type(router._tool_policy), "allows", lambda self, context, name: name != "hidden_extension",
    )
    assert set(_schemas(router)) == set(router.available_tools()) == {"visible_extension", "calculate"}


@pytest.mark.parametrize("tool,field,item_type", [
    ("social_post", "channels", "string"),
    ("social_post", "media_paths", "string"),
    ("social_post", "media_urls", "string"),
    ("skill_manage", "tags", "string"),
    ("sqlite_query", "params", None),
])
def test_array_types_survive_provider_sanitization(router, tool, field, item_type):
    property_schema = _schemas(router)[tool]["parameters"]["properties"][field]
    assert property_schema["type"] == "array"
    assert property_schema["items"] == ({"type": item_type} if item_type else {})


def test_social_array_contract_executes_with_fake_client(router, monkeypatch):
    client = MagicMock()
    client.list_integrations.return_value = []
    monkeypatch.setattr(router, "_postiz_client", lambda: client)
    args = {"content": "functional draft", "channels": ["channel-a", "channel-b"], "post_type": "draft"}
    schema = _schemas(router)["social_post"]["parameters"]
    assert set(schema["required"]) <= args.keys()
    assert "rascunho" in router.execute_native_call("social_post", args)
    assert client.create_post.call_args.args[:2] == ("functional draft", ["channel-a", "channel-b"])


def test_sqlite_array_accepts_heterogeneous_parameters(router):
    import sqlite3

    with sqlite3.connect(router.workspace / "sample.db") as connection:
        connection.execute("CREATE TABLE sample (name TEXT, value INTEGER)")
        connection.execute("INSERT INTO sample VALUES ('example', 42)")
    args = {"path": "sample.db", "sql": "SELECT name FROM sample WHERE name = ? AND value = ?", "params": ["example", 42]}
    assert _schemas(router)["sqlite_query"]["parameters"]["properties"]["params"]["items"] == {}
    assert "example" in router.execute_native_call("sqlite_query", args)


_MINIMAL_ACTIONS = [
    ("memory", {"action": "set", "key": "name", "value": "new"}),
    ("memory", {"action": "get", "key": "name"}),
    ("memory", {"action": "list"}),
    ("memory", {"action": "delete", "key": "name"}),
    ("todo", {"action": "add", "text": "new task"}),
    ("todo", {"action": "list"}),
    ("todo", {"action": "done", "id": 1}),
    ("todo", {"action": "remove", "id": 1}),
    ("todo", {"action": "clear"}),
    ("cronjob", {"action": "create", "name": "new", "command": "print(1)", "schedule": "every 1h"}),
    ("cronjob", {"action": "list"}),
    ("cronjob", {"action": "run", "name": "existing"}),
    ("cronjob", {"action": "delete", "name": "existing"}),
    ("cronjob", {"action": "pause", "name": "existing"}),
    ("cronjob", {"action": "resume", "name": "existing"}),
    ("process", {"action": "start", "command": "python --version"}),
    ("process", {"action": "list"}),
    ("process", {"action": "poll", "pid": "123"}),
    ("process", {"action": "log", "pid": "123"}),
    ("process", {"action": "kill", "pid": "123"}),
    ("process", {"action": "write", "pid": "123", "input": "hello"}),
    ("skill_manage", {"action": "create", "name": "new", "description": "new skill", "content": "steps", "tags": ["one", "two"]}),
    ("skill_manage", {"action": "update", "name": "existing", "description": "updated skill", "content": "new steps"}),
    ("skill_manage", {"action": "delete", "name": "existing"}),
    ("session_search", {"action": "search", "query": "example"}),
    ("session_search", {"action": "recent"}),
    ("kanban_read", {"mode": "list"}),
    ("kanban_read", {"mode": "show", "task_id": "T0001"}),
    ("kanban_write", {"action": "create", "title": "new task"}),
    ("kanban_write", {"action": "complete", "task_id": "T0001"}),
    ("kanban_write", {"action": "block", "task_id": "T0001", "reason": "waiting"}),
    ("kanban_write", {"action": "unblock", "task_id": "T0001"}),
    ("kanban_write", {"action": "heartbeat", "task_id": "T0001", "progress": "working"}),
    ("kanban_write", {"action": "comment", "task_id": "T0001", "comment": "update"}),
    ("kanban_write", {"action": "link", "parent_id": "T0001", "child_id": "T0002"}),
]


@pytest.mark.parametrize("tool,args", _MINIMAL_ACTIONS, ids=lambda value: str(value))
def test_minimal_action_is_valid_in_schema_and_handler(router, monkeypatch, tool, args):
    if tool == "memory":
        router._memory({"action": "set", "key": "name", "value": "existing"})
    elif tool == "todo":
        router._todo({"action": "add", "text": "existing"})
    elif tool == "cronjob":
        router._cronjob({"action": "create", "name": "existing", "command": "print(1)", "schedule": "every 1h"})
        monkeypatch.setattr(router, "_execute_code", lambda args: "fake successful execution")
    elif tool == "process":
        process = MagicMock(pid=123, stdout=(), stderr=())
        process.poll.return_value = None
        router._processes["123"] = {"proc": process, "label": "fake", "stdout_buf": [], "stderr_buf": []}
        runner = MagicMock()
        runner.validate.return_value = ["python", "--version"]
        monkeypatch.setattr(router, "_shell_runner", runner)
        monkeypatch.setattr("subprocess.Popen", lambda *args, **kwargs: process)
    elif tool == "skill_manage":
        router._skill_manage({"action": "create", "name": "existing", "description": "skill", "content": "steps"})
    elif tool.startswith("kanban_"):
        router._kanban_create({"title": "parent"})
        router._kanban_create({"title": "child"})

    schema = _schemas(router)[tool]["parameters"]
    assert set(schema["required"]) <= args.keys(), f"{tool} requires fields for other actions"
    result = router.execute_native_call(tool, args)
    assert isinstance(result, str) and result


def test_unconditional_requirements_remain_global(router):
    schemas = _schemas(router)
    assert schemas["read_file"]["parameters"]["required"] == ["path"]
    assert schemas["write_file"]["parameters"]["required"] == ["path", "content"]
    assert schemas["text_to_speech"]["parameters"]["required"] == ["text", "output_file"]
    assert schemas["social_post"]["parameters"]["required"] == ["content", "channels"]
    assert schemas["skill_manage"]["parameters"]["required"] == ["action", "name"]
