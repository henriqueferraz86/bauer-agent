"""Plano 057 / sprint 51 — pesquisa e desenvolvimento em modo de leitura.

Herméticos: GitHub via ``httpx.MockTransport``; MCP via servidor stdio fake
(``sys.executable -c``); busca web com o método do SDK monkeypatchado.
"""

from __future__ import annotations

import json
import sys
import textwrap
import types
from pathlib import Path

import httpx
import pytest

from bauer.core.events.bus import EventBus
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.adapters.base import RuntimeAdapterError
from bauer.core.runtime.agno_capabilities import catalog
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability
from bauer.core.runtime.agno_capabilities.families import github as gh
from bauer.core.runtime.agno_capabilities.families import mcp as mcp_family
from bauer.tools.base import ToolError

pytest.importorskip("agno")


def _settings(tmp_path: Path, monkeypatch, body: str) -> Path:
    target = tmp_path / "agno_capabilities.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))
    return target


def _adapter(tmp_path: Path, bus: EventBus | None = None) -> AgnoRuntimeAdapter:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return AgnoRuntimeAdapter(
        adapter_config={"workspace": str(workspace), "db_file": str(tmp_path / "agno.db")},
        runtime_policy_root=tmp_path / "runtime",
        runtime_event_bus=bus,
    )


def _tools(adapter: AgnoRuntimeAdapter, ref: str) -> dict:
    return {fn.name: fn for fn in adapter._map_tools([ref])}


# ── busca web ────────────────────────────────────────────────────────────────


def test_url_fetchers_are_incompatible_and_point_to_bauer_web_fetch():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in ("agno.website", "agno.jina", "agno.firecrawl", "agno.webbrowser", "agno.api"):
        assert rows[capability_id]["state"] == "incompatible"
        assert "bauer.web_fetch" in rows[capability_id]["reason"]
    from bauer.core.runtime.agno_catalog import AgnoCapabilityCatalog

    listed = {row["id"]: row for row in AgnoCapabilityCatalog().list()}
    assert listed["bauer.web_fetch"]["source"] == "bauer"


