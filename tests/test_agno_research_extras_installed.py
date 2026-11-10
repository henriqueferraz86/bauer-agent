"""Plano 067 / sprint 73 — toolkits de pesquisa com as bibliotecas REAIS.

Só roda com o extra ``agno-research`` instalado
(``uv sync --frozen --extra dev --extra agno-research``). Fora dele, pula;
com ``BAUER_REQUIRE_AGNO_EXTRAS=1`` (job ``install-check`` do CI, que instala
todos os extras) a ausência vira falha, para o teste nunca pular calado.

Herméticos: conexões de socket são proibidas; ``requests`` responde por um
adapter falso, ``httpx`` por ``MockTransport`` e o oEmbed do YouTube por um
``urlopen`` falso.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
from pathlib import Path

import httpx
import pytest

_MODULES = ("agno", "wikipedia", "arxiv", "pypdf", "yfinance", "youtube_transcript_api", "tavily", "exa_py", "serpapi", "linkup")
_MISSING = [name for name in _MODULES if importlib.util.find_spec(name) is None]
if _MISSING:
    if os.environ.get("BAUER_REQUIRE_AGNO_EXTRAS") == "1":
        raise RuntimeError(f"extra agno-research incompleto: {', '.join(_MISSING)}")
    pytest.skip(f"extra agno-research não instalado ({', '.join(_MISSING)})", allow_module_level=True)

import requests  # noqa: E402

from bauer.core.runtime.agno_capabilities import catalog  # noqa: E402
from bauer.core.runtime.agno_capabilities.catalog import CapabilityError, catalog_entries, resolve_capability  # noqa: E402
from bauer.core.runtime.agno_capabilities.families import research_extras as rx  # noqa: E402
from bauer.core.runtime.agno_capabilities.manifest import reviewed_policies  # noqa: E402

NO_KEY = ("agno.wikipedia", "agno.arxiv", "agno.yfinance", "agno.youtube")
KEYED = {"agno.tavily": "TAVILY_API_KEY", "agno.exa": "EXA_API_KEY", "agno.serpapi": "SERP_API_KEY", "agno.linkup": "LINKUP_API_KEY"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch, tmp_path):
    """Any real socket connection fails the test; caches go to tmp."""

    def refuse(*args, **kwargs):
        raise AssertionError("conexão de rede real em teste hermético")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("BAUER_AGNO_CAPABILITIES", str(tmp_path / "no-settings.yaml"))
    for name in KEYED.values():
        monkeypatch.delenv(name, raising=False)


class FakeRequests:
    """Fake transport for every ``requests`` session (HTTPAdapter.send)."""

    def __init__(self, monkeypatch):
        self.urls: list[str] = []
        self.routes: dict[str, tuple[int, bytes, str]] = {}
        fake = self

        def send(adapter, request, **kwargs):
            fake.urls.append(request.url)
            host = requests.utils.urlparse(request.url).hostname or ""
            status, body, ctype = fake.routes.get(host, (404, b"not found", "text/plain"))
            response = requests.Response()
            response.status_code = status
            response._content = body
            response.headers["Content-Type"] = ctype
            response.url = request.url
            response.request = request
            return response

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)


@pytest.fixture()
def fake_requests(monkeypatch):
    return FakeRequests(monkeypatch)


def _tools(tmp_path: Path, ref: str) -> dict:
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    adapter = AgnoRuntimeAdapter(adapter_config={"workspace": str(workspace)}, runtime_policy_root=tmp_path / "rt")
    return {fn.name: fn for fn in adapter._map_tools([ref])}


# ── estados com o extra instalado ────────────────────────────────────────────


def test_no_key_toolkits_are_ready_and_keyed_need_configuration():
    rows = {row["id"]: row for row in catalog_entries()}
    for capability_id in NO_KEY:
        assert rows[capability_id]["state"] == "ready", (capability_id, rows[capability_id]["reason"])
    for capability_id, credential in KEYED.items():
        assert rows[capability_id]["state"] == "needs_configuration"
        assert credential in rows[capability_id]["missing"]


@pytest.mark.parametrize("capability_id", sorted((*NO_KEY, *KEYED)))
def test_reviewed_actions_exist_in_real_sdk_and_materialize_offline(capability_id, tmp_path, monkeypatch):
    """Every reviewed action exists in the real toolkit; building it touches no network."""
    if capability_id in KEYED:
        monkeypatch.setenv(KEYED[capability_id], "fake-key-for-offline-construction")
    policy = reviewed_policies()[capability_id]
    tools = _tools(tmp_path, capability_id)
    expected = {f"{capability_id.replace('.', '_')}__{action.name}" for action in policy.default_actions}
    assert set(tools) == expected


def test_unreviewed_real_sdk_actions_are_not_grantable(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("EXA_API_KEY", "k")
    for ref in ("agno.tavily:extract_url_content", "agno.exa:get_contents", "agno.exa:research",
                "agno.wikipedia:search_wikipedia_and_update_knowledge_base"):
        with pytest.raises(CapabilityError) as exc:
            resolve_capability(ref)
        assert exc.value.state == "blocked_by_policy"


# ── Wikipedia: HTTPS forçado ────────────────────────────────────────────────


def test_wikipedia_calls_go_over_https(tmp_path, fake_requests):
    import wikipedia.wikipedia as wk

    wk.set_lang("pt")  # library default rewrites API_URL with http://
    assert wk.API_URL.startswith("http://")
    fake_requests.routes["pt.wikipedia.org"] = (200, json.dumps({
        "query": {"search": [{"title": "Recife"}], "pages": {"1": {"pageid": 1, "title": "Recife", "extract": "Cidade."}}},
    }).encode(), "application/json")
    search = _tools(tmp_path, "agno.wikipedia")["agno_wikipedia__search_wikipedia"]
    search.entrypoint(query="Recife")
    assert fake_requests.urls and all(url.startswith("https://pt.wikipedia.org/") for url in fake_requests.urls)
    wk.set_lang("en")


# ── arXiv: busca limitada e leitura em memória ───────────────────────────────

_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2103.03404v1</id>
    <updated>2021-03-05T00:00:00Z</updated>
    <published>2021-03-05T00:00:00Z</published>
    <title>Attention is not all you need</title>
    <summary>Pure attention loses rank.</summary>
    <author><name>Yihe Dong</name></author>
    <link href="http://arxiv.org/abs/2103.03404v1" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://evil.example/2103.03404v1.pdf" rel="related" type="application/pdf"/>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
  </entry>
</feed>"""


