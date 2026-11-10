"""Sprint 73 — pesquisa somente leitura instalável (plano 067).

The optional extra ``agno-research`` installs the packages behind the
read-only research toolkits reviewed in sprints 51 and 58. Installing a
package never widens a permission: the reviewed action lists stay the
authority. This module adds the Bauer-side guards some of those SDK toolkits
need once their dependency is really present:

* ``agno.wikipedia`` — the ``wikipedia`` package hardcodes a plain-HTTP API
  URL; every call is forced to HTTPS before it reaches the network.
* ``agno.arxiv`` — search keeps the SDK implementation (clamped result
  count). ``read_arxiv_papers`` is reimplemented here: the SDK version writes
  PDFs into ``site-packages`` and relies on ``Result.download_pdf`` (removed
  in ``arxiv`` 4). Bauer validates the ids, downloads only from
  ``https://arxiv.org`` (redirects limited to arXiv hosts, size capped) and
  parses the PDF **in memory** — nothing is written to disk.
* ``agno.yfinance`` — the library keeps cookie/timezone caches; they are
  pinned under ``$BAUER_HOME/cache/yfinance`` instead of the user cache dir.
* ``agno.searxng`` — Bauer-native client for an operator-configured,
  self-hosted SearXNG instance with the ``url_safety`` SSRF guard (private
  network only when the operator opts in; metadata/link-local never).
* ``agno.tavily`` — its two search variants are mutually exclusive in one
  SDK instance (``enable_search_context``); each granted action gets its own
  restricted instance.
"""

from __future__ import annotations

import io
import ipaddress
import json
import re
import socket
import urllib.parse
from dataclasses import replace
from typing import Any, Callable

import httpx
from pydantic import Field, field_validator

from .....tools.base import ToolError
from ..bridge import ActionImpl, register_factory, sdk_toolkit_factory
from ..manifest import ActionPolicy, ToolkitPolicy, register_policy
from ..settings import StrictSettings, register_settings_model

RESEARCH_EXTRA_HINT = "uv pip install 'bauer-agent[agno-research]'"


# ── Wikipedia: HTTPS only ───────────────────────────────────────────────────


def _force_https_api() -> None:
    import importlib

    module = importlib.import_module("wikipedia.wikipedia")
    current = str(getattr(module, "API_URL", ""))
    if current.startswith("http://"):
        setattr(module, "API_URL", "https://" + current[len("http://"):])
    if not str(getattr(module, "API_URL", "")).startswith("https://"):
        raise ToolError("wikipedia: API sem HTTPS recusada")


def _wikipedia_factory(resolved: Any, section: Any, context: Any = None) -> dict[str, ActionImpl]:
    _force_https_api()
    impls = sdk_toolkit_factory(resolved, section, context)
    for impl in impls.values():
        impl.fn = _before_each_call(_force_https_api, impl.fn)
        impl.timeout_seconds = 30
    return impls


def _before_each_call(check: Callable[[], None], fn: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(**kwargs: Any) -> Any:
        # ``wikipedia.set_lang`` (any caller in-process) rewrites API_URL with
        # http://; re-check right before the call reaches the library.
        check()
        return fn(**kwargs)

    return guarded


register_factory("agno.wikipedia", _wikipedia_factory)


# ── arXiv: clamped search + in-memory paper reading ─────────────────────────

_ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z][a-z\-]{1,20}(?:\.[A-Z]{2})?/\d{7})(?:v\d{1,3})?$")
_ARXIV_PDF_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})
ARXIV_MAX_SEARCH_RESULTS = 25
ARXIV_MAX_PAPERS = 3
ARXIV_MAX_PAGES = 40
ARXIV_MAX_PDF_BYTES = 30_000_000
ARXIV_TIMEOUT_SECONDS = 30.0
_ARXIV_MAX_REDIRECTS = 3

#: Test seams: an ``httpx`` transport for the PDF download and a factory for
#: the ``arxiv.Client`` used by ``read_arxiv_papers``. ``None`` = real.
_ARXIV_PDF_TRANSPORT: Any = None
_ARXIV_CLIENT_FACTORY: Callable[[], Any] | None = None


