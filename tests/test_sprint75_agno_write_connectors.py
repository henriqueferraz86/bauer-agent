"""Plano 067 / sprint 75 — conectores de escrita Slack, Linear e GitLab sob aprovação.

Herméticos: as três APIs são um único ``httpx.MockTransport`` que registra
cada request; Kernel, RunManager, ApprovalManager e EventBus reais em
``tmp_path``; o "humano" aprova/nega pelo ApprovalManager (direto ou por
thread, como na sprint 57).
"""

from __future__ import annotations

import json
import re
import textwrap
import threading
import time
from typing import Any
from urllib.parse import unquote

import httpx
import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel
from bauer.core.kernel.entry import run_governed
from bauer.core.policy.approvals import ApprovalManager
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability
from bauer.core.runtime.agno_capabilities.families import write_common as wc
from bauer.core.runtime.agno_capabilities.settings import CapabilitySettingsError, parse_settings
from bauer.core.runtime.run_manager import RunManager
from bauer.runtime_ids import reset_runtime_ids, set_runtime_ids
from bauer.tools.base import ToolError

pytest.importorskip("agno")

# Fake tokens assembled at runtime: no token-shaped literal in the repo
# (GitHub push protection) while the values still look real to the redactor.
TOKENS = {
    "slack": "-".join(["xox" + "b", "1111111111", "2222222222", "sprint75slacksecretvalue"]),
    "linear": "lin_" + "api_" + "sprint75linearsecretvalue0123456789",
    "gitlab": "glp" + "at-" + "sprint75gitlabsecretvalue01",
}

LINEAR_TEAM = "6f1c1b54-6f1a-4c3e-9a51-2f1f6d0b7a10"
LINEAR_PROJECT = "1d2c3b4a-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
LINEAR_LABEL = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"

CONNECTORS: dict[str, dict[str, Any]] = {
    "slack": {
        "ref": "bauer.slack_messages:post_message",
        "tool": "bauer_slack_messages__post_message",
        "id": "bauer.slack_messages",
        "yaml": """
          bauer.slack_messages:
            channels: [C0123ABCD]
            credential: BAUER_TEST_SLACK
        """,
        "env": "BAUER_TEST_SLACK",
        "args": {"channel": "C0123ABCD", "text": "Deploy 1.2 concluído"},
        "other": {"channel": "C0123ABCD", "text": "Deploy 1.3 concluído"},
        "out_of_scope": {"channel": "C9999ZZZZ", "text": "oi"},
        "summary": 'Postar mensagem no Slack em C0123ABCD: "Deploy 1.2 concluído" (identidade: BAUER_TEST_SLACK)',
        "content": "Deploy 1.2 concluído",
    },
    "linear": {
        "ref": "bauer.linear_issues:create_issue",
        "tool": "bauer_linear_issues__create_issue",
        "id": "bauer.linear_issues",
        "yaml": f"""
          bauer.linear_issues:
            teams: {{ENG: {LINEAR_TEAM}}}
            projects: {{Backend: {LINEAR_PROJECT}}}
            labels: {{bug: {LINEAR_LABEL}}}
            credential: BAUER_TEST_LINEAR
        """,
        "env": "BAUER_TEST_LINEAR",
        "args": {"team": "ENG", "title": "Bug no login", "description": "Passos para reproduzir"},
        "other": {"team": "ENG", "title": "Outro título", "description": "Passos para reproduzir"},
        "out_of_scope": {"team": "OPS", "title": "x"},
        "summary": 'Criar issue no Linear (time ENG): "Bug no login" (identidade: BAUER_TEST_LINEAR)',
        "content": "Passos para reproduzir",
    },
    "gitlab": {
        "ref": "bauer.gitlab_issues:create_issue",
        "tool": "bauer_gitlab_issues__create_issue",
        "id": "bauer.gitlab_issues",
        "yaml": """
          bauer.gitlab_issues:
            base_url: https://gitlab.example.com
            projects: [acme/app]
            allowed_labels: [bug]
            credential: BAUER_TEST_GITLAB
        """,
        "env": "BAUER_TEST_GITLAB",
        "args": {"project": "acme/app", "title": "Bug no login", "description": "Passos para reproduzir"},
        "other": {"project": "acme/app", "title": "Outro título", "description": "Passos para reproduzir"},
        "out_of_scope": {"project": "evil/repo", "title": "x"},
        "summary": 'Criar issue no GitLab gitlab.example.com/acme/app: "Bug no login" (identidade: BAUER_TEST_GITLAB)',
        "content": "Passos para reproduzir",
    },
}
NAMES = sorted(CONNECTORS)


