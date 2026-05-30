"""Dispatcher de backends web — seleciona search e extract via config.

Arquitetura inspirada no Hermes Agent (NousResearch/hermes-agent):
  - search_backend e extract_backend são independentes
  - Cada backend é detectado via import dinâmico com fallback claro
  - Sem dependências obrigatórias além de httpx (já na stack)

Backends de busca:
  ddgs     — DuckDuckGo (padrão, MIT, sem API key)
  searxng  — SearXNG self-hosted (AGPL-3, sem limite de taxa)
  brave    — Brave Search API (requer BRAVE_API_KEY)

Backends de extração:
  httpx    — httpx + BeautifulSoup (padrão, leve, zero config)
  crawl4ai — crawl4ai (MIT, Markdown limpo para LLMs, requer instalação)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bauer.config_loader import WebSection


class WebError(Exception):
    """Erro de operação web com mensagem legível."""


# ---------------------------------------------------------------------------
# Resultado padronizado de busca
# ---------------------------------------------------------------------------

class SearchResult:
    """Resultado único de busca."""

    def __init__(self, title: str, url: str, snippet: str, engine: str = ""):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.engine = engine

    def __str__(self) -> str:
        return f"{self.title}\n   {self.url}\n   {self.snippet}"


# ---------------------------------------------------------------------------
# Dispatcher principal
# ---------------------------------------------------------------------------

class WebDispatcher:
    """Seleciona e delega para backends de search e extract configurados.

    Uso:
        dispatcher = WebDispatcher(cfg.web)
        results = dispatcher.search("query", max_results=5)
        content = dispatcher.extract("https://...", max_chars=5000)
    """

    def __init__(self, web_config: "WebSection | None" = None):
        self._cfg = web_config

    @property
    def search_backend(self) -> str:
        return (self._cfg.search_backend if self._cfg else None) or "ddgs"

    @property
    def extract_backend(self) -> str:
        return (self._cfg.extract_backend if self._cfg else None) or "httpx"

    @property
    def max_results(self) -> int:
        return self._cfg.max_results if self._cfg else 5

    @property
    def max_chars(self) -> int:
        return self._cfg.max_chars if self._cfg else 5000

    @property
    def timeout(self) -> int:
        return self._cfg.timeout_seconds if self._cfg else 15

    # --- Search ---------------------------------------------------------------

    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        """Executa busca usando o backend configurado.

        Retorna lista de SearchResult com title, url, snippet.
        Levanta WebError em caso de falha ou backend não disponível.
        """
        n = max_results or self.max_results
        backend = self.search_backend.lower()

        if backend == "ddgs":
            return self._search_ddgs(query, n)
        elif backend == "searxng":
            return self._search_searxng(query, n)
        elif backend == "brave":
            return self._search_brave(query, n)
        else:
            raise WebError(
                f"Backend de busca '{backend}' desconhecido. "
                "Opções: ddgs, searxng, brave"
            )

    def search_as_text(self, query: str, max_results: int | None = None) -> str:
        """Executa busca e retorna resultado formatado como string para o agent."""
        results = self.search(query, max_results)
        if not results:
            return f"Nenhum resultado encontrado para '{query}'."
        lines = []
        for i, r in enumerate(results, 1):
            snippet = r.snippet[:300] if r.snippet else ""
            lines.append(f"{i}. {r.title}\n   {r.url}\n   {snippet}")
        header = f"[{self.search_backend}] {len(results)} resultado(s) para '{query}':\n"
        return header + "\n\n".join(lines)

    # --- Extract --------------------------------------------------------------

    def extract(self, url: str, max_chars: int | None = None) -> str:
        """Extrai conteúdo de URL usando o backend configurado.

        Retorna texto limpo (Markdown ou plain text).
        Levanta WebError em caso de falha.
        """
        n = max_chars or self.max_chars
        self._validate_url(url)
        backend = self.extract_backend.lower()

        if backend == "httpx":
            return self._extract_httpx(url, n)
        elif backend == "crawl4ai":
            return self._extract_crawl4ai(url, n)
        else:
            raise WebError(
                f"Backend de extração '{backend}' desconhecido. "
                "Opções: httpx, crawl4ai"
            )

    # --- Backends de busca ----------------------------------------------------

    def _search_ddgs(self, query: str, max_results: int) -> list[SearchResult]:
        """DuckDuckGo via biblioteca ddgs (MIT, sem API key)."""
        try:
            from ddgs import DDGS
        except ImportError:
            raise WebError(
                "Backend 'ddgs' não instalado.\n"
                "Instale com: pip install ddgs\n"
                "Ou configure outro backend em config.yaml: web.search_backend: searxng"
            )

        try:
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(SearchResult(
                        title=r.get("title", "").strip(),
                        url=r.get("href", "").strip(),
                        snippet=r.get("body", "").strip(),
                        engine="ddgs",
                    ))
            return results
        except Exception as exc:
            raise WebError(f"Erro na busca DuckDuckGo: {exc}") from exc

    def _search_searxng(self, query: str, max_results: int) -> list[SearchResult]:
        """SearXNG self-hosted via REST API (AGPL-3, sem limite de taxa)."""
        import httpx

        base_url = (self._cfg.searxng_url if self._cfg else "http://localhost:8080").rstrip("/")

        try:
            resp = httpx.get(
                f"{base_url}/search",
                params={
                    "q": query,
                    "format": "json",
                    "engines": "google,bing,duckduckgo",
                    "safesearch": "1",
                },
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            raise WebError(
                f"Não foi possível conectar ao SearXNG em {base_url}.\n"
                "Certifique-se de que o SearXNG está rodando:\n"
                "  docker run -p 8080:8080 searxng/searxng\n"
                "Ou configure outro backend: web.search_backend: ddgs"
            )
        except Exception as exc:
            raise WebError(f"Erro na busca SearXNG: {exc}") from exc

        results = []
        for r in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=r.get("title", "").strip(),
                url=r.get("url", "").strip(),
                snippet=r.get("content", "").strip(),
                engine=r.get("engine", "searxng"),
            ))
        return results

    def _search_brave(self, query: str, max_results: int) -> list[SearchResult]:
        """Brave Search API (requer BRAVE_API_KEY ou web.brave_api_key)."""
        import httpx

        api_key = (
            (self._cfg.brave_api_key if self._cfg else "")
            or os.environ.get("BRAVE_API_KEY", "")
        )
        if not api_key:
            raise WebError(
                "Backend 'brave' requer BRAVE_API_KEY.\n"
                "Adicione no .env: BRAVE_API_KEY=sua-chave\n"
                "Obtenha em: https://api.search.brave.com/\n"
                "Ou use outro backend: web.search_backend: ddgs"
            )

        try:
            resp = httpx.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results, "safesearch": "moderate"},
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": api_key,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise WebError(f"Erro Brave API (HTTP {exc.response.status_code}): {exc}") from exc
        except Exception as exc:
            raise WebError(f"Erro na busca Brave: {exc}") from exc

        results = []
        for r in data.get("web", {}).get("results", [])[:max_results]:
            results.append(SearchResult(
                title=r.get("title", "").strip(),
                url=r.get("url", "").strip(),
                snippet=r.get("description", "").strip(),
                engine="brave",
            ))
        return results

    # --- Backends de extração -------------------------------------------------

    def _extract_httpx(self, url: str, max_chars: int) -> str:
        """httpx + BeautifulSoup (padrão, leve, zero config)."""
        import httpx

        try:
            resp = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; BauerAgent/1.0)"},
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise WebError(f"Timeout ao acessar {url}")
        except httpx.HTTPStatusError as exc:
            raise WebError(f"HTTP {exc.response.status_code} ao acessar {url}")
        except Exception as exc:
            raise WebError(f"Erro ao acessar {url}: {exc}")

        content_type = resp.headers.get("content-type", "")
        if "text" not in content_type and "html" not in content_type:
            return f"[Conteúdo binário — content-type: {content_type}]"

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except ImportError:
            text = resp.text

        lines = [l.strip() for l in text.splitlines() if l.strip()]
        text = "\n".join(lines)

        if not text:
            return "Conteúdo vazio ou não extraído."

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncado — limite de {max_chars} chars]"

        return text

    def _extract_crawl4ai(self, url: str, max_chars: int) -> str:
        """crawl4ai — extração LLM-friendly em Markdown (MIT).

        Requer instalação: pip install crawl4ai
        Pós-instalação:    crawl4ai-setup  (baixa Playwright + Chromium)
        """
        try:
            from crawl4ai import AsyncWebCrawler
            import asyncio
        except ImportError:
            raise WebError(
                "Backend 'crawl4ai' não instalado.\n"
                "Instale com:\n"
                "  pip install crawl4ai\n"
                "  crawl4ai-setup\n"
                "Ou use o backend padrão: web.extract_backend: httpx"
            )

        async def _crawl() -> str:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url)
                return result.markdown or result.extracted_content or ""

        try:
            text = asyncio.run(_crawl())
        except Exception as exc:
            raise WebError(f"Erro crawl4ai em {url}: {exc}") from exc

        if not text:
            return "Conteúdo vazio ou não extraído pelo crawl4ai."

        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncado — limite de {max_chars} chars]"

        return text

    # --- Validação de URL -----------------------------------------------------

    def _validate_url(self, url: str) -> None:
        """Bloqueia URLs para hosts internos/privados (segurança)."""
        import ipaddress
        import urllib.parse as _urlparse

        if not url.startswith(("http://", "https://")):
            raise WebError("URL deve começar com http:// ou https://")

        parsed = _urlparse.urlparse(url)
        hostname = parsed.hostname or ""
        _BLOCKED = ("localhost", "127.", "0.0.0.0", "::1", "169.254.")
        if any(hostname.startswith(b) or hostname == b.rstrip(".") for b in _BLOCKED):
            raise WebError(f"Acesso bloqueado a host interno: '{hostname}'")

        try:
            addr = ipaddress.ip_address(hostname)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                raise WebError(f"Acesso bloqueado a IP privado: '{hostname}'")
        except ValueError:
            pass  # não é IP literal — ok