def validate_arxiv_ids(id_list: Any) -> list[str]:
    if isinstance(id_list, str):
        id_list = [part.strip() for part in id_list.split(",")]
    if not isinstance(id_list, (list, tuple)) or not id_list:
        raise ToolError("id_list deve ser uma lista não vazia de ids do arXiv")
    ids: list[str] = []
    for item in id_list:
        text = str(item or "").strip()
        if text.lower().startswith("arxiv:"):
            text = text[len("arxiv:"):]
        if not _ARXIV_ID.match(text):
            raise ToolError("id do arXiv inválido (use o formato 2103.03404 ou 2103.03404v1)")
        if text not in ids:
            ids.append(text)
    if len(ids) > ARXIV_MAX_PAPERS:
        raise ToolError(f"no máximo {ARXIV_MAX_PAPERS} artigos por chamada")
    return ids


def _arxiv_client() -> Any:
    if _ARXIV_CLIENT_FACTORY is not None:
        return _ARXIV_CLIENT_FACTORY()
    import arxiv

    return arxiv.Client(num_retries=1)


def _arxiv_location_allowed(url: httpx.URL) -> bool:
    return url.scheme == "https" and (url.host or "").lower() in _ARXIV_PDF_HOSTS


def fetch_arxiv_pdf(paper_id: str) -> bytes:
    """Download one PDF from arxiv.org into memory (bounded, arXiv hosts only)."""
    if not _ARXIV_ID.match(paper_id):
        raise ToolError("id do arXiv inválido")
    from .....http_shared import shared_ssl_context

    url = httpx.URL(f"https://arxiv.org/pdf/{paper_id}")
    with httpx.Client(
        transport=_ARXIV_PDF_TRANSPORT,
        timeout=ARXIV_TIMEOUT_SECONDS,
        follow_redirects=False,
        verify=shared_ssl_context(),
        headers={"User-Agent": "bauer-agent (arxiv reader)"},
    ) as client:
        for _hop in range(_ARXIV_MAX_REDIRECTS + 1):
            with client.stream("GET", url) as response:
                if response.is_redirect:
                    target = url.join(response.headers.get("location", ""))
                    if not _arxiv_location_allowed(target):
                        raise ToolError("redirect do arXiv para host não permitido recusado")
                    url = target
                    continue
                if response.status_code != 200:
                    raise ToolError(f"arXiv respondeu HTTP {response.status_code} ao baixar o PDF")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > ARXIV_MAX_PDF_BYTES:
                    raise ToolError("PDF do arXiv excede o limite de tamanho")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > ARXIV_MAX_PDF_BYTES:
                        raise ToolError("PDF do arXiv excede o limite de tamanho")
                    chunks.append(chunk)
                data = b"".join(chunks)
                if not data.startswith(b"%PDF"):
                    raise ToolError("resposta do arXiv não é um PDF")
                return data
    raise ToolError("redirects demais ao baixar o PDF do arXiv")