def test_websearch_runs_through_router_and_secret_query_is_denied(tmp_path, monkeypatch):
    from agno.tools.websearch import WebSearchTools

    calls: list[str] = []

    def web_search(self, query: str, max_results: int = 5) -> str:
        calls.append(query)
        return json.dumps([{"title": "Bauer", "href": "https://example.org"}])

    monkeypatch.setattr(WebSearchTools, "web_search", web_search)
    search = _tools(_adapter(tmp_path), "agno.websearch:web_search")["agno_websearch__web_search"]

    assert "Bauer" in search.entrypoint(query="bauer agent")
    with pytest.raises(ToolError, match="material secreto"):
        search.entrypoint(query="token sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert calls == ["bauer agent"]


# ── GitHub nativo ────────────────────────────────────────────────────────────


class _GithubApi:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/repos/acme/app":
            return httpx.Response(200, json={"full_name": "acme/app", "default_branch": "main", "private": True})
        if path == "/repos/acme/app/issues":
            return httpx.Response(200, json=[
                {"number": 1, "title": "Bug", "state": "open", "user": {"login": "ana"}},
                {"number": 2, "title": "PR", "pull_request": {}},
            ])
        if path == "/repos/acme/app/contents/README.md":
            import base64

            return httpx.Response(200, json={"type": "file", "path": "README.md", "size": 5,
                                             "content": base64.b64encode(b"hello").decode()})
        if path == "/search/issues":
            return httpx.Response(200, json={"items": [{"number": 7, "title": "x", "state": "open"}]})
        if path == "/repos/acme/broken":
            return httpx.Response(200, content=b"<html>not json")
        if path == "/repos/acme/slow":
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture()
def github_api(tmp_path, monkeypatch):
    api = _GithubApi()
    monkeypatch.setattr(gh, "_TRANSPORT", httpx.MockTransport(api))
    monkeypatch.setenv("BAUER_TEST_GH_TOKEN", "ghp_testtokenvalue1234567890")
    _settings(tmp_path, monkeypatch, """
        version: 1
        capabilities:
          bauer.github:
            repositories: [acme/app, acme/broken, acme/slow]
            credential: BAUER_TEST_GH_TOKEN
            max_items: 5
    """)
    return api


def test_github_reads_only_allowlisted_repositories(tmp_path, github_api):
    bus = EventBus(root=tmp_path / "events")
    tools = _tools(_adapter(tmp_path, bus), "bauer.github")

    repo = json.loads(tools["bauer_github__get_repository"].entrypoint(repo="ACME/app"))
    assert repo["full_name"] == "acme/app"
    issues = json.loads(tools["bauer_github__list_issues"].entrypoint(repo="acme/app"))
    assert [item["number"] for item in issues] == [1]  # PRs filtered out
    assert github_api.requests[0].headers["Authorization"] == "Bearer ghp_testtokenvalue1234567890"
    assert github_api.requests[1].url.params["per_page"] == "5"

    sent = len(github_api.requests)
    with pytest.raises(ToolError, match="fora da allowlist"):
        tools["bauer_github__get_repository"].entrypoint(repo="other/secret")
    assert len(github_api.requests) == sent  # denied before the network

    events = json.dumps([e.data for e in bus.list_events()] + [e.message for e in bus.list_events()])
    assert "ghp_testtokenvalue" not in events
    assert "acme/app" not in events  # argument values never reach telemetry


def test_github_mutations_are_not_available(github_api):
    with pytest.raises(CapabilityError) as exc:
        resolve_capability("bauer.github:create_issue")
    assert exc.value.state == "blocked_by_policy"
    resolved = resolve_capability("bauer.github")
    assert all(action.risk == "read" for action in resolved.actions)


def test_github_search_cannot_widen_scope(tmp_path, github_api):
    search = _tools(_adapter(tmp_path), "bauer.github:search_issues")["bauer_github__search_issues"]
    search.entrypoint(repo="acme/app", query="crash repo:evil/private org:evil")

    query = github_api.requests[-1].url.params["q"]
    assert "evil" not in query
    assert query.endswith("repo:acme/app")


def test_github_file_path_traversal_and_bad_responses(tmp_path, github_api):
    tools = _tools(_adapter(tmp_path), "bauer.github:get_file,get_repository")
    get_file = tools["bauer_github__get_file"]

    assert json.loads(get_file.entrypoint(repo="acme/app", path="README.md"))["content"] == "hello"
    with pytest.raises(ToolError, match="caminho inválido"):
        get_file.entrypoint(repo="acme/app", path="../../etc/passwd")
    with pytest.raises(ToolError, match="malformada"):
        tools["bauer_github__get_repository"].entrypoint(repo="acme/broken")
    with pytest.raises(ToolError, match="timeout"):
        tools["bauer_github__get_repository"].entrypoint(repo="acme/slow")


def test_github_without_settings_needs_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(tmp_path / "missing.yaml"))
    row = next(row for row in catalog_entries() if row["id"] == "bauer.github")
    assert row["state"] == "needs_configuration"
    with pytest.raises(RuntimeAdapterError, match="needs_configuration"):
        _adapter(tmp_path)._map_tools(["bauer.github"])


# ── GitHub via SDK (PyGithub opcional) ──────────────────────────────────────


def test_sdk_github_reports_missing_dependency_without_breaking_catalog(tmp_path, monkeypatch):
    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.github:
            repositories: [acme/app]
    """)
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: name != "github")
    row = next(row for row in catalog_entries() if row["id"] == "agno.github")
    assert row["state"] == "dependency_missing"
    assert "pygithub" in row["reason"]


def test_sdk_github_guard_blocks_repo_outside_allowlist(tmp_path, monkeypatch):
    from agno.tools import Toolkit

    seen: list[str] = []

    class FakeGithubTools(Toolkit):
        def __init__(self, access_token=None, base_url=None, **kwargs):
            super().__init__(name="github", tools=[self.get_repository, self.list_issues], **kwargs)

        def get_repository(self, repo_name: str) -> str:
            seen.append(repo_name)
            return json.dumps({"name": repo_name})

        def list_issues(self, repo_name: str, state: str = "open") -> str:
            return "[]"

    fake_module = types.ModuleType("agno.tools.github")
    fake_module.GithubTools = FakeGithubTools
    monkeypatch.setitem(sys.modules, "agno.tools.github", fake_module)
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: True)
    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.github:
            repositories: [acme/app]
    """)
    get_repo = _tools(_adapter(tmp_path), "agno.github:get_repository")["agno_github__get_repository"]

    assert "acme/app" in get_repo.entrypoint(repo_name="acme/app")
    with pytest.raises(ToolError, match="fora da allowlist"):
        get_repo.entrypoint(repo_name="evil/repo")
    assert seen == ["acme/app"]


# ── MCP ──────────────────────────────────────────────────────────────────────

