"""Plano 067 / sprint 73 — pesquisa somente leitura instalável.

Roda sempre (sem o extra ``agno-research``): SearXNG nativo, guardas do
download do arXiv, contrato do extra no ``pyproject.toml``/``uv.lock``.
Herméticos: ``httpx.MockTransport`` e DNS falso; nenhuma rede.
Os testes com as bibliotecas reais ficam em
``test_agno_research_extras_installed.py``.
"""

from __future__ import annotations

import json
import socket
import textwrap
import tomllib
from pathlib import Path

import httpx
import pytest

from bauer.core.runtime.agno_capabilities import catalog
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability
from bauer.core.runtime.agno_capabilities.families import research_extras as rx
from bauer.core.runtime.agno_capabilities.settings import CapabilitySettingsError, parse_settings
from bauer.tools.base import ToolError

pytest.importorskip("agno")

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_PACKAGES = {
    "wikipedia", "arxiv", "pypdf", "yfinance", "youtube-transcript-api",
    "tavily-python", "exa-py", "google-search-results", "linkup-sdk",
}


def _settings(tmp_path: Path, monkeypatch, body: str) -> Path:
    target = tmp_path / "agno_capabilities.yaml"
    target.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(target))
    return target


def _tools(tmp_path: Path, ref: str) -> dict:
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(workspace)}, runtime_policy_root=tmp_path / "rt")
    return {fn.name: fn for fn in adapter._map_tools([ref])}


@pytest.fixture()
def fake_dns(monkeypatch):
    """``socket.getaddrinfo`` answering from a table (no real DNS)."""
    table: dict[str, str] = {}

    def getaddrinfo(host, *args, **kwargs):
        address = table.get(host, "93.184.216.34")
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return table


# ── contrato do extra (instalação padrão inalterada) ─────────────────────────


def _lock_packages() -> dict[str, dict]:
    data = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {pkg["name"]: pkg for pkg in data["package"]}