def _pdf_pages(data: bytes, pages_to_read: int) -> list[dict[str, Any]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[dict[str, Any]] = []
    for number, page in enumerate(reader.pages, start=1):
        if number > pages_to_read:
            break
        pages.append({"page": number, "text": page.extract_text() or ""})
    return pages


def read_arxiv_papers(id_list: list[str], pages_to_read: int | None = None) -> str:
    """Lê artigos do arXiv por id (metadados + texto do PDF, sem gravar em disco).

    Args:
        id_list: ids do arXiv, ex.: ["2103.03404v1"] (no máximo 3).
        pages_to_read: quantas páginas ler de cada PDF (padrão e teto: 40).
    """
    ids = validate_arxiv_ids(id_list)
    try:
        pages = ARXIV_MAX_PAGES if pages_to_read is None else int(pages_to_read)
    except (TypeError, ValueError):
        raise ToolError("pages_to_read deve ser inteiro") from None
    pages = max(1, min(pages, ARXIV_MAX_PAGES))
    import arxiv

    client = _arxiv_client()
    articles: list[dict[str, Any]] = []
    for result in client.results(arxiv.Search(id_list=ids, max_results=len(ids))):
        short_id = str(result.get_short_id())
        if not _ARXIV_ID.match(short_id):
            raise ToolError("arXiv devolveu id inesperado")
        articles.append({
            "title": result.title,
            "id": short_id,
            "authors": [author.name for author in result.authors],
            "primary_category": result.primary_category,
            "published": result.published.isoformat() if result.published else None,
            "summary": result.summary,
            "content": _pdf_pages(fetch_arxiv_pdf(short_id), pages),
        })
    return json.dumps(articles, ensure_ascii=False)


def _clamp_kwarg(fn: Callable[..., Any], name: str, default: int, ceiling: int) -> Callable[..., Any]:
    def clamped(**kwargs: Any) -> Any:
        try:
            value = int(kwargs.get(name, default))
        except (TypeError, ValueError):
            raise ToolError(f"{name} deve ser inteiro") from None
        kwargs[name] = max(1, min(value, ceiling))
        return fn(**kwargs)

    return clamped


_READ_PAPERS_PARAMETERS = {
    "type": "object",
    "properties": {
        "id_list": {"type": "array", "items": {"type": "string"},
                    "description": f"Ids do arXiv (ex.: 2103.03404v1), no máximo {ARXIV_MAX_PAPERS}."},
        "pages_to_read": {"type": "integer", "description": f"Páginas por PDF (teto {ARXIV_MAX_PAGES})."},
    },
    "required": ["id_list"],
}


def _arxiv_factory(resolved: Any, section: Any, context: Any = None) -> dict[str, ActionImpl]:
    impls: dict[str, ActionImpl] = {}
    sdk_actions = tuple(action for action in resolved.actions if action.name != "read_arxiv_papers")
    if sdk_actions:
        impls.update(sdk_toolkit_factory(replace(resolved, actions=sdk_actions), section, context))
        search = impls.get("search_arxiv_and_return_articles")
        if search is not None:
            search.fn = _clamp_kwarg(search.fn, "num_articles", 10, ARXIV_MAX_SEARCH_RESULTS)
    if any(action.name == "read_arxiv_papers" for action in resolved.actions):
        impls["read_arxiv_papers"] = ActionImpl(
            fn=read_arxiv_papers,
            description=(read_arxiv_papers.__doc__ or "").strip().split("\n\n")[0],
            parameters=_READ_PAPERS_PARAMETERS,
            timeout_seconds=int(ARXIV_TIMEOUT_SECONDS) * (ARXIV_MAX_PAPERS + 1),
        )
    return impls


register_factory("agno.arxiv", _arxiv_factory)


# ── Yahoo Finance: caches under $BAUER_HOME ──────────────────────────────────


def _yfinance_factory(resolved: Any, section: Any, context: Any = None) -> dict[str, ActionImpl]:
    import yfinance

    from .....paths import get_bauer_home

    cache_dir = get_bauer_home() / "cache" / "yfinance"
    cache_dir.mkdir(parents=True, exist_ok=True)
    yfinance.set_tz_cache_location(str(cache_dir))
    return sdk_toolkit_factory(resolved, section, context)


register_factory("agno.yfinance", _yfinance_factory)


# ── Tavily: one restricted SDK instance per search variant ───────────────────


def _tavily_factory(resolved: Any, section: Any, context: Any = None) -> dict[str, ActionImpl]:
    impls: dict[str, ActionImpl] = {}
    for action in resolved.actions:
        # ``web_search_with_tavily`` exists only with enable_search_context=True,
        # which hides ``web_search_using_tavily`` in the same instance.
        context_mode = action.name == "web_search_with_tavily"
        impls.update(sdk_toolkit_factory(
            replace(resolved, actions=(action,)), section, context,
            init_kwargs={"enable_search_context": context_mode, "enable_extract": False},
        ))
    return impls


register_factory("agno.tavily", _tavily_factory)


# ── SearXNG: operator-configured host + SSRF guard ───────────────────────────

_ENGINE = re.compile(r"^[a-z0-9_ .\-]{1,40}$")
_BASE_PATH = re.compile(r"^(?:/[A-Za-z0-9._~\-]+)*/?$")
#: Never a SearXNG instance, whatever the operator allows: cloud metadata,
#: link-local and unspecified addresses.
_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fd00:ec2::254/128"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
)
_ALWAYS_BLOCKED_HOSTS = frozenset({"metadata.google.internal", "metadata.goog"})
_SEARXNG_CATEGORIES = {
    "search_web": None,
    "news_search": "news",
    "science_search": "science",
    "it_search": "it",
    "image_search": "images",
    "video_search": "videos",
    "map_search": "map",
    "music_search": "music",
}
_MAX_QUERY_CHARS = 500

