"""Testes do ToolRegistry e integração com ToolRouter (TOOL-1).

Cobre:
- ToolDefinition: campos, to_info()
- ToolRegistry: singleton, register(), decorator @registry.tool, get_tool, list_names
- Isolamento: reset() entre testes para não vazar registros
- ToolRouter.execute() roda tool externa
- ToolRouter.available_tools() inclui tools externas
- ToolRouter.tool_info() retorna info de tool externa
- Conflict resolution: tool externa tem prioridade sobre built-in de mesmo nome
- Erros: nome vazio, fn não callable, tool não encontrada
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.tool_registry import ToolDefinition, ToolRegistry, registry as _global_registry
from bauer.tool_router import ToolRouter, ToolError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_registry():
    """Reseta o singleton antes e depois de cada teste para isolamento."""
    ToolRegistry.reset()
    yield
    ToolRegistry.reset()


@pytest.fixture
def reg() -> ToolRegistry:
    return ToolRegistry.get()


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws, audit_enabled=False)


# ---------------------------------------------------------------------------
# ToolDefinition
# ---------------------------------------------------------------------------

class TestToolDefinition:
    def test_to_info_contains_required_keys(self):
        def fn(args: dict) -> str:
            return "ok"

        td = ToolDefinition(
            name="test",
            fn=fn,
            description="Test tool",
            args={"x": "str — param x"},
            permission="read",
            risk="low",
        )
        info = td.to_info()
        assert "description" in info
        assert "args" in info
        assert "permission" in info
        assert "risk" in info
        assert "requires_approval" in info
        assert "tags" in info

    def test_to_info_values(self):
        def fn(args: dict) -> str:
            return "ok"

        td = ToolDefinition(
            name="echo",
            fn=fn,
            description="Ecoa texto",
            args={"text": "str — texto"},
            permission="network",
            risk="medium",
            requires_approval=True,
            tags=["util"],
        )
        info = td.to_info()
        assert info["description"] == "Ecoa texto"
        assert info["permission"] == "network"
        assert info["risk"] == "medium"
        assert info["requires_approval"] is True
        assert "util" in info["tags"]


# ---------------------------------------------------------------------------
# ToolRegistry — singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_returns_same_instance(self):
        r1 = ToolRegistry.get()
        r2 = ToolRegistry.get()
        assert r1 is r2

    def test_reset_creates_new_instance(self):
        r1 = ToolRegistry.get()
        ToolRegistry.reset()
        r2 = ToolRegistry.get()
        assert r1 is not r2

    def test_reset_clears_registrations(self, reg: ToolRegistry):
        reg.register("t", lambda a: "x", description="d", args={})
        ToolRegistry.reset()
        new_reg = ToolRegistry.get()
        assert "t" not in new_reg


# ---------------------------------------------------------------------------
# ToolRegistry — register()
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_basic(self, reg: ToolRegistry):
        def fn(args: dict) -> str:
            return "result"

        td = reg.register("my_tool", fn, description="My tool", args={"a": "str"})
        assert td.name == "my_tool"
        assert "my_tool" in reg

    def test_register_empty_name_raises(self, reg: ToolRegistry):
        with pytest.raises(ValueError, match="nao pode ser vazio"):
            reg.register("", lambda a: "x", description="d", args={})

    def test_register_non_callable_raises(self, reg: ToolRegistry):
        with pytest.raises(ValueError, match="callable"):
            reg.register("t", "not_a_function", description="d", args={})  # type: ignore

    def test_register_twice_overwrites(self, reg: ToolRegistry):
        reg.register("t", lambda a: "v1", description="first", args={})
        reg.register("t", lambda a: "v2", description="second", args={})
        td = reg.get_tool("t")
        assert td.description == "second"

    def test_register_with_all_metadata(self, reg: ToolRegistry):
        td = reg.register(
            "full",
            lambda a: "x",
            description="Full tool",
            args={"x": "str"},
            permission="execute",
            risk="high",
            requires_approval=True,
            tags=["critical", "dangerous"],
        )
        assert td.permission == "execute"
        assert td.risk == "high"
        assert td.requires_approval is True
        assert "critical" in td.tags


# ---------------------------------------------------------------------------
# ToolRegistry — decorator @registry.tool
# ---------------------------------------------------------------------------

class TestDecorator:
    def test_decorator_registers_tool(self, reg: ToolRegistry):
        @reg.tool("echo", description="Ecoa", args={"text": "str"})
        def echo(args: dict) -> str:
            return args.get("text", "")

        assert "echo" in reg

    def test_decorator_fn_still_callable(self, reg: ToolRegistry):
        @reg.tool("my_fn", description="d", args={})
        def my_fn(args: dict) -> str:
            return "direct_call"

        # O decorador retorna a função original — ainda pode ser chamada diretamente
        assert my_fn({"x": 1}) == "direct_call"

    def test_decorator_with_metadata(self, reg: ToolRegistry):
        @reg.tool(
            "net_tool",
            description="Faz requisição",
            args={"url": "str"},
            permission="network",
            risk="medium",
            tags=["web"],
        )
        def net_tool(args: dict) -> str:
            return "response"

        td = reg.get_tool("net_tool")
        assert td.permission == "network"
        assert td.risk == "medium"


# ---------------------------------------------------------------------------
# ToolRegistry — consulta
# ---------------------------------------------------------------------------

class TestQuery:
    def test_get_tool_existing(self, reg: ToolRegistry):
        reg.register("t", lambda a: "x", description="d", args={})
        td = reg.get_tool("t")
        assert td is not None
        assert td.name == "t"

    def test_get_tool_missing_returns_none(self, reg: ToolRegistry):
        assert reg.get_tool("nao_existe") is None

    def test_list_names_sorted(self, reg: ToolRegistry):
        reg.register("z", lambda a: "x", description="d", args={})
        reg.register("a", lambda a: "x", description="d", args={})
        reg.register("m", lambda a: "x", description="d", args={})
        names = reg.list_names()
        assert names == sorted(names)
        assert set(names) == {"a", "m", "z"}

    def test_tool_info_existing(self, reg: ToolRegistry):
        reg.register("t", lambda a: "x", description="Teste", args={"p": "str"})
        info = reg.tool_info("t")
        assert info is not None
        assert info["description"] == "Teste"

    def test_tool_info_missing_returns_none(self, reg: ToolRegistry):
        assert reg.tool_info("nao_existe") is None

    def test_len(self, reg: ToolRegistry):
        assert len(reg) == 0
        reg.register("t1", lambda a: "x", description="d", args={})
        reg.register("t2", lambda a: "x", description="d", args={})
        assert len(reg) == 2

    def test_contains(self, reg: ToolRegistry):
        assert "t" not in reg
        reg.register("t", lambda a: "x", description="d", args={})
        assert "t" in reg

    def test_unregister_existing(self, reg: ToolRegistry):
        reg.register("t", lambda a: "x", description="d", args={})
        assert reg.unregister("t") is True
        assert "t" not in reg

    def test_unregister_nonexistent(self, reg: ToolRegistry):
        assert reg.unregister("nao_existe") is False

    def test_clear(self, reg: ToolRegistry):
        reg.register("t1", lambda a: "x", description="d", args={})
        reg.register("t2", lambda a: "x", description="d", args={})
        reg.clear()
        assert len(reg) == 0

    def test_repr(self, reg: ToolRegistry):
        reg.register("my_tool", lambda a: "x", description="d", args={})
        r = repr(reg)
        assert "my_tool" in r


# ---------------------------------------------------------------------------
# Integração com ToolRouter
# ---------------------------------------------------------------------------

class TestToolRouterIntegration:
    def test_router_executes_external_tool(self, router: ToolRouter, reg: ToolRegistry):
        @reg.tool("ping", description="Ping", args={})
        def ping(args: dict) -> str:
            return "pong"

        result = router.execute({"action": "ping", "args": {}})
        assert result == "pong"

    def test_router_external_tool_receives_args(self, router: ToolRouter, reg: ToolRegistry):
        @reg.tool("adder", description="Soma", args={"a": "int", "b": "int"})
        def adder(args: dict) -> str:
            return str(args.get("a", 0) + args.get("b", 0))

        result = router.execute({"action": "adder", "args": {"a": 3, "b": 4}})
        assert result == "7"

    def test_available_tools_includes_external(self, router: ToolRouter, reg: ToolRegistry):
        @reg.tool("my_external", description="Ext", args={})
        def my_external(args: dict) -> str:
            return "ext"

        tools = router.available_tools()
        assert "my_external" in tools

    def test_available_tools_includes_builtins(self, router: ToolRouter):
        tools = router.available_tools()
        # tools built-in básicas sempre presentes
        assert "read_file" in tools
        assert "list_dir" in tools
        assert "write_file" in tools

    def test_tool_info_external(self, router: ToolRouter, reg: ToolRegistry):
        @reg.tool(
            "info_test",
            description="Tool de teste",
            args={"x": "str — parâmetro x"},
            permission="execute",
            risk="medium",
        )
        def info_test(args: dict) -> str:
            return "ok"

        info = router.tool_info("info_test")
        assert info["description"] == "Tool de teste"
        assert info["permission_level"] == "execute"
        assert info["risk_level"] == "medium"
        assert info.get("source") == "external"

    def test_tool_info_builtin(self, router: ToolRouter):
        info = router.tool_info("read_file")
        assert "description" in info
        assert info.get("source") == "builtin"

    def test_external_overrides_builtin(self, router: ToolRouter, reg: ToolRegistry):
        """Tool externa com mesmo nome de built-in deve ter prioridade."""
        @reg.tool("list_dir", description="Override", args={})
        def list_dir_override(args: dict) -> str:
            return "OVERRIDDEN"

        result = router.execute({"action": "list_dir", "args": {}})
        assert result == "OVERRIDDEN"

    def test_unknown_tool_raises_error(self, router: ToolRouter):
        with pytest.raises(ToolError, match="desconhecida"):
            router.execute({"action": "nao_existe_de_verdade", "args": {}})

    def test_external_tool_exception_propagates(self, router: ToolRouter, reg: ToolRegistry):
        @reg.tool("kaboom", description="Explode", args={})
        def kaboom(args: dict) -> str:
            raise ValueError("boom!")

        with pytest.raises(ValueError, match="boom!"):
            router.execute({"action": "kaboom", "args": {}})
