"""Sprint 61 — fronteiras de execução (T01, T06, T08 e metadados do lote 6).

Herméticos: ``tmp_path``, ShellRunner local com comandos inofensivos e
nenhum provider, bridge ou rede real. O approver por LLM é sempre um mock.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer import tool_effects
from bauer.core.events import EventBus
from bauer.core.policy import ApprovalManager
from bauer.shell_runner import ShellRunner
from bauer.tool_registry import ToolRegistry
from bauer.tool_router import (
    _CAPABILITY_TOOLS,
    _PERMISSION_LEVELS,
    _RISK_LEVELS,
    _TOOL_SECURITY,
    ToolError,
    ToolRouter,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    ToolRegistry.reset()
    yield
    ToolRegistry.reset()


@pytest.fixture
def ws(tmp_path):
    path = tmp_path / "ws"
    path.mkdir()
    return path


def _jobs(ws: Path) -> dict:
    return json.loads((ws / ".bauer_cronjobs.json").read_text(encoding="utf-8"))


def _create(router, name, command, mode):
    router.execute({"action": "cronjob", "args": {
        "action": "create", "name": name, "command": command, "schedule": "every 30m", "mode": mode}})


# ── T01: cronjob run reusa os gates de shell/código ─────────────────────────

def test_t01_cron_shell_outside_allowlist_waits_for_approval_like_run_command(ws, tmp_path):
    runner = ShellRunner(workspace=ws)
    router = ToolRouter(workspace=ws, shell_runner=runner, audit_enabled=False,
                        policy_enabled=True, policy_root=tmp_path / "runtime")
    _create(router, "audit", "printf cron-audit", "shell")
    with patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
        with pytest.raises(ToolError, match="waiting_approval.*shell.execute"):
            router.execute({"action": "cronjob", "args": {"action": "run", "name": "audit"}})
    run.assert_not_called()
    popen.assert_not_called()
    assert _jobs(ws)["audit"]["run_count"] == 0


def test_t01_cron_shell_blocked_by_allowlist_without_policy(ws):
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), audit_enabled=False)
    _create(router, "audit", "printf cron-audit", "shell")
    with patch("subprocess.run") as run:
        with pytest.raises(ToolError):
            router.execute({"action": "cronjob", "args": {"action": "run", "name": "audit"}})
    run.assert_not_called()


def test_t01_cron_shell_refused_when_shell_disabled(ws):
    router = ToolRouter(workspace=ws, audit_enabled=False)  # sem ShellRunner = shell_enabled false
    with pytest.raises(ToolError, match="shell"):
        _create(router, "s", "echo hi", "shell")
    # Job persistido antes (ou por outro processo) também não roda.
    (ws / ".bauer_cronjobs.json").write_text(json.dumps({"s": {
        "command": "echo hi", "mode": "shell", "status": "active", "run_count": 0,
        "schedule": {"type": "interval", "unit": "minutes", "value": 30}, "schedule_str": "every 30m",
    }}), encoding="utf-8")
    with patch("subprocess.run") as run:
        with pytest.raises(ToolError, match="shell"):
            router.execute({"action": "cronjob", "args": {"action": "run", "name": "s"}})
    run.assert_not_called()


@pytest.mark.parametrize("mode,command", [("shell", "echo hi"), ("python", "print('hi')")])
def test_t01_policy_deny_applies_to_persisted_job(ws, tmp_path, mode, command):
    rules = tmp_path / "policy.yaml"
    rules.write_text("rules:\n  - id: shell.deny\n    operation: shell.execute\n    action: deny\n",
                     encoding="utf-8")
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), audit_enabled=False,
                        policy_enabled=True, policy_rules_path=rules, policy_root=tmp_path / "runtime")
    _create(router, "j", command, mode)
    with patch("subprocess.run") as run:
        with pytest.raises(ToolError, match="policy denied"):
            router.execute({"action": "cronjob", "args": {"action": "run", "name": "j"}})
    run.assert_not_called()


def test_t01_context_denial_of_run_command_applies_to_cron(ws):
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), audit_enabled=False,
                        tool_allowlist=["cronjob"])
    _create(router, "j", "echo hi", "shell")
    with pytest.raises(ToolError, match="tool denied: 'run_command'"):
        router.execute({"action": "cronjob", "args": {"action": "run", "name": "j"}})


def test_t01_hardline_guard_applies_to_both_modes(ws):
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), audit_enabled=False)
    _create(router, "sh", "rm -rf /", "shell")
    _create(router, "py", "import os; os.system('rm -rf /')", "python")
    for name in ("sh", "py"):
        with pytest.raises(ToolError, match="BLOCKED|denylist|bloqueado"):
            router.execute({"action": "cronjob", "args": {"action": "run", "name": name}})


def test_t01_approved_cron_job_runs_once_after_approval(ws, tmp_path):
    policy_root = tmp_path / "runtime"
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), audit_enabled=False,
                        policy_enabled=True, policy_root=policy_root, session_id="s1", run_id="r1")
    _create(router, "ok", f'"{sys.executable}" -c "print(4321)"', "shell")
    with pytest.raises(ToolError, match="waiting_approval") as exc:
        router.execute({"action": "cronjob", "args": {"action": "run", "name": "ok"}})
    approval_id = re.search(r"approval_id=(appr-[0-9a-f-]+)", str(exc.value)).group(1)
    ApprovalManager(root=policy_root).approve(approval_id)
    out = router.execute({"action": "cronjob", "args": {"action": "run", "name": "ok", "approval_id": approval_id}})
    assert "4321" in out
    assert _jobs(ws)["ok"]["run_count"] == 1
    with pytest.raises(ToolError):  # aprovação "once" não é reutilizável
        router.execute({"action": "cronjob", "args": {"action": "run", "name": "ok", "approval_id": approval_id}})


def test_t01_allowed_job_still_works_and_counts_once_in_budget(ws):
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), audit_enabled=False)
    _create(router, "py", "print('cron-ok')", "python")
    before = router._tool_call_count
    out = router.execute({"action": "cronjob", "args": {"action": "run", "name": "py"}})
    assert "cron-ok" in out
    assert router._tool_call_count == before + 1
    assert _jobs(ws)["py"]["run_count"] == 1


# ── T06: dry_run por tool/ação ──────────────────────────────────────────────

_MUTATING_CALLS = [
    ("memory", {"action": "set", "key": "x", "value": "1"}),
    ("memory", {"action": "delete", "key": "x"}),
    ("kanban_write", {"action": "create", "title": "dry"}),
    ("skill_manage", {"action": "create", "name": "s", "description": "d", "content": "c"}),
    ("cronjob", {"action": "create", "name": "j", "command": "print(1)", "schedule": "every 1h"}),
    ("cronjob", {"action": "run", "name": "j"}),
    ("cronjob", {"action": "pause", "name": "j"}),
    ("app_factory_init", {"name": "idea"}),
    ("process", {"action": "start", "command": "echo hi"}),
    ("process", {"action": "kill", "pid": "1"}),
    ("verify_app", {"path": "."}),
    ("mcp_call", {"server": "s", "tool": "t", "args": {}}),
    ("mcp_list_tools", {"server": "s"}),
    ("send_message", {"channel": "telegram", "chat_id": "1", "text": "oi"}),
    ("channel_send", {"channel": "c", "text": "oi"}),
    ("social_post", {"content": "oi", "channels": ["ig-1"]}),
    ("lsp_rename", {"path": "a.py", "line": 1, "character": 1, "new_name": "b"}),
    ("mixture_of_agents", {"prompt": "x"}),
    ("write_file", {"path": "a.txt", "content": "x"}),
]


@pytest.mark.parametrize("tool,args", _MUTATING_CALLS, ids=lambda v: str(v)[:40])
def test_t06_dry_run_simulates_every_mutating_action(ws, tool, args):
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), audit_enabled=False,
                        dry_run=True)
    bridge = MagicMock()
    with patch("bauer.live_bridges.get", lambda ch: bridge), \
         patch("subprocess.Popen") as popen, patch("subprocess.run") as run, \
         patch.object(router, "_postiz_client") as postiz:
        out = router.execute({"action": tool, "args": args})
    assert out.startswith("[dry_run]"), out
    assert list(ws.iterdir()) == []
    popen.assert_not_called()
    run.assert_not_called()
    postiz.assert_not_called()
    assert bridge.mock_calls == []


@pytest.mark.parametrize("tool,args", [
    ("memory", {"action": "get", "key": "x"}), ("memory", {"action": "list"}),
    ("cronjob", {"action": "list"}), ("process", {"action": "list"}),
    ("kanban_read", {"mode": "list"}), ("list_dir", {"path": "."}), ("todo", {"action": "list"}),
])
def test_t06_dry_run_keeps_read_actions(ws, tool, args):
    router = ToolRouter(workspace=ws, audit_enabled=False, dry_run=True)
    out = router.execute({"action": tool, "args": args})
    assert not out.startswith("[dry_run]")


def test_t06_external_extension_is_simulated_by_default(ws):
    calls = []
    ToolRegistry.get().register("ext_write", lambda a: calls.append(a) or "wrote",
                                description="x", args={})
    ToolRegistry.get().register("ext_read", lambda a: "read-ok", description="x", args={},
                                side_effects=False)
    router = ToolRouter(workspace=ws, audit_enabled=False, dry_run=True)
    assert router.execute({"action": "ext_write", "args": {}}).startswith("[dry_run]")
    assert calls == []
    assert router.execute({"action": "ext_read", "args": {}}) == "read-ok"


def test_t06_override_of_mutating_builtin_cannot_opt_out_of_dry_run(ws):
    ToolRegistry.get().register("write_file", lambda a: "wrote", description="x", args={},
                                side_effects=False)
    router = ToolRouter(workspace=ws, audit_enabled=False, dry_run=True)
    assert router.execute({"action": "write_file", "args": {"path": "a", "content": "b"}}).startswith("[dry_run]")


def test_t06_every_builtin_has_explicit_effect_classification(ws):
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), web_enabled=True,
                        audit_enabled=False)
    classified = (tool_effects.READ_ONLY_TOOLS | set(tool_effects.READ_ACTIONS)
                  | tool_effects.EFFECT_TOOLS)
    builtins = {name for name in router._tools if name not in _CAPABILITY_TOOLS}
    assert builtins - classified == set()
    assert not tool_effects.READ_ONLY_TOOLS & tool_effects.EFFECT_TOOLS
    assert tool_effects.call_has_side_effects("brand_new_builtin", {}) is True


# ── T08: aprovação declarada no registry efetivo ────────────────────────────

def _register_critical(calls):
    ToolRegistry.get().register(
        "approval_audit", lambda a: calls.append(a) or "ran", description="critical", args={},
        permission="system", risk="critical", requires_approval=True)


def test_t08_extension_approval_denied_blocks_fn(ws):
    calls: list = []
    _register_critical(calls)
    router = ToolRouter(workspace=ws, audit_enabled=False, llm_client=MagicMock())
    with patch("bauer.llm_approval.llm_evaluate_tool",
               return_value=MagicMock(approved=False, reason="no", suggestion="")) as approver:
        out = router.execute({"action": "approval_audit", "args": {}})
    assert approver.call_count == 1
    assert "Negado" in out
    assert calls == []


def test_t08_extension_approval_granted_runs_once(ws):
    calls: list = []
    _register_critical(calls)
    router = ToolRouter(workspace=ws, audit_enabled=False, llm_client=MagicMock())
    with patch("bauer.llm_approval.llm_evaluate_tool",
               return_value=MagicMock(approved=True)) as approver:
        assert router.execute({"action": "approval_audit", "args": {}}) == "ran"
    assert approver.call_count == 1
    assert len(calls) == 1


def test_t08_extension_approval_goes_through_policy_when_enabled(ws, tmp_path):
    calls: list = []
    _register_critical(calls)
    policy_root = tmp_path / "runtime"
    router = ToolRouter(workspace=ws, audit_enabled=False, policy_enabled=True,
                        policy_root=policy_root, session_id="s1", run_id="r1")
    with pytest.raises(ToolError, match="waiting_approval.*tool.approval") as exc:
        router.execute({"action": "approval_audit", "args": {}})
    assert calls == []
    approval_id = re.search(r"approval_id=(appr-[0-9a-f-]+)", str(exc.value)).group(1)
    ApprovalManager(root=policy_root).approve(approval_id)
    assert router.execute({"action": "approval_audit", "args": {"approval_id": approval_id}}) == "ran"


def test_t08_metadata_and_events_reflect_extension(ws):
    calls: list = []
    _register_critical(calls)
    bus = MagicMock(spec=EventBus)
    router = ToolRouter(workspace=ws, audit_enabled=False)
    router._event_bus = bus
    expected = {"permission": "system", "risk": "critical", "approval": True}
    assert router.tool_security("approval_audit") == expected
    info = router.tool_info("approval_audit")
    assert (info["permission_level"], info["risk_level"], info["requires_approval"]) == ("system", "critical", True)
    router.execute({"action": "approval_audit", "args": {}})
    data = [c.kwargs["data"] for c in bus.publish.call_args_list if c.args[0] == "tool.call.requested"]
    assert data and data[0]["permission"] == "system" and data[0]["risk"] == "critical"


def test_t08_override_raises_security_but_never_lowers_it(ws):
    ToolRegistry.get().register("read_file", lambda a: "x", description="x", args={},
                                permission="network", risk="high", requires_approval=True)
    ToolRegistry.get().register("delete_file", lambda a: "x", description="x", args={},
                                permission="read", risk="low", requires_approval=False)
    router = ToolRouter(workspace=ws, audit_enabled=False)
    assert router.tool_security("read_file") == {"permission": "network", "risk": "high", "approval": True}
    assert router.tool_security("delete_file") == {"permission": "write", "risk": "high", "approval": True}
    with patch("bauer.llm_approval.llm_evaluate_tool",
               return_value=MagicMock(approved=False, reason="no", suggestion="")) as approver:
        router._llm_client = MagicMock()
        assert "Negado" in router.execute({"action": "delete_file", "args": {"path": "a"}})
    assert approver.call_count == 1


# ── Lote 6: metadados de segurança ──────────────────────────────────────────

def test_lot6_kanban_metadata_is_write_and_legacy_orphans_removed(ws):
    router = ToolRouter(workspace=ws, audit_enabled=False)
    assert router.tool_security("kanban_write") == {"permission": "write", "risk": "low", "approval": False}
    assert router.tool_info("kanban_write")["permission_level"] == "write"
    assert router.tool_security("kanban_read")["permission"] == "read"
    legacy = {"kanban_create", "kanban_complete", "kanban_block", "kanban_unblock", "kanban_heartbeat",
              "kanban_comment", "kanban_link", "kanban_list", "kanban_show", "video_generate"}
    assert legacy & set(_TOOL_SECURITY) == set()


def test_lot6_security_table_matches_builtin_inventory(ws):
    router = ToolRouter(workspace=ws, shell_runner=ShellRunner(workspace=ws), web_enabled=True,
                        audit_enabled=False)
    builtins = {name for name in router._tools if name not in _CAPABILITY_TOOLS}
    table = {name for name in _TOOL_SECURITY if name not in _CAPABILITY_TOOLS}
    assert builtins - table == set(), "built-in sem metadados de segurança"
    assert table - builtins == set(), "metadados órfãos"
    for name in table:
        assert _TOOL_SECURITY[name]["permission"] in _PERMISSION_LEVELS, name
        assert _TOOL_SECURITY[name]["risk"] in _RISK_LEVELS, name


def test_lot6_kanban_write_event_carries_write_permission(ws):
    bus = MagicMock(spec=EventBus)
    router = ToolRouter(workspace=ws, audit_enabled=False)
    router._event_bus = bus
    router.execute({"action": "kanban_write", "args": {"action": "create", "title": "t"}})
    data = [c.kwargs["data"] for c in bus.publish.call_args_list if c.args[0] == "tool.call.completed"]
    assert data and data[0]["permission"] == "write" and data[0]["risk"] == "low"