class FakeApis:
    """Slack + Linear + GitLab, with failure knobs. Records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.writes: list[dict[str, Any]] = []
        self.status: int | None = None
        self.timeout_after_write = False
        self.redirect = False
        self.linear_ids: dict[str, dict[str, Any]] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.redirect:
            return httpx.Response(302, headers={"Location": "https://evil.example/steal"})
        host = request.url.host
        if host == "slack.com":
            return self._slack(request)
        if host == "api.linear.app":
            return self._linear(request)
        if host in {"gitlab.example.com", "localhost"}:
            return self._gitlab(request)
        return httpx.Response(404)

    def _after_write(self, response: httpx.Response) -> httpx.Response:
        if self.timeout_after_write:
            self.timeout_after_write = False
            raise httpx.ReadTimeout("simulated timeout after the write landed")
        return response

    def _slack(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/chat.postMessage":
            if self.status:
                return httpx.Response(self.status, json={"ok": False})
            body = json.loads(request.content)
            ts = f"1700000000.{len(self.writes) + 1:06d}"
            self.writes.append({**body, "ts": ts})
            return self._after_write(httpx.Response(200, json={"ok": True, "channel": body["channel"], "ts": ts}))
        if request.method == "GET" and request.url.path == "/api/conversations.history":
            return httpx.Response(200, json={"ok": True, "messages": [
                {"ts": w["ts"], "text": w["text"], "metadata": w.get("metadata")} for w in self.writes
            ]})
        return httpx.Response(404)

    def _linear(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "issueCreate" in body["query"]:
            if self.status:
                return httpx.Response(self.status, json={"errors": [{"message": "boom"}]})
            data = body["variables"]["input"]
            if data["id"] in self.linear_ids:
                return httpx.Response(400, json={"errors": [{"message": "Entity already exists"}]})
            issue = {"id": data["id"], "identifier": f"ENG-{len(self.writes) + 1}",
                     "url": f"https://linear.app/acme/issue/ENG-{len(self.writes) + 1}"}
            self.linear_ids[data["id"]] = issue
            self.writes.append(data)
            return self._after_write(httpx.Response(200, json={"data": {"issueCreate": {"success": True, "issue": issue}}}))
        issue = self.linear_ids.get(body["variables"]["id"])
        if issue is None:
            return httpx.Response(200, json={"data": None, "errors": [{"message": "Entity not found"}]})
        return httpx.Response(200, json={"data": {"issue": issue}})

    def _gitlab(self, request: httpx.Request) -> httpx.Response:
        if unquote(request.url.raw_path.decode().split("?")[0]) != "/api/v4/projects/acme/app/issues":
            return httpx.Response(404)
        assert b"acme%2Fapp" in request.url.raw_path  # project path is one encoded segment
        if request.method == "POST":
            if self.status:
                return httpx.Response(self.status, json={"message": "boom"})
            body = json.loads(request.content)
            iid = len(self.writes) + 1
            self.writes.append(body)
            return self._after_write(httpx.Response(201, json={
                "iid": iid, "web_url": f"https://gitlab.example.com/acme/app/-/issues/{iid}"}))
        search = request.url.params.get("search", "")
        hits = [{"iid": i + 1, "web_url": "u", "description": w["description"]}
                for i, w in enumerate(self.writes) if search and search in w["description"]]
        return httpx.Response(200, json=hits)


@pytest.fixture()
def kit(tmp_path, monkeypatch):
    api = FakeApis()
    monkeypatch.setattr(wc, "_TRANSPORT", httpx.MockTransport(api))
    monkeypatch.setenv("BAUER_TEST_SLACK", TOKENS["slack"])
    monkeypatch.setenv("BAUER_TEST_LINEAR", TOKENS["linear"])
    monkeypatch.setenv("BAUER_TEST_GITLAB", TOKENS["gitlab"])
    target = tmp_path / "agno_capabilities.yaml"
    body = "capabilities:\n" + "".join(textwrap.indent(textwrap.dedent(c["yaml"]).strip("\n"), "  ") + "\n"
                                      for c in CONNECTORS.values())
    target.write_text(body, encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))
    root = tmp_path / "runtime"
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    approvals = ApprovalManager(root=root, event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, approvals=approvals, heartbeat_interval_s=0)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return {"api": api, "root": root, "bus": bus, "runs": runs, "approvals": approvals,
            "kernel": kernel, "workspace": workspace, "tmp": tmp_path, "settings": target}


def _tool(kit, name: str, mode: str = "enforce", policy_rules_path: str | None = None, dry_run: bool = False):
    spec = CONNECTORS[name]
    config = {"workspace": str(kit["workspace"]), "db_file": str(kit["tmp"] / "agno.db")}
    if policy_rules_path:
        config["policy_rules_path"] = policy_rules_path
    adapter = AgnoRuntimeAdapter(adapter_config=config, runtime_event_bus=kit["bus"],
                                 runtime_policy_root=kit["root"], runtime_policy_mode=mode)
    if dry_run:
        build = adapter._build_tool_router

        def build_dry():
            router = build()
            router._dry_run = True
            return router

        adapter._build_tool_router = build_dry  # type: ignore[method-assign]
    return {fn.name: fn for fn in adapter._map_tools([spec["ref"]])}[spec["tool"]]


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


def _pending_id(exc: pytest.ExceptionInfo) -> str:
    match = re.search(r"approval_id=([\w-]+)", str(exc.value))
    assert match, str(exc.value)
    return match.group(1)


def _leak_dump(kit) -> str:
    events = kit["bus"].list_events()
    records = kit["approvals"].list()
    return json.dumps([e.data for e in events] + [e.message for e in events]
                      + [r.reason for r in records], ensure_ascii=False, default=str)


def _assert_no_token(text: str) -> None:
    for token in TOKENS.values():
        assert token not in text
        assert token[8:30] not in text


# ── catálogo ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", NAMES)
def test_catalog_state_transitions(name, kit, tmp_path, monkeypatch):
    spec = CONNECTORS[name]
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(tmp_path / "none.yaml"))
    row = next(r for r in catalog_entries() if r["id"] == spec["id"])
    assert row["state"] == "needs_configuration" and row["risk"] == "write"
    assert row["default_actions"] == [] and row["source"] == "bauer"
    with pytest.raises(CapabilityError) as exc:
        resolve_capability(spec["ref"])
    assert exc.value.state == "needs_configuration"

    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(kit["settings"]))
    row = next(r for r in catalog_entries() if r["id"] == spec["id"])
    assert row["state"] == "ready"
    row = next(r for r in catalog_entries(agent_tools=[spec["ref"]]) if r["id"] == spec["id"])
    assert row["state"] == "enabled"
    # never granted implicitly: the bare id has no default (read) action
    with pytest.raises(CapabilityError) as exc:
        resolve_capability(spec["id"])
    assert exc.value.state == "blocked_by_policy"
    assert resolve_capability(spec["ref"]).actions[0].operation == "integration.write"


def test_sdk_connectors_stay_blocked():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in ("agno.slack", "agno.linear", "agno.gitlab", "agno.google.gmail"):
        assert rows[capability_id]["state"] == "blocked_by_policy"


# ── fluxo aprovado, idempotência, eventos ───────────────────────────────────


@pytest.mark.parametrize("name", NAMES)
def test_approved_call_writes_once_with_safe_summary_and_redacted_events(name, kit):
    spec = CONNECTORS[name]
    tool = _tool(kit, name)
    seen: list = []
    human = _human(kit, "approve", seen=seen)
    result = _in_run(kit, [lambda: tool.entrypoint(**spec["args"])])
    human.join(5)

    assert result.status == "completed", result.error
    assert len(kit["api"].writes) == 1
    created = json.loads(result.output[0])
    assert "idempotent_replay" not in created
    record = seen[0]
    assert record.operation == "integration.write"
    assert record.reason == spec["summary"]
    # the credential really reached the API (the test is meaningful) …
    sent = [r for r in kit["api"].requests if r.method == "POST"][0]
    assert any(TOKENS[name] in value for value in sent.headers.values())
    # … but never events, messages or approval cards; and no argument values
    dump = _leak_dump(kit)
    _assert_no_token(dump)
    assert spec["content"] not in json.dumps([e.data for e in kit["bus"].list_events()], ensure_ascii=False)
    called = [e for e in kit["bus"].list_events() if e.tool_name == spec["tool"] and "args" in (e.data or {})]
    assert called and all(isinstance(e.data["args"], (list, dict)) for e in called)


@pytest.mark.parametrize("name", NAMES)
def test_retry_of_same_approved_call_does_not_duplicate(name, kit):
    spec = CONNECTORS[name]
    tool = _tool(kit, name)
    human = _human(kit, "approve", times=2)
    result = _in_run(kit, [lambda: tool.entrypoint(**spec["args"]), lambda: tool.entrypoint(**spec["args"])])
    human.join(5)

    assert result.status == "completed", result.error
    first, second = (json.loads(item) for item in result.output)
    assert second["idempotent_replay"] is True
    assert {k: v for k, v in second.items() if k != "idempotent_replay"} == first
    assert len(kit["api"].writes) == 1


@pytest.mark.parametrize("name", NAMES)
def test_uncertain_write_is_verified_not_resent(name, kit):
    """Timeout after the write landed: the retry finds the marker, no duplicate."""
    spec = CONNECTORS[name]
    kit["api"].timeout_after_write = True
    tool = _tool(kit, name, mode="audit")
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    first_id = _pending_id(pending)
    kit["approvals"].approve(first_id)
    with pytest.raises(ToolError, match="incerto"):
        tool.entrypoint(**spec["args"], approval_id=first_id)
    assert len(kit["api"].writes) == 1

    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    second_id = _pending_id(pending)
    kit["approvals"].approve(second_id)
    replay = json.loads(tool.entrypoint(**spec["args"], approval_id=second_id))
    assert replay["idempotent_replay"] is True
    assert len(kit["api"].writes) == 1


def test_slack_uncertain_without_marker_is_never_resent(kit):
    """Slack has no server-side dedupe: unconfirmed uncertainty fails closed."""
    spec = CONNECTORS["slack"]
    tool = _tool(kit, "slack", mode="audit")
    kit["api"].status = 503
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    approval = _pending_id(pending)
    kit["approvals"].approve(approval)
    with pytest.raises(ToolError, match="HTTP 503"):
        tool.entrypoint(**spec["args"], approval_id=approval)
    kit["api"].status = None
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    approval = _pending_id(pending)
    kit["approvals"].approve(approval)
    with pytest.raises(ToolError, match="não reenviada"):
        tool.entrypoint(**spec["args"], approval_id=approval)
    assert kit["api"].writes == []
    assert [r.url.path for r in kit["api"].requests if r.method == "POST"] == ["/api/chat.postMessage"]


def test_linear_resend_after_uncertain_failure_reuses_issue_id(kit):
    spec = CONNECTORS["linear"]
    tool = _tool(kit, "linear", mode="audit")
    kit["api"].status = 502
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    approval = _pending_id(pending)
    kit["approvals"].approve(approval)
    with pytest.raises(ToolError, match="HTTP 502"):
        tool.entrypoint(**spec["args"], approval_id=approval)
    kit["api"].status = None
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    approval = _pending_id(pending)
    kit["approvals"].approve(approval)
    created = json.loads(tool.entrypoint(**spec["args"], approval_id=approval))
    ids = [json.loads(r.content)["variables"]["input"]["id"] for r in kit["api"].requests
           if r.method == "POST" and b"issueCreate" in r.content]
    assert len(ids) == 2 and ids[0] == ids[1] == created["id"]
    assert len(kit["api"].writes) == 1


# ── aprovação: obrigatória, vinculada, uso único ─────────────────────────────


@pytest.mark.parametrize("name", NAMES)
def test_no_request_without_approval_and_denial_never_writes(name, kit):
    spec = CONNECTORS[name]
    tool = _tool(kit, name)
    human = _human(kit, "deny")
    result = _in_run(kit, [lambda: tool.entrypoint(**spec["args"])])
    human.join(5)
    assert result.status != "completed"
    assert kit["api"].requests == []


@pytest.mark.parametrize("name", NAMES)
def test_approval_bound_to_exact_arguments_and_single_use(name, kit):
    spec = CONNECTORS[name]
    tool = _tool(kit, name, mode="audit")
    with pytest.raises(ToolError, match="waiting_approval") as pending:
        tool.entrypoint(**spec["args"])
    approval = _pending_id(pending)
    assert kit["api"].requests == []
    kit["approvals"].approve(approval)

    with pytest.raises(ToolError, match="does not authorize"):
        tool.entrypoint(**spec["other"], approval_id=approval)
    assert kit["api"].requests == []

    json.loads(tool.entrypoint(**spec["args"], approval_id=approval))
    assert len(kit["api"].writes) == 1
    requests_after = len(kit["api"].requests)
    with pytest.raises(ToolError, match="does not authorize|consumed"):
        tool.entrypoint(**spec["args"], approval_id=approval)
    assert len(kit["api"].requests) == requests_after


@pytest.mark.parametrize("name", NAMES)
def test_audit_mode_does_not_auto_approve(name, kit):
    tool = _tool(kit, name, mode="audit")
    with pytest.raises(ToolError, match="waiting_approval"):
        tool.entrypoint(**CONNECTORS[name]["args"])
    assert kit["api"].requests == []


@pytest.mark.parametrize("name", NAMES)
def test_policy_off_refuses(name, kit):
    tool = _tool(kit, name, mode="off")
    with pytest.raises(ToolError, match="policy habilitada"):
        tool.entrypoint(**CONNECTORS[name]["args"])
    assert kit["api"].requests == [] and kit["approvals"].list() == []


@pytest.mark.parametrize("name", NAMES)
def test_custom_policy_cannot_lower_below_ask(name, kit, tmp_path):
    rules = tmp_path / "policy.yaml"
    rules.write_text("rules:\n  - id: all.allow\n    operation: integration.write\n    action: allow\n", encoding="utf-8")
    tool = _tool(kit, name, mode="audit", policy_rules_path=str(rules))
    with pytest.raises(ToolError, match="waiting_approval"):
        tool.entrypoint(**CONNECTORS[name]["args"])
    assert kit["api"].requests == []


@pytest.mark.parametrize("name", NAMES)
def test_dry_run_simulates_after_approval_and_sends_nothing(name, kit):
    spec = CONNECTORS[name]
    tool = _tool(kit, name, mode="audit", dry_run=True)
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    approval = _pending_id(pending)
    kit["approvals"].approve(approval)
    result = tool.entrypoint(**spec["args"], approval_id=approval)
    assert "dry_run" in result.lower()
    assert kit["api"].requests == []
    assert not (kit["root"] / "agno_capabilities" / "write_ledger").exists()


# ── preflight: fora de escopo antes da aprovação ─────────────────────────────


@pytest.mark.parametrize("name", NAMES)
def test_out_of_scope_unknown_and_secret_rejected_before_approval(name, kit):
    spec = CONNECTORS[name]
    tool = _tool(kit, name)
    with pytest.raises(ToolError, match="fora da allowlist"):
        tool.entrypoint(**spec["out_of_scope"])
    with pytest.raises(ToolError, match="desconhecidos"):
        tool.entrypoint(**spec["args"], assignees=["boss"])
    secret_args = dict(spec["args"])
    field = "text" if name == "slack" else "title"
    secret_args[field] = "token ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    with pytest.raises(ToolError, match="material secreto"):
        tool.entrypoint(**secret_args)
    assert kit["approvals"].list() == []
    assert kit["api"].requests == []


@pytest.mark.parametrize("name", NAMES)
def test_missing_credential_rejected_before_approval(name, kit, monkeypatch):
    monkeypatch.delenv(CONNECTORS[name]["env"])
    monkeypatch.setattr("bauer.core.runtime.agno_capabilities.credentials._pool",
                        lambda: (_ for _ in ()).throw(RuntimeError("no vault")))
    tool = _tool(kit, name)
    with pytest.raises(ToolError, match="credencial"):
        tool.entrypoint(**CONNECTORS[name]["args"])
    assert kit["approvals"].list() == [] and kit["api"].requests == []


def test_out_of_scope_labels_and_projects(kit):
    linear = _tool(kit, "linear")
    with pytest.raises(ToolError, match="projeto fora"):
        linear.entrypoint(team="ENG", title="x", project="Secreto")
    with pytest.raises(ToolError, match="label fora"):
        linear.entrypoint(team="ENG", title="x", labels=["prod"])
    gitlab = _tool(kit, "gitlab")
    with pytest.raises(ToolError, match="label fora"):
        gitlab.entrypoint(project="acme/app", title="x", labels=["deploy"])
    with pytest.raises(ToolError, match="fora da allowlist"):
        gitlab.entrypoint(project="acme/app/../other", title="x")
    slack = _tool(kit, "slack")
    with pytest.raises(ToolError, match="broadcast"):
        slack.entrypoint(channel="C0123ABCD", text="<!channel> deploy")
    with pytest.raises(ToolError, match="thread_ts"):
        slack.entrypoint(channel="C0123ABCD", text="oi", thread_ts="x")
    assert kit["approvals"].list() == [] and kit["api"].requests == []


# ── erros remotos sem vazar token ────────────────────────────────────────────


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("status", [401, 500])
def test_remote_failure_is_explicit_without_token(name, status, kit):
    spec = CONNECTORS[name]
    kit["api"].status = status
    tool = _tool(kit, name)
    human = _human(kit, "approve")
    result = _in_run(kit, [lambda: tool.entrypoint(**spec["args"])])
    human.join(5)
    assert result.status == "failed"
    assert f"HTTP {status}" in (result.error or "")
    _assert_no_token(result.error or "")
    _assert_no_token(_leak_dump(kit))
    assert kit["api"].writes == []


def test_slack_api_error_code_is_mapped(kit):
    from bauer.core.runtime.agno_capabilities.families import slack_write

    assert "not_in_channel" in slack_write._slack_error({"ok": False, "error": "not_in_channel"})
    assert slack_write._slack_error({"ok": False, "error": "xoxb-evil token"}) == "Slack recusou a mensagem"


# ── --local ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", NAMES)
def test_local_only_refuses_cloud_targets_before_approval(name, kit, monkeypatch):
    monkeypatch.setenv("BAUER_LOCAL_ONLY", "1")
    tool = _tool(kit, name)
    with pytest.raises(ToolError, match="--local"):
        tool.entrypoint(**CONNECTORS[name]["args"])
    assert kit["approvals"].list() == [] and kit["api"].requests == []


def test_local_only_accepts_self_hosted_gitlab_on_this_machine(kit, monkeypatch):
    kit["settings"].write_text(textwrap.dedent("""
        capabilities:
          bauer.gitlab_issues:
            base_url: http://localhost:8080
            projects: [acme/app]
            credential: BAUER_TEST_GITLAB
    """), encoding="utf-8")
    monkeypatch.setenv("BAUER_LOCAL_ONLY", "1")
    tool = _tool(kit, "gitlab", mode="audit")
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**CONNECTORS["gitlab"]["args"])
    approval = _pending_id(pending)
    kit["approvals"].approve(approval)
    created = json.loads(tool.entrypoint(**CONNECTORS["gitlab"]["args"], approval_id=approval))
    assert created["iid"] == 1
    assert {str(r.url.host) for r in kit["api"].requests} == {"localhost"}


# ── GitLab: base_url do operador, SSRF e redirect ───────────────────────────


@pytest.mark.parametrize("base_url", [
    "http://gitlab.example.com",            # plain http off-machine
    "https://user:pw@gitlab.example.com",   # userinfo
    "https://gitlab.example.com/?x=1",      # query
    "https://169.254.169.254",              # metadata
    "https://metadata.google.internal",
    "https://[fe80::1]",                    # link-local
    "ftp://gitlab.example.com",
    "https://gitlab.example.com/a/../b",
])
def test_gitlab_base_url_rejected(base_url):
    raw = {"capabilities": {"bauer.gitlab_issues": {"base_url": base_url, "projects": ["acme/app"]}}}
    with pytest.raises(CapabilitySettingsError) as exc:
        parse_settings(raw)
    assert base_url not in str(exc.value)


@pytest.mark.parametrize("base_url", ["https://gitlab.example.com/gitlab", "https://10.0.0.5", "http://127.0.0.1:8080"])
def test_gitlab_base_url_accepted(base_url):
    raw = {"capabilities": {"bauer.gitlab_issues": {"base_url": base_url, "projects": ["Acme/Sub/App"]}}}
    section = parse_settings(raw).section("bauer.gitlab_issues")
    assert section.base_url == base_url and section.projects == ["acme/sub/app"]


@pytest.mark.parametrize("name", NAMES)
def test_redirect_is_never_followed(name, kit):
    spec = CONNECTORS[name]
    kit["api"].redirect = True
    tool = _tool(kit, name, mode="audit")
    with pytest.raises(ToolError) as pending:
        tool.entrypoint(**spec["args"])
    approval = _pending_id(pending)
    kit["approvals"].approve(approval)
    with pytest.raises(ToolError, match="redirect") as exc:
        tool.entrypoint(**spec["args"], approval_id=approval)
    _assert_no_token(str(exc.value))
    assert len(kit["api"].requests) == 1
    assert all(r.url.host != "evil.example" for r in kit["api"].requests)


# ── ledger ───────────────────────────────────────────────────────────────────


def test_ledger_reservation_is_exclusive(tmp_path):
    ledger = wc.WriteLedger(tmp_path, "unit")
    key = "0123456789abcdef0123456789abcdef"
    assert ledger.reserve(key) is True
    assert ledger.reserve(key) is False
    ledger.release(key)
    assert ledger.reserve(key) is True
    ledger.complete(key, {"x": 1})
    assert ledger.get(key) == {"status": "done", "result": {"x": 1}}
    with pytest.raises(ToolError):
        ledger.get("../../etc/passwd")


def test_secret_scanner_failure_fails_closed(monkeypatch):
    from bauer.core.runtime.agno_capabilities import bridge

    def broken(*_args, **_kwargs):
        raise RuntimeError("scanner down")

    monkeypatch.setattr("bauer.secrets_scanner.scan", broken)
    with pytest.raises(ToolError, match="material secreto"):
        bridge._refuse_secret_material({"text": "olá"})
