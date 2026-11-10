"""Sprint 69 — fronteiras de rede e aprovação restantes (plano 066).

Herméticos: dispatcher web, cliente MCP, client LLM, transporte HTTP e
subprocess são sempre falsos. Nenhum request sai da máquina, nenhum provider
real é construído e o seletor de modelo nunca lê stdin.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from bauer.core.policy import ApprovalManager
from bauer.core.policy.engine import DEFAULT_RULES, PolicyEngine
from bauer.tool_registry import ToolRegistry
from bauer.tool_router import _TOOL_SECURITY, ToolRouter, _policy_operation_for_tool
from bauer.tools.base import ToolError

_REPO = Path(__file__).resolve().parents[1]
_SECRET = "sk-" + "A" * 32


@pytest.fixture(autouse=True)
def _clean_registry():
    ToolRegistry.reset()
    yield
    ToolRegistry.reset()


def _router(tmp_path: Path, rules: str | None = None, **kwargs) -> ToolRouter:
    ws = tmp_path / "ws"
    (ws / ".bauer").mkdir(parents=True, exist_ok=True)
    if rules is not None:
        (ws / ".bauer" / "policy.yaml").write_text(rules, encoding="utf-8")
    return ToolRouter(workspace=ws, audit_enabled=False, policy_enabled=True,
                      policy_root=tmp_path / "runtime", **kwargs)


def _rule(operation: str, action: str) -> str:
    return f"rules:\n  - id: {operation}.{action}.test\n    operation: {operation}\n    action: {action}\n"


def _pending(tmp_path: Path, tool_name: str) -> list:
    return [r for r in ApprovalManager(root=tmp_path / "runtime").list()
            if r.tool_name == tool_name and r.status == "pending"]


# ─── 1a. web_search → network.search ─────────────────────────────────────────


def _web_router(tmp_path, rules=None):
    router = _router(tmp_path, rules, web_enabled=True)
    router._web = MagicMock()
    router._web.search_as_text.return_value = "1. resultado"
    router._web._raw_search_backend = "ddgs"
    return router


def test_web_search_maps_to_network_search():
    assert _policy_operation_for_tool("web_search") == "network.search"


def test_web_search_deny_never_reaches_search_backend(tmp_path):
    router = _web_router(tmp_path, _rule("network.search", "deny"))
    with pytest.raises(ToolError, match="policy denied"):
        router.execute({"action": "web_search", "args": {"query": "bauer agent"}})
    router._web.search_as_text.assert_not_called()


def test_web_search_ask_leaves_pending_approval_and_no_request(tmp_path):
    router = _web_router(tmp_path, _rule("network.search", "ask"))
    with pytest.raises(ToolError):
        router.execute({"action": "web_search", "args": {"query": "bauer agent"}})
    router._web.search_as_text.assert_not_called()
    assert _pending(tmp_path, "web_search")


def test_web_search_allow_runs(tmp_path):
    router = _web_router(tmp_path, _rule("network.search", "allow"))
    assert "resultado" in router.execute({"action": "web_search", "args": {"query": "bauer agent"}})
    router._web.search_as_text.assert_called_once()


def test_web_search_default_rules_deny_secret_in_query(tmp_path):
    router = _web_router(tmp_path)
    with patch("bauer.core.policy.engine.PolicyEngine._load_rules", return_value=list(DEFAULT_RULES)), \
            pytest.raises(ToolError, match="secret"):
        router.execute({"action": "web_search", "args": {"query": f"onde vazou {_SECRET}"}})
    router._web.search_as_text.assert_not_called()


# ─── 1b. mcp_call / mcp_list_tools → mcp.call ───────────────────────────────


def _mcp_router(tmp_path, rules=None):
    router = _router(tmp_path, rules)
    router._mcp_client_for = MagicMock()
    client = router._mcp_client_for.return_value.__enter__.return_value
    client.call_tool.return_value = "ok-mcp"
    client.list_tools.return_value = [{"name": "t", "description": "d"}]
    return router


def test_mcp_tools_map_to_mcp_call():
    assert _policy_operation_for_tool("mcp_call") == "mcp.call"
    assert _policy_operation_for_tool("mcp_list_tools") == "mcp.call"


def test_mcp_call_deny_never_reaches_transport(tmp_path):
    router = _mcp_router(tmp_path, _rule("mcp.call", "deny"))
    with pytest.raises(ToolError, match="policy denied"):
        router.execute({"action": "mcp_call", "args": {"server": "s", "tool": "t", "arguments": {}}})
    with pytest.raises(ToolError, match="policy denied"):
        router.execute({"action": "mcp_list_tools", "args": {"server": "s"}})
    router._mcp_client_for.assert_not_called()


def test_mcp_call_ask_leaves_pending_approval_and_no_request(tmp_path):
    router = _mcp_router(tmp_path, _rule("mcp.call", "ask"))
    with pytest.raises(ToolError):
        router.execute({"action": "mcp_call", "args": {"server": "s", "tool": "t", "arguments": {}}})
    router._mcp_client_for.assert_not_called()
    assert _pending(tmp_path, "mcp_call")


def test_mcp_call_allow_runs(tmp_path):
    router = _mcp_router(tmp_path, _rule("mcp.call", "allow"))
    out = router.execute({"action": "mcp_call", "args": {"server": "s", "tool": "t", "arguments": {}}})
    assert out == "ok-mcp"


def test_mcp_call_default_rules_deny_secret_in_arguments(tmp_path):
    router = _mcp_router(tmp_path)
    with patch("bauer.core.policy.engine.PolicyEngine._load_rules", return_value=list(DEFAULT_RULES)), \
            pytest.raises(ToolError, match="secret"):
        router.execute({"action": "mcp_call", "args": {
            "server": "s", "tool": "t", "arguments": {"token": _SECRET}}})
    router._mcp_client_for.assert_not_called()


def test_mcp_policy_payload_describes_transport(tmp_path, monkeypatch):
    from bauer.tools.mcp import mcp_policy_payload

    router = _router(tmp_path)
    monkeypatch.setenv("MCP_SERVER_REMOTO", "https://mcp.example:8443/rpc")
    payload = mcp_policy_payload(router, "mcp_call", {"server": "remoto", "tool": "x", "arguments": {}})
    assert payload["transport"] == "http"
    assert (payload["host"], payload["remote"]) == ("mcp.example", True)
    assert payload["contains_secret"] is False
    unknown = mcp_policy_payload(router, "mcp_list_tools", {"server": "nao-existe"})
    assert unknown["transport"] == "unconfigured"
    assert unknown["kind"] == "list_tools"


# ─── 1c. delegate_task → agent.delegate ─────────────────────────────────────


def _delegate_router(tmp_path, rules=None):
    llm = MagicMock()
    llm.chat_stream.return_value = iter(["resposta"])
    llm._provider = "ollama"
    router = _router(tmp_path, rules, llm_client=llm, model_name="m")
    return router, llm


def test_delegate_task_maps_to_agent_delegate():
    assert _policy_operation_for_tool("delegate_task") == "agent.delegate"


def test_delegate_task_deny_never_calls_provider_remote_or_subprocess(tmp_path):
    router, llm = _delegate_router(tmp_path, _rule("agent.delegate", "deny"))
    with patch("httpx.post") as post, patch("bauer.tools.execution.run_process") as proc, \
            pytest.raises(ToolError, match="policy denied"):
        router.execute({"action": "delegate_task", "args": {"task": "resuma o README"}})
    llm.chat_stream.assert_not_called()
    post.assert_not_called()
    proc.assert_not_called()


def test_delegate_task_ask_leaves_pending_approval(tmp_path):
    router, llm = _delegate_router(tmp_path, _rule("agent.delegate", "ask"))
    with patch("httpx.post") as post, pytest.raises(ToolError):
        router.execute({"action": "delegate_task", "args": {"task": "resuma o README"}})
    llm.chat_stream.assert_not_called()
    post.assert_not_called()
    assert _pending(tmp_path, "delegate_task")


def test_delegate_task_allow_runs(tmp_path):
    router, llm = _delegate_router(tmp_path, _rule("agent.delegate", "allow"))
    with patch.object(type(router), "_resolve_delegate_agent",
                      return_value=(None, False, "agents.yaml")):
        out = router.execute({"action": "delegate_task", "args": {"task": "resuma o README"}})
    assert "resposta" in out


def test_delegate_policy_payload_marks_remote_dispatch_and_secret(tmp_path):
    router, _ = _delegate_router(tmp_path)
    agent = MagicMock(url="https://agente.example:9000", model="", system="")
    agent.name = "pesquisador"
    with patch.object(type(router), "_resolve_delegate_agent",
                      return_value=(agent, True, "agents.yaml")):
        payload = router._delegate_policy_payload(
            {"task": f"use {_SECRET}", "agent_name": "pesquisador"})
    assert payload["dispatch"] == "remote"
    assert (payload["remote"], payload["host"]) == (True, "agente.example")
    assert payload["contains_secret"] is True
    decision = PolicyEngine(workspace=tmp_path, rules=list(DEFAULT_RULES)).evaluate(
        "agent.delegate", payload)
    assert decision.action == "deny"


def test_delegate_policy_payload_local_provider_dispatch(tmp_path):
    router, _ = _delegate_router(tmp_path)
    with patch.object(type(router), "_resolve_delegate_agent",
                      return_value=(None, False, "agents.yaml")):
        payload = router._delegate_policy_payload({"task": f"use {_SECRET}"})
    assert payload["dispatch"] == "provider"
    assert payload["remote"] is False
    assert (payload["provider"], payload["provider_local"]) == ("ollama", True)
    # O segredo vai para o mesmo provider da sessão: não é exfiltração nova.
    decision = PolicyEngine(workspace=tmp_path, rules=list(DEFAULT_RULES)).evaluate(
        "agent.delegate", payload)
    assert decision.action == "allow"


# ─── 2. lsp_rename → tool.approval ──────────────────────────────────────────


def test_lsp_rename_has_policy_operation():
    assert _policy_operation_for_tool("lsp_rename") == "tool.approval"


def test_every_builtin_requiring_approval_has_policy_operation():
    """Catraca: nenhuma built-in com approval=True fica só com o aprovador LLM."""
    missing = sorted(name for name, sec in _TOOL_SECURITY.items()
                     if sec.get("approval") and _policy_operation_for_tool(name) is None)
    assert missing == []


def test_lsp_rename_waits_for_human_approval_and_runs_once_approved(tmp_path):
    router = _router(tmp_path)
    router._lsp_call = MagicMock(return_value={"changes": {}})
    args = {"file": "a.py", "line": 0, "character": 0, "new_name": "b"}
    with patch("bauer.core.policy.engine.PolicyEngine._load_rules", return_value=list(DEFAULT_RULES)), \
            pytest.raises(ToolError, match="waiting_approval.*tool.approval") as exc:
        router.execute({"action": "lsp_rename", "args": dict(args)})
    router._lsp_call.assert_not_called()
    import re

    approval_id = re.search(r"approval_id=(appr-[0-9a-f-]+)", str(exc.value)).group(1)
    ApprovalManager(root=tmp_path / "runtime").approve(approval_id)
    with patch("bauer.core.policy.engine.PolicyEngine._load_rules", return_value=list(DEFAULT_RULES)):
        router.execute({"action": "lsp_rename", "args": {**args, "approval_id": approval_id}})
    router._lsp_call.assert_called_once()


def test_lsp_rename_deny_rule_blocks(tmp_path):
    router = _router(tmp_path, _rule("tool.approval", "deny"))
    router._lsp_call = MagicMock(return_value={})
    with pytest.raises(ToolError, match="policy denied"):
        router.execute({"action": "lsp_rename", "args": {
            "file": "a.py", "line": 0, "character": 0, "new_name": "b"}})
    router._lsp_call.assert_not_called()


def test_tool_approval_payload_carries_tool_name_for_targeted_rules(tmp_path):
    rules = ("rules:\n  - id: allow-rename\n    operation: tool.approval\n    action: allow\n"
             "    when:\n      tool_name: lsp_rename\n")
    router = _router(tmp_path, rules)
    router._lsp_call = MagicMock(return_value={})
    router.execute({"action": "lsp_rename", "args": {
        "file": "a.py", "line": 0, "character": 0, "new_name": "b"}})
    router._lsp_call.assert_called_once()


# ─── Regras default: engine e config/policy.yaml do repo concordam ──────────


@pytest.mark.parametrize("rules_source", ["default", "repo_config"])
def test_default_rules_cover_new_operations(tmp_path, rules_source):
    if rules_source == "default":
        rules = list(DEFAULT_RULES)
    else:
        rules = yaml.safe_load((_REPO / "config" / "policy.yaml").read_text(encoding="utf-8"))["rules"]
    engine = PolicyEngine(workspace=tmp_path, rules=rules)
    assert engine.evaluate("network.search", {"contains_secret": True}).action == "deny"
    assert engine.evaluate("network.search", {"contains_secret": False}).action == "allow"
    assert engine.evaluate("mcp.call", {"contains_secret": True}).action == "deny"
    assert engine.evaluate("mcp.call", {"contains_secret": False}).action == "allow"
    assert engine.evaluate("agent.delegate", {"remote": True, "contains_secret": True}).action == "deny"
    assert engine.evaluate("agent.delegate", {"remote": False, "contains_secret": True}).action == "allow"
    assert engine.evaluate("tool.approval", {"tool_name": "lsp_rename"}).action == "ask"


# ─── 3. /model em `bauer agent --local` ─────────────────────────────────────


def test_model_switcher_local_only_offers_only_local_providers_and_never_writes_cloud(tmp_path):
    from bauer import model_switcher

    cfg = tmp_path / "config.yaml"
    original = "model:\n  provider: ollama\n  name: qwen\n"
    cfg.write_text(original, encoding="utf-8")
    seen: list = []

    def _pick(items, title):
        seen.append([i for i, _ in items])
        return "anthropic"  # escolha que um seletor sem guarda aceitaria

    with patch.object(model_switcher, "_pick_from_list", side_effect=_pick), \
            patch.object(model_switcher, "_ask_api_key") as ask_key, \
            patch("bauer.auth.AuthManager") as auth:
        model_switcher.run_model_switcher(cfg, local_only=True)
    assert seen and set(seen[0]) <= {"ollama"}
    ask_key.assert_not_called()
    auth.assert_not_called()
    assert cfg.read_text(encoding="utf-8") == original


def _client(provider: str, reply: str) -> MagicMock:
    client = MagicMock()
    client._provider = provider
    client.chat_stream.side_effect = lambda *a, **k: iter([reply])
    return client


def _run_session_with_model_cmd(tmp_path, *, local_only: bool, rebuilt_client):
    from rich.console import Console

    from bauer.agent import run_agent_session

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    local_client = _client("ollama", "resposta local")
    rebuild = MagicMock(return_value=(rebuilt_client, "gpt-cloud", []))
    with patch("bauer.model_switcher.run_model_switcher") as switcher, \
            patch("builtins.input", side_effect=["/model", "oi", EOFError]):
        run_agent_session(local_client, "qwen", 4096, Console(file=MagicMock()),
                          ToolRouter(workspace=ws, audit_enabled=False),
                          rebuild_client_fn=rebuild, local_only=local_only)
    return switcher, local_client


def test_agent_session_local_only_passes_guard_to_switcher(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    switcher, _ = _run_session_with_model_cmd(
        tmp_path, local_only=True, rebuilt_client=_client("ollama", "outro local"))
    assert switcher.call_args.kwargs.get("local_only") is True


def test_agent_session_local_only_refuses_cloud_client_after_model_switch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cloud = _client("openai", "resposta nuvem")
    _, local_client = _run_session_with_model_cmd(tmp_path, local_only=True, rebuilt_client=cloud)
    cloud.chat_stream.assert_not_called()
    assert local_client.chat_stream.call_count >= 1
    pref = tmp_path / "memory" / "model_preference.json"
    assert not pref.exists() or "gpt-cloud" not in pref.read_text(encoding="utf-8")


def test_agent_session_default_mode_still_switches_to_cloud(tmp_path, monkeypatch):
    """Controle positivo: sem --local, /model continua trocando para a nuvem."""
    monkeypatch.chdir(tmp_path)
    cloud = _client("openai", "resposta nuvem")
    _, local_client = _run_session_with_model_cmd(tmp_path, local_only=False, rebuilt_client=cloud)
    assert cloud.chat_stream.call_count >= 1


def test_rebuild_client_for_local_refuses_cloud_config_before_building(tmp_path):
    from bauer.commands import agent_cmd

    cfg = MagicMock()
    cfg.model.provider = "openai"
    cfg.model.name = "gpt-cloud"
    with patch.object(agent_cmd, "_load_or_die", return_value=(cfg, None)), \
            patch("bauer.model_router.validar_execucao_local", return_value=["model.name na nuvem"]), \
            patch.object(agent_cmd, "_build_client") as build, \
            patch.object(agent_cmd, "_build_fallback_clients") as build_fb, \
            pytest.raises(agent_cmd.LocalSwitchRefused, match="--local"):
        agent_cmd._rebuild_client_from_config(Path("config.yaml"), Path("models.yaml"),
                                              local=True, console=None)
    build.assert_not_called()
    build_fb.assert_not_called()


def test_rebuild_client_for_local_uses_local_fallback_chain(tmp_path):
    from bauer.commands import agent_cmd

    cfg = MagicMock()
    cfg.model.provider = "ollama"
    cfg.model.name = "qwen"
    with patch.object(agent_cmd, "_load_or_die", return_value=(cfg, None)), \
            patch("bauer.model_router.validar_execucao_local", return_value=[]), \
            patch.object(agent_cmd, "_build_client", return_value="client") as build, \
            patch.object(agent_cmd, "_build_fallback_clients", return_value=[]) as build_fb:
        out = agent_cmd._rebuild_client_from_config(Path("config.yaml"), Path("models.yaml"),
                                                    local=True, console=None)
    assert out == ("client", "qwen", [])
    build.assert_called_once_with(cfg)
    assert build_fb.call_args.kwargs["conjunto"] == "local"


# ─── 3b. delegate_task numa sessão --local ──────────────────────────────────


def _failing_local_router(tmp_path, monkeypatch, *, client=None):
    from bauer.model_router import LOCAL_ONLY_ENV

    monkeypatch.setenv(LOCAL_ONLY_ENV, "1")
    if client is None:
        client = MagicMock()
        client._provider = "ollama"
        client.chat_stream.side_effect = RuntimeError("ollama caiu")
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return ToolRouter(workspace=ws, audit_enabled=False, llm_client=client, model_name="qwen")


def _completed(stdout="ok-sub"):
    proc = MagicMock()
    proc.returncode, proc.stdout, proc.stderr = 0, stdout, ""
    return proc


def test_local_session_failing_local_client_delegates_only_to_local_verified_subprocess(tmp_path, monkeypatch):
    router = _failing_local_router(tmp_path, monkeypatch)
    with patch.object(type(router), "_resolve_delegate_agent", return_value=(None, False, "agents.yaml")), \
            patch("bauer.tools.execution.run_process", return_value=_completed()) as proc:
        router.execute({"action": "delegate_task", "args": {"task": "resuma"}})
    cmd = proc.call_args.args[0]
    assert cmd[3:5] == ["agent", "run-one"]
    assert "--local" in cmd


def test_local_session_never_uses_cloud_session_client(tmp_path, monkeypatch):
    cloud = MagicMock()
    cloud._provider = "openai"
    cloud.host = "https://api.openai.com"
    router = _failing_local_router(tmp_path, monkeypatch, client=cloud)
    with patch.object(type(router), "_resolve_delegate_agent", return_value=(None, False, "agents.yaml")), \
            patch("bauer.tools.execution.run_process", return_value=_completed()) as proc:
        router.execute({"action": "delegate_task", "args": {"task": "resuma"}})
    cloud.chat_stream.assert_not_called()
    assert "--local" in proc.call_args.args[0]


def test_local_session_refuses_remote_agent_outside_machine(tmp_path, monkeypatch):
    router = _failing_local_router(tmp_path, monkeypatch)
    agent = MagicMock(url="https://agente.example:9000", model="", system="", api_key="")
    agent.name = "pesquisador"
    with patch.object(type(router), "_resolve_delegate_agent", return_value=(agent, True, "agents.yaml")), \
            patch("httpx.post") as post, patch("bauer.tools.execution.run_process") as proc, \
            pytest.raises(ToolError, match="--local recusado"):
        router.execute({"action": "delegate_task", "args": {"task": "resuma", "agent_name": "pesquisador"}})
    post.assert_not_called()
    proc.assert_not_called()


def test_local_session_remote_fallback_stays_local(tmp_path, monkeypatch):
    """Agente em localhost cujo dispatch falha de forma inesperada: o fallback
    (client local que também falha → subprocess) continua preso ao --local."""
    router = _failing_local_router(tmp_path, monkeypatch)
    agent = MagicMock(url="http://127.0.0.1:7770", model="", system="", api_key="")
    agent.name = "vizinho"
    with patch.object(type(router), "_resolve_delegate_agent", return_value=(agent, True, "agents.yaml")), \
            patch("httpx.post", side_effect=ValueError("resposta estranha")), \
            patch("bauer.tools.execution.run_process", return_value=_completed()) as proc:
        router.execute({"action": "delegate_task", "args": {"task": "resuma", "agent_name": "vizinho"}})
    assert "--local" in proc.call_args.args[0]


def test_default_session_subprocess_has_no_local_flag(tmp_path, monkeypatch):
    """Controle positivo: fora do --local, o subprocess não muda."""
    from bauer.model_router import LOCAL_ONLY_ENV

    monkeypatch.delenv(LOCAL_ONLY_ENV, raising=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    router = ToolRouter(workspace=ws, audit_enabled=False)
    with patch.object(type(router), "_resolve_delegate_agent", return_value=(None, False, "agents.yaml")), \
            patch("bauer.tools.execution.run_process", return_value=_completed()) as proc:
        router.execute({"action": "delegate_task", "args": {"task": "resuma"}})
    assert "--local" not in proc.call_args.args[0]


def _cloud_cfg():
    cfg = MagicMock()
    cfg.model.provider = "openai"
    cfg.model.name = "gpt-cloud"
    return cfg


@pytest.mark.parametrize("via", ["flag", "env"])
def test_run_one_local_refuses_cloud_config_before_building_client(monkeypatch, via):
    import typer

    from bauer.commands import agent_cmd
    from bauer.model_router import LOCAL_ONLY_ENV

    if via == "env":
        monkeypatch.setenv(LOCAL_ONLY_ENV, "1")
    else:
        monkeypatch.delenv(LOCAL_ONLY_ENV, raising=False)
    kwargs = {"local": True} if via == "flag" else {}
    with patch.object(agent_cmd, "_load_or_die", return_value=(_cloud_cfg(), None)), \
            patch.object(agent_cmd, "_build_client") as build, \
            pytest.raises(typer.Exit) as exc:
        agent_cmd.agent_run_one(task="oi", config=Path("config.yaml"), models=Path("models.yaml"),
                                agent="", agents_file=Path("agents.yaml"), **kwargs)
    assert exc.value.exit_code == 1
    build.assert_not_called()


def test_run_one_local_with_local_config_marks_nested_delegations(monkeypatch):
    from bauer.commands import agent_cmd
    from bauer.model_router import LOCAL_ONLY_ENV, local_only_active

    monkeypatch.delenv(LOCAL_ONLY_ENV, raising=False)
    cfg = MagicMock()
    cfg.model.provider = "ollama"
    cfg.model.name = "qwen"
    with patch.object(agent_cmd, "_load_or_die", return_value=(cfg, None)), \
            patch.object(agent_cmd, "_build_client", side_effect=RuntimeError("parar aqui")), \
            pytest.raises(RuntimeError, match="parar aqui"):
        agent_cmd.agent_run_one(task="oi", config=Path("config.yaml"), models=Path("models.yaml"),
                                agent="", agents_file=Path("agents.yaml"), local=True)
    assert local_only_active()
