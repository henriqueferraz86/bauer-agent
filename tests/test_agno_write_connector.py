"""Plano 057 / sprint 57 — primeiro conector com escrita (criar issue) sob aprovação.

Herméticos: GitHub via ``httpx.MockTransport``; Kernel, RunManager,
ApprovalManager e EventBus reais em ``tmp_path``; o "humano" é uma thread que
aprova/nega pedidos pendentes.
"""

from __future__ import annotations

import json
import textwrap
import threading
import time
from pathlib import Path

import httpx
import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel
from bauer.core.kernel.entry import run_governed
from bauer.core.policy.approvals import ApprovalManager
from bauer.core.policy.engine import PolicyEngine
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability
from bauer.core.runtime.agno_capabilities.families import github as gh
from bauer.core.runtime.run_manager import RunManager
from bauer.runtime_ids import reset_runtime_ids, set_runtime_ids
from bauer.tools.base import ToolError

pytest.importorskip("agno")

_ISSUE = {"repo": "acme/app", "title": "Bug no login", "body": "Passos para reproduzir"}


class _GithubWrites:
    def __init__(self):
        self.posts: list[dict] = []
        self.searches: list[str] = []
        self.fail_with: int | None = None
        self.remote_marker_hit = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/search/issues":
            self.searches.append(request.url.params["q"])
            items = [{"number": 41, "html_url": "https://github.com/acme/app/issues/41"}] if self.remote_marker_hit else []
            return httpx.Response(200, json={"items": items})
        if request.method == "POST" and request.url.path == "/repos/acme/app/issues":
            if self.fail_with:
                return httpx.Response(self.fail_with, json={"message": "boom"})
            body = json.loads(request.content)
            self.posts.append(body)
            number = 100 + len(self.posts)
            return httpx.Response(201, json={"number": number, "html_url": f"https://github.com/acme/app/issues/{number}"})
        return httpx.Response(404)


@pytest.fixture()
def kit(tmp_path, monkeypatch):
    api = _GithubWrites()
    monkeypatch.setattr(gh, "_TRANSPORT", httpx.MockTransport(api))
    monkeypatch.setenv("BAUER_TEST_GH_WRITE", "ghp_writetokenvalue1234567890abcdefgh")
    target = tmp_path / "agno_capabilities.yaml"
    target.write_text(textwrap.dedent("""
        capabilities:
          bauer.github_issues:
            repositories: [acme/app]
            credential: BAUER_TEST_GH_WRITE
            allowed_labels: [bug]
    """), encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))
    root = tmp_path / "runtime"
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    approvals = ApprovalManager(root=root, event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, approvals=approvals, heartbeat_interval_s=0)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return {"api": api, "root": root, "bus": bus, "runs": runs, "approvals": approvals,
            "kernel": kernel, "workspace": workspace, "tmp": tmp_path}


def _create_issue(kit, mode: str = "enforce", policy_rules_path: str | None = None):
    config = {"workspace": str(kit["workspace"]), "db_file": str(kit["tmp"] / "agno.db")}
    if policy_rules_path:
        config["policy_rules_path"] = policy_rules_path
    adapter = AgnoRuntimeAdapter(adapter_config=config, runtime_event_bus=kit["bus"],
                                 runtime_policy_root=kit["root"], runtime_policy_mode=mode)
    return {fn.name: fn for fn in adapter._map_tools(["bauer.github_issues:create_issue"])}["bauer_github_issues__create_issue"]


def _human(kit, decision: str, *, times: int = 1, seen: list | None = None):
    def worker():
        done = 0
        deadline = time.monotonic() + 15
        while done < times and time.monotonic() < deadline:
            for record in kit["approvals"].list("pending"):
                if seen is not None:
                    seen.append(record)
                if decision == "approve":
                    kit["kernel"].approve(record.id)
                else:
                    kit["kernel"].deny(record.id)
                done += 1
            time.sleep(0.02)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _in_run(kit, calls):
    def executor(payload):
        run = kit["runs"].get_run(payload["run_id"])
        token = set_runtime_ids(run.session_id, run.id)
        try:
            return {"output": [call() for call in calls]}
        finally:
            reset_runtime_ids(token)

    return run_governed(kit["kernel"], executor, agent_id="bauer.dev")


def test_capability_is_separate_and_starts_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(tmp_path / "none.yaml"))
    row = next(r for r in catalog_entries() if r["id"] == "bauer.github_issues")
    assert row["state"] == "needs_configuration" and row["risk"] == "write"
    with pytest.raises(CapabilityError):
        resolve_capability("bauer.github:create_issue")
    assert row["default_actions"] == []  # never granted implicitly