def _tiny_pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_arxiv_search_is_clamped(tmp_path, fake_requests, monkeypatch):
    import arxiv

    requested: list[int] = []
    original_init = arxiv.Search.__init__

    def spy(self, *args, **kwargs):
        requested.append(kwargs.get("max_results"))
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(arxiv.Search, "__init__", spy)
    fake_requests.routes["export.arxiv.org"] = (200, _FEED, "application/atom+xml")
    search = _tools(tmp_path, "agno.arxiv:search_arxiv_and_return_articles")["agno_arxiv__search_arxiv_and_return_articles"]
    articles = json.loads(search.entrypoint(query="attention", num_articles=10_000))
    assert articles[0]["id"] == "2103.03404v1"
    assert requested == [rx.ARXIV_MAX_SEARCH_RESULTS]
    assert fake_requests.urls and all(url.startswith("https://export.arxiv.org/api/query") for url in fake_requests.urls)


def test_arxiv_read_papers_in_memory_from_arxiv_only(tmp_path, fake_requests, monkeypatch):
    import arxiv
    import agno.tools.arxiv as sdk_arxiv

    fake_requests.routes["export.arxiv.org"] = (200, _FEED, "application/atom+xml")
    monkeypatch.setattr(rx, "_ARXIV_CLIENT_FACTORY", lambda: arxiv.Client(delay_seconds=0, num_retries=0))
    pdf_hosts: list[str] = []

    def pdf(request: httpx.Request) -> httpx.Response:
        pdf_hosts.append(str(request.url))
        return httpx.Response(200, content=_tiny_pdf(5))

    monkeypatch.setattr(rx, "_ARXIV_PDF_TRANSPORT", httpx.MockTransport(pdf))
    workspace_before = set((tmp_path / "workspace").rglob("*")) if (tmp_path / "workspace").exists() else set()
    sdk_download_dir = Path(sdk_arxiv.__file__).parent / "arxiv_pdfs"
    existed = sdk_download_dir.exists()

    read = _tools(tmp_path, "agno.arxiv:read_arxiv_papers")["agno_arxiv__read_arxiv_papers"]
    articles = json.loads(read.entrypoint(id_list=["2103.03404v1"], pages_to_read=2))

    assert articles[0]["title"] == "Attention is not all you need"
    assert [page["page"] for page in articles[0]["content"]] == [1, 2]
    # the feed's pdf link (evil.example) is ignored: Bauer builds the arXiv URL itself
    assert pdf_hosts == ["https://arxiv.org/pdf/2103.03404v1"]
    assert set((tmp_path / "workspace").rglob("*")) == workspace_before  # nothing written
    assert sdk_download_dir.exists() == existed  # SDK download dir never created