#: Test seam: an ``httpx`` transport (e.g. ``MockTransport``). ``None`` = real.
_SEARXNG_TRANSPORT: Any = None


class SearxngSettings(StrictSettings):
    #: Base URL of the operator's own SearXNG (``http(s)://host[:port][/path]``).
    host: str
    #: Self-hosted instances often live on localhost/LAN. Off by default: the
    #: ``url_safety`` private-range block applies unless the operator opts in.
    allow_private_network: bool = False
    engines: list[str] = Field(default_factory=list)
    max_results: int = Field(default=10, ge=1, le=50)
    timeout_seconds: float = Field(default=15.0, gt=0, le=60)
    max_response_bytes: int = Field(default=2_000_000, ge=10_000, le=20_000_000)

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        text = str(value or "").strip()
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("host deve ser uma URL http(s) com nome de host")
        if parsed.username or parsed.password:
            raise ValueError("host não pode conter credenciais")
        if parsed.query or parsed.fragment:
            raise ValueError("host não pode conter query ou fragmento")
        if not _BASE_PATH.match(parsed.path or "/") or ".." in parsed.path:
            raise ValueError("caminho base inválido")
        if parsed.hostname.lower() in _ALWAYS_BLOCKED_HOSTS or _always_blocked_ip(parsed.hostname):
            raise ValueError("host aponta para endpoint de metadata/link-local")
        return text.rstrip("/")

    @field_validator("engines")
    @classmethod
    def _engines(cls, value: list[str]) -> list[str]:
        cleaned = [str(item).strip().lower() for item in value]
        if any(not _ENGINE.match(item) for item in cleaned):
            raise ValueError("nome de engine inválido")
        return sorted(set(cleaned))


def _always_blocked_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return any(address in network for network in _ALWAYS_BLOCKED_NETWORKS)


def ensure_searxng_target(settings: SearxngSettings, url: str) -> None:
    """SSRF guard run before every request (DNS re-resolved each call)."""
    from .....url_safety import UrlSafetyConfig, UrlSafetyError, check_url

    try:
        check_url(url, config=UrlSafetyConfig(block_private_ips=not settings.allow_private_network))
    except UrlSafetyError:
        raise ToolError("host do SearXNG recusado pela proteção SSRF (rede privada exige allow_private_network)") from None
    hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    if hostname in _ALWAYS_BLOCKED_HOSTS or _always_blocked_ip(hostname):
        raise ToolError("host do SearXNG aponta para metadata/link-local")
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError):
        return  # httpx fails the same lookup; nothing reachable to block
    for _family, _type, _proto, _canon, sockaddr in resolved:
        if _always_blocked_ip(str(sockaddr[0])):
            raise ToolError("host do SearXNG resolve para metadata/link-local")


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    return text[:limit]