def _closure(packages: dict[str, dict], roots: list[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen or name not in packages:
            continue
        seen.add(name)
        stack.extend(dep["name"] for dep in packages[name].get("dependencies", []))
    return seen


def test_research_extra_is_optional_and_default_install_unchanged():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extra = {req.split(">")[0].split("=")[0].split("[")[0].strip().lower() for req in project["optional-dependencies"]["agno-research"]}
    assert extra == RESEARCH_PACKAGES
    default = {req.split(">")[0].split("=")[0].split("[")[0].strip().lower() for req in project["dependencies"]}
    assert not default & RESEARCH_PACKAGES

    packages = _lock_packages()
    root = packages["bauer-agent"]
    default_closure = _closure(packages, [dep["name"] for dep in root.get("dependencies", [])])
    heavy = RESEARCH_PACKAGES | {"curl-cffi", "pandas", "peewee", "tiktoken", "multitasking"}
    assert not default_closure & heavy, "o extra agno-research vazou para a instalação padrão"
    locked_extra = {dep["name"] for dep in root["optional-dependencies"]["agno-research"]}
    assert locked_extra == RESEARCH_PACKAGES  # travado no uv.lock (versões fixas)


def test_brave_stays_dependency_missing_with_reason(monkeypatch):
    monkeypatch.setattr(catalog, "module_spec_exists", lambda name: name != "brave")
    row = next(r for r in catalog_entries() if r["id"] == "agno.bravesearch")
    assert row["state"] == "dependency_missing"
    assert "httpx<0.26" in row["reason"]


# ── SearXNG: host do operador + SSRF ─────────────────────────────────────────


class _Searx:
    def __init__(self, payload=None, status=200, headers=None, content=None):
        self.requests: list[httpx.Request] = []
        self.payload = payload if payload is not None else {
            "results": [
                {"title": f"r{i}", "url": f"https://e.org/{i}", "content": "x" * 5_000, "engine": "ddg"}
                for i in range(30)
            ],
            "suggestions": ["bauer agent"],
        }
        self.status, self.headers, self.content = status, headers or {}, content

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.content is not None:
            return httpx.Response(self.status, headers=self.headers, content=self.content)
        return httpx.Response(self.status, headers=self.headers, json=self.payload)


@pytest.fixture()
def searx(tmp_path, monkeypatch, fake_dns):
    server = _Searx()
    monkeypatch.setattr(rx, "_SEARXNG_TRANSPORT", httpx.MockTransport(server))
    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.searxng:
            host: https://search.example.org/searx/
            engines: [duckduckgo, wikipedia]
            max_results: 4
    """)
    return server


def test_searxng_needs_operator_host(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(tmp_path / "missing.yaml"))
    row = next(r for r in catalog_entries() if r["id"] == "agno.searxng")
    assert row["state"] == "needs_configuration"
    assert row["review"] == "reviewed" and all(risk == "read" for risk in row["action_risks"].values())
    with pytest.raises(CapabilityError) as exc:
        resolve_capability("agno.searxng")
    assert exc.value.state == "needs_configuration"


@pytest.mark.parametrize("host", [
    "ftp://search.example.org",
    "https://user:pw@search.example.org",
    "https://search.example.org/?q=1",
    "https://search.example.org/#x",
    "https://search.example.org/a/../b",
    "http://169.254.169.254",
    "http://[fe80::1]:8080",
    "http://metadata.google.internal",
    "search.example.org",
])
def test_searxng_settings_reject_unsafe_hosts(host):
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"agno.searxng": {"host": host}}})


def test_searxng_settings_reject_bad_engine_and_unknown_keys():
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"agno.searxng": {"host": "https://s.example.org", "engines": ["a&b=c"]}}})
    with pytest.raises(CapabilitySettingsError):
        parse_settings({"capabilities": {"agno.searxng": {"host": "https://s.example.org", "api_key": "x"}}})


def test_searxng_search_uses_only_configured_host_and_limits(tmp_path, searx):
    tools = _tools(tmp_path, "agno.searxng")
    assert set(tools) == {f"agno_searxng__{name}" for name in rx._SEARXNG_CATEGORIES}

    out = json.loads(tools["agno_searxng__news_search"].entrypoint(query="bauer", max_results=50, host="https://evil.example"))
    request = searx.requests[-1]
    assert (request.url.scheme, request.url.host, request.url.path) == ("https", "search.example.org", "/searx/search")
    assert request.url.params["format"] == "json"
    assert request.url.params["categories"] == "news"
    assert request.url.params["engines"] == "duckduckgo,wikipedia"
    assert len(out["results"]) == 4  # clamped to settings.max_results
    assert len(out["results"][0]["content"]) == 1_000

    tools["agno_searxng__search_web"].entrypoint(query="plain web")
    assert "categories" not in searx.requests[-1].url.params


def test_searxng_secret_query_never_leaves(tmp_path, searx):
    tools = _tools(tmp_path, "agno.searxng:search_web")
    with pytest.raises(ToolError, match="material secreto"):
        tools["agno_searxng__search_web"].entrypoint(query="sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert searx.requests == []


def test_searxng_private_network_requires_explicit_opt_in(tmp_path, monkeypatch, fake_dns):
    server = _Searx()
    monkeypatch.setattr(rx, "_SEARXNG_TRANSPORT", httpx.MockTransport(server))
    fake_dns["searx.lan"] = "192.168.1.20"
    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.searxng:
            host: http://searx.lan:8888
    """)
    search = _tools(tmp_path, "agno.searxng:search_web")["agno_searxng__search_web"]
    with pytest.raises(ToolError, match="SSRF"):
        search.entrypoint(query="x")
    assert server.requests == []

    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.searxng:
            host: http://searx.lan:8888
            allow_private_network: true
    """)
    search = _tools(tmp_path, "agno.searxng:search_web")["agno_searxng__search_web"]
    json.loads(search.entrypoint(query="x"))
    assert server.requests[-1].url.host == "searx.lan"


def test_searxng_metadata_is_refused_even_with_private_network(tmp_path, monkeypatch, fake_dns):
    server = _Searx()
    monkeypatch.setattr(rx, "_SEARXNG_TRANSPORT", httpx.MockTransport(server))
    fake_dns["rebind.example.org"] = "169.254.169.254"
    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.searxng:
            host: https://rebind.example.org
            allow_private_network: true
    """)
    search = _tools(tmp_path, "agno.searxng:search_web")["agno_searxng__search_web"]
    with pytest.raises(ToolError, match="metadata"):
        search.entrypoint(query="x")
    assert server.requests == []