# ── YouTube: argumento URL nunca vira destino de rede ────────────────────────


def test_youtube_url_argument_only_selects_video_id(tmp_path, monkeypatch):
    import agno.tools.youtube as sdk_youtube

    opened: list[str] = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"title": "Demo", "author_name": "Bauer"}).encode()

    def fake_urlopen(url, timeout=None):
        opened.append(url)
        return _Resp()

    monkeypatch.setattr(sdk_youtube, "urlopen", fake_urlopen)
    data = _tools(tmp_path, "agno.youtube:get_youtube_video_data")["agno_youtube__get_youtube_video_data"]
    assert json.loads(data.entrypoint(url="https://www.youtube.com/watch?v=abc123"))["title"] == "Demo"
    data.entrypoint(url="http://169.254.169.254/latest/meta-data/?v=x")
    assert opened and all(url.startswith("https://www.youtube.com/oembed?") for url in opened)


# ── Yahoo Finance: caches sob $BAUER_HOME ────────────────────────────────────


def test_yfinance_cache_pinned_under_bauer_home(tmp_path, monkeypatch):
    import yfinance

    seen: list[str] = []
    monkeypatch.setattr(yfinance, "set_tz_cache_location", lambda path: seen.append(path))

    class FakeTicker:
        def __init__(self, symbol, *args, **kwargs):
            self.info = {"regularMarketPrice": 10.5, "currentPrice": 10.5}
            self.fast_info = {"lastPrice": 10.5}

    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    monkeypatch.setattr("agno.tools.yfinance.yf.Ticker", FakeTicker, raising=False)
    price = _tools(tmp_path, "agno.yfinance:get_current_stock_price")["agno_yfinance__get_current_stock_price"]
    assert "10.5" in price.entrypoint(symbol="ACME")
    assert seen == [str(tmp_path / "home" / "cache" / "yfinance")]


# ── Tavily: só busca, chave injetada, host fixo ──────────────────────────────


def test_tavily_both_search_variants_without_extract(tmp_path, fake_requests, monkeypatch):
    monkeypatch.setattr(catalog, "credential_available", lambda name: True)
    monkeypatch.setattr("bauer.core.runtime.agno_capabilities.bridge.resolve_credential", lambda name: "tvly-vault-key")
    fake_requests.routes["api.tavily.com"] = (200, json.dumps({
        "query": "q", "results": [{"title": "T", "url": "https://e.org", "content": "c", "score": 0.9}],
    }).encode(), "application/json")
    tools = _tools(tmp_path, "agno.tavily")
    assert set(tools) == {"agno_tavily__web_search_using_tavily", "agno_tavily__web_search_with_tavily"}
    assert "https://e.org" in tools["agno_tavily__web_search_using_tavily"].entrypoint(query="bauer")
    assert fake_requests.urls and all(url.startswith("https://api.tavily.com/") for url in fake_requests.urls)