def searxng_search(settings: SearxngSettings, query: Any, category: str | None, max_results: Any) -> str:
    text = str(query or "").strip()
    if not text:
        raise ToolError("query é obrigatória")
    if len(text) > _MAX_QUERY_CHARS:
        raise ToolError(f"query excede {_MAX_QUERY_CHARS} caracteres")
    try:
        limit = max(1, min(int(max_results), settings.max_results))
    except (TypeError, ValueError):
        raise ToolError("max_results deve ser inteiro") from None
    params: dict[str, str] = {"q": text, "format": "json"}
    if category:
        params["categories"] = category
    if settings.engines:
        params["engines"] = ",".join(settings.engines)
    url = f"{settings.host}/search"
    ensure_searxng_target(settings, url)
    from .....http_shared import shared_ssl_context

    with httpx.Client(
        transport=_SEARXNG_TRANSPORT,
        timeout=settings.timeout_seconds,
        follow_redirects=False,
        verify=shared_ssl_context(),
        headers={"Accept": "application/json", "User-Agent": "bauer-agent"},
    ) as client:
        with client.stream("GET", url, params=params) as response:
            if response.is_redirect:
                raise ToolError("SearXNG respondeu com redirect; configure a URL final da instância")
            if response.status_code != 200:
                raise ToolError(
                    f"SearXNG respondeu HTTP {response.status_code} (o formato json precisa estar habilitado na instância)"
                )
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > settings.max_response_bytes:
                    raise ToolError("resposta do SearXNG excede o limite configurado")
    try:
        payload = json.loads(bytes(body))
    except ValueError:
        raise ToolError("resposta do SearXNG não é JSON") from None
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        raise ToolError("resposta do SearXNG malformada")
    results = [
        {
            "title": _clip(item.get("title"), 300),
            "url": _clip(item.get("url"), 2_000),
            "content": _clip(item.get("content"), 1_000),
            "engine": _clip(item.get("engine"), 60),
            "published": _clip(item.get("publishedDate"), 40),
        }
        for item in raw_results[:limit]
        if isinstance(item, dict)
    ]
    suggestions = payload.get("suggestions") if isinstance(payload.get("suggestions"), list) else []
    return json.dumps(
        {"query": text, "results": results, "suggestions": [_clip(s, 200) for s in suggestions[:10]]},
        ensure_ascii=False,
    )


def _searxng_parameters(category: str | None) -> dict[str, Any]:
    label = f"categoria '{category}'" if category else "web"
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": f"Termos de busca ({label})."},
            "max_results": {"type": "integer", "description": "Máximo de resultados (limitado pela configuração)."},
        },
        "required": ["query"],
    }


def _searxng_action(settings: SearxngSettings, category: str | None) -> Callable[..., str]:
    def call(**kwargs: Any) -> str:
        # Only the two public arguments are read: host, category and limits
        # come from the operator settings and can never be set by the model.
        return searxng_search(settings, kwargs.get("query", ""), category, kwargs.get("max_results", 5))

    return call


def _searxng_factory(resolved: Any, section: Any, context: Any = None) -> dict[str, ActionImpl]:
    if not isinstance(section, SearxngSettings):
        raise ToolError("agno.searxng sem configuração")
    impls: dict[str, ActionImpl] = {}
    for action in resolved.actions:
        category = _SEARXNG_CATEGORIES[action.name]
        impls[action.name] = ActionImpl(
            fn=_searxng_action(section, category),
            description=f"Busca no SearXNG configurado pelo operador ({category or 'web'}); somente leitura.",
            parameters=_searxng_parameters(category),
            timeout_seconds=int(section.timeout_seconds) + 5,
        )
    return impls


register_settings_model("agno.searxng", SearxngSettings)
register_policy(
    ToolkitPolicy(
        id="agno.searxng",
        title="SearXNG (auto-hospedado)",
        category="Pesquisa e conteúdo web",
        description="Metabusca numa instância SearXNG do operador; somente leitura de resultados.",
        actions=tuple(ActionPolicy(name, "read", "network.http") for name in _SEARXNG_CATEGORIES),
        module="agno.tools.searxng",
        class_name="Searxng",
        settings_section="agno.searxng",
        notes=(
            "Cliente Bauer (o toolkit do SDK não é instanciado): host fixo do operador, url_safety a cada "
            "chamada, rede privada só com allow_private_network, metadata/link-local sempre negados, sem redirect."
        ),
    )
)
register_factory("agno.searxng", _searxng_factory)