_FAKE_MCP = r"""
import json, os, sys, time
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    req = json.loads(line)
    if "id" not in req:
        continue
    method, rid = req.get("method"), req["id"]
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05", "capabilities": {}}})
    elif method == "tools/call":
        name = req["params"]["name"]; args = req["params"].get("arguments") or {}
        if name == "echo":
            text = "echo:" + str(args.get("text")) + " leaked=" + str("BAUER_TEST_PARENT_SECRET" in os.environ)
            text += " injected=" + str(os.environ.get("DOCS_TOKEN") == "docs-secret-value")
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}]}})
        elif name == "slow":
            time.sleep(5)
        elif name == "garbage":
            sys.stdout.write("this is not json\n"); sys.stdout.flush()
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -1, "message": "boom"}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nope"}})
"""


@pytest.fixture()
def mcp_settings(tmp_path, monkeypatch):
    script = tmp_path / "fake_mcp.py"
    script.write_text(_FAKE_MCP, encoding="utf-8")
    monkeypatch.setenv("BAUER_TEST_PARENT_SECRET", "must-not-leak")
    monkeypatch.setenv("BAUER_TEST_DOCS_TOKEN", "docs-secret-value")
    command = json.dumps([sys.executable, str(script)])
    _settings(tmp_path, monkeypatch, f"""
        capabilities:
          bauer.mcp:
            servers:
              docs:
                transport: stdio
                command: {command}
                timeout_seconds: 1.5
                env_credentials:
                  DOCS_TOKEN: BAUER_TEST_DOCS_TOKEN
                tools:
                  echo:
                    description: Ecoa um texto (admin).
                    parameters:
                      type: object
                      properties:
                        text: {{type: string}}
                      required: [text]
                  slow: {{}}
                  garbage: {{}}
                  wipe:
                    risk: write
    """)


def test_mcp_default_grant_is_read_tools_and_write_is_blocked(mcp_settings):
    resolved = resolve_capability("bauer.mcp")
    assert {a.name for a in resolved.actions} == {"docs__echo", "docs__slow", "docs__garbage"}
    with pytest.raises(CapabilityError) as exc:
        resolve_capability("bauer.mcp:docs__wipe")
    assert exc.value.state == "blocked_by_policy"
    with pytest.raises(CapabilityError, match="não está classificada"):
        resolve_capability("bauer.mcp:docs__undeclared")


def test_mcp_call_uses_scoped_env_and_frames_untrusted_output(tmp_path, mcp_settings):
    echo = _tools(_adapter(tmp_path), "bauer.mcp:docs__echo")["bauer_mcp__docs__echo"]

    assert echo.description == "Ecoa um texto (admin)."
    out = echo.entrypoint(text="oi")
    assert out.startswith("[conteúdo de servidor MCP externo")
    assert "echo:oi" in out
    assert "leaked=False" in out
    assert "injected=True" in out


def test_mcp_server_not_started_until_a_granted_tool_is_called(tmp_path, mcp_settings, monkeypatch):
    started: list[str] = []
    monkeypatch.setitem(mcp_family._CALLERS, "stdio", lambda *args: started.append(args[0]) or "ok")

    tools = _tools(_adapter(tmp_path), "bauer.mcp:docs__echo")
    assert started == []
    tools["bauer_mcp__docs__echo"].entrypoint(text="x")
    assert started == ["docs"]


def test_mcp_timeout_and_malformed_response_do_not_break_run(tmp_path, mcp_settings):
    tools = _tools(_adapter(tmp_path), "bauer.mcp:docs__slow,docs__garbage")

    with pytest.raises(ToolError, match="timeout"):
        tools["bauer_mcp__docs__slow"].entrypoint(arguments={})
    with pytest.raises(ToolError, match="falhou"):
        tools["bauer_mcp__docs__garbage"].entrypoint(arguments={})


def test_mcp_settings_reject_invalid_server_definitions(tmp_path, monkeypatch):
    from bauer.core.runtime.agno_capabilities.settings import CapabilitySettingsError, parse_settings

    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.mcp": {"servers": {"x": {"transport": "stdio", "tools": {"a": {}}}}}}})
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.mcp": {"servers": {"x": {"transport": "http", "url": "file:///etc",
                                                                          "tools": {"a": {}}}}}}})
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"bauer.mcp": {"servers": {"Bad-Name": {"transport": "http",
                                                                                 "url": "https://x", "tools": {"a": {}}}}}}})