@pytest.mark.parametrize("server, match", [
    (_Searx(status=302, headers={"location": "http://169.254.169.254/"}, content=b""), "redirect"),
    (_Searx(status=403, content=b"forbidden"), "HTTP 403"),
    (_Searx(content=b"<html>"), "não é JSON"),
    (_Searx(payload={"results": "nope"}), "malformada"),
    (_Searx(content=b"[" + b"1," * 1_200_000 + b"1]"), "excede"),
])
def test_searxng_bad_responses_become_tool_errors(tmp_path, monkeypatch, fake_dns, server, match):
    monkeypatch.setattr(rx, "_SEARXNG_TRANSPORT", httpx.MockTransport(server))
    _settings(tmp_path, monkeypatch, """
        capabilities:
          agno.searxng:
            host: https://search.example.org
    """)
    search = _tools(tmp_path, "agno.searxng:search_web")["agno_searxng__search_web"]
    with pytest.raises(ToolError, match=match):
        search.entrypoint(query="x")


def test_searxng_listing_never_imports_sdk_toolkit():
    import sys

    sys.modules.pop("agno.tools.searxng", None)
    catalog_entries()
    assert "agno.tools.searxng" not in sys.modules


# ── arXiv: ids e download confinado (sem disco) ──────────────────────────────


@pytest.mark.parametrize("value", [["2103.03404"], ["2103.03404v2"], ["hep-th/9901001"], ["math.AG/0601001v1"], "2103.03404, arXiv:2101.00001"])
def test_arxiv_ids_accept_real_formats(value):
    assert rx.validate_arxiv_ids(value)


@pytest.mark.parametrize("value", [[], ["../../etc/passwd"], ["2103.03404?x=1"], ["https://evil.example/x.pdf"],
                                   ["1", "2", "3", "4"], ["2103.034"], [None]])
def test_arxiv_ids_reject_everything_else(value):
    with pytest.raises(ToolError):
        rx.validate_arxiv_ids(value)


def test_arxiv_pdf_only_from_arxiv_hosts(monkeypatch):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/pdf/2103.03404":
            return httpx.Response(301, headers={"location": "https://export.arxiv.org/pdf/2103.03404v1"})
        if request.url.host == "export.arxiv.org":
            return httpx.Response(200, content=b"%PDF-1.4 fake")
        return httpx.Response(404)

    monkeypatch.setattr(rx, "_ARXIV_PDF_TRANSPORT", httpx.MockTransport(handler))
    assert rx.fetch_arxiv_pdf("2103.03404").startswith(b"%PDF")
    assert seen == ["https://arxiv.org/pdf/2103.03404", "https://export.arxiv.org/pdf/2103.03404v1"]


@pytest.mark.parametrize("response, match", [
    (httpx.Response(302, headers={"location": "https://evil.example/pdf"}), "não permitido"),
    (httpx.Response(302, headers={"location": "http://arxiv.org/pdf/x"}), "não permitido"),
    (httpx.Response(200, content=b"<html>login</html>"), "não é um PDF"),
    (httpx.Response(200, headers={"content-length": str(10**9)}, content=b"%PDF"), "limite"),
    (httpx.Response(503), "HTTP 503"),
])
def test_arxiv_pdf_guards(monkeypatch, response, match):
    monkeypatch.setattr(rx, "_ARXIV_PDF_TRANSPORT", httpx.MockTransport(lambda request: response))
    with pytest.raises(ToolError, match=match):
        rx.fetch_arxiv_pdf("2103.03404")


def test_arxiv_pdf_streamed_size_cap(monkeypatch):
    monkeypatch.setattr(rx, "ARXIV_MAX_PDF_BYTES", 1_000)
    monkeypatch.setattr(rx, "_ARXIV_PDF_TRANSPORT",
                        httpx.MockTransport(lambda request: httpx.Response(200, content=b"%PDF" + b"0" * 5_000)))
    with pytest.raises(ToolError, match="limite"):
        rx.fetch_arxiv_pdf("2103.03404")


def test_arxiv_read_is_reviewed_read_and_extract_actions_stay_out():
    from bauer.core.runtime.agno_capabilities.manifest import reviewed_policies

    policies = reviewed_policies()
    arxiv_actions = {a.name: a for a in policies["agno.arxiv"].actions}
    assert set(arxiv_actions) == {"search_arxiv_and_return_articles", "read_arxiv_papers"}
    assert all(a.risk == "read" and a.operation == "network.http" for a in arxiv_actions.values())
    assert policies["agno.wikipedia"].action("search_wikipedia_and_update_knowledge_base") is None
    assert policies["agno.tavily"].action("extract_url_content") is None
    assert policies["agno.exa"].action("get_contents") is None and policies["agno.exa"].action("research") is None