def test_approved_call_creates_issue_once_with_visible_summary(kit):
    create = _create_issue(kit)
    seen: list = []
    human = _human(kit, "approve", seen=seen)
    result = _in_run(kit, [lambda: create.entrypoint(**_ISSUE, labels=["bug"])])
    human.join(5)

    assert result.status == "completed", result.error
    created = json.loads(result.output[0])
    assert created["number"] == 101 and created["repo"] == "acme/app"
    assert len(kit["api"].posts) == 1
    assert "bauer-idempotency" in kit["api"].posts[0]["body"]
    record = seen[0]
    assert record.operation == "integration.write"
    assert record.reason.startswith('Criar issue em acme/app: "Bug no login"')
    assert "BAUER_TEST_GH_WRITE" in record.reason
    assert "ghp_writetoken" not in json.dumps([e.data for e in kit["bus"].list_events()] + [e.message for e in kit["bus"].list_events()])


def test_retry_of_same_approved_call_does_not_duplicate(kit):
    create = _create_issue(kit)
    human = _human(kit, "approve", times=2)
    result = _in_run(kit, [lambda: create.entrypoint(**_ISSUE), lambda: create.entrypoint(**_ISSUE)])
    human.join(5)

    first, second = (json.loads(item) for item in result.output)
    assert first["number"] == second["number"]
    assert second["idempotent_replay"] is True
    assert len(kit["api"].posts) == 1


def test_remote_marker_prevents_duplicate_after_lost_ledger(kit):
    kit["api"].remote_marker_hit = True
    create = _create_issue(kit)
    human = _human(kit, "approve")
    result = _in_run(kit, [lambda: create.entrypoint(**_ISSUE)])
    human.join(5)
    assert json.loads(result.output[0])["number"] == 41
    assert kit["api"].posts == []


def test_denied_approval_never_writes(kit):
    create = _create_issue(kit)
    human = _human(kit, "deny")
    result = _in_run(kit, [lambda: create.entrypoint(**_ISSUE)])
    human.join(5)
    assert result.status != "completed"
    assert kit["api"].posts == []


def test_audit_mode_does_not_auto_approve_external_write(kit):
    create = _create_issue(kit, mode="audit")
    with pytest.raises(ToolError, match="waiting_approval"):
        create.entrypoint(**_ISSUE)
    assert kit["api"].posts == []


def test_policy_off_refuses_external_write(kit):
    create = _create_issue(kit, mode="off")
    with pytest.raises(ToolError, match="policy habilitada"):
        create.entrypoint(**_ISSUE)
    assert kit["api"].posts == []


def test_custom_policy_cannot_lower_write_below_ask(kit, tmp_path):
    rules = tmp_path / "policy.yaml"
    rules.write_text("rules:\n  - id: all.allow\n    operation: integration.write\n    action: allow\n", encoding="utf-8")
    assert PolicyEngine(workspace=tmp_path, rules_path=rules, runtime_root=tmp_path / "rt").evaluate(
        "integration.write", {}).action == "ask"
    create = _create_issue(kit, mode="audit", policy_rules_path=str(rules))
    with pytest.raises(ToolError, match="waiting_approval"):
        create.entrypoint(**_ISSUE)
    assert kit["api"].posts == []


def test_approval_is_bound_to_exact_arguments(kit):
    approvals = kit["approvals"]
    record = approvals.request(operation="integration.write", tool_name="bauer_github_issues__create_issue",
                               reason="x", risk_level="high", payload=dict(_ISSUE))
    approvals.approve(record.id)
    other = {**_ISSUE, "title": "Outro título"}
    assert not approvals.authorize(record.id, operation="integration.write", tool_name="bauer_github_issues__create_issue",
                                   payload=other, run_id=None, session_id=None)
    assert approvals.authorize(record.id, operation="integration.write", tool_name="bauer_github_issues__create_issue",
                               payload=dict(_ISSUE), run_id=None, session_id=None)
    # single use
    assert not approvals.authorize(record.id, operation="integration.write", tool_name="bauer_github_issues__create_issue",
                                   payload=dict(_ISSUE), run_id=None, session_id=None)


def test_remote_failure_is_explicit_and_run_not_completed(kit):
    kit["api"].fail_with = 500
    create = _create_issue(kit)
    human = _human(kit, "approve")
    result = _in_run(kit, [lambda: create.entrypoint(**_ISSUE)])
    human.join(5)
    assert result.status == "failed"
    assert "HTTP 500" in (result.error or "")


def test_out_of_scope_target_or_label_rejected_before_approval_and_network(kit):
    create = _create_issue(kit)
    with pytest.raises(ToolError, match="fora da allowlist"):
        create.entrypoint(repo="evil/repo", title="x")
    with pytest.raises(ToolError, match="label fora"):
        create.entrypoint(**_ISSUE, labels=["prod-deploy"])
    with pytest.raises(ToolError, match="desconhecidos"):
        create.entrypoint(**_ISSUE, assignees=["boss"])
    assert kit["approvals"].list() == []
    assert kit["api"].posts == [] and kit["api"].searches == []
