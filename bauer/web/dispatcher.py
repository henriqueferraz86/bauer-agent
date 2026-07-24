"""Dispatcher de backends web — seleciona search e extract via config.

Arquitetura inspirada no Hermes Agent (NousResearch/hermes-agent):
  - search_backend e extract_backend são independentes
  - "auto" (padrão) detecta o melhor backend disponível sem config manual
  - Cada backend é ativado por presença de API key ou pacote instalado
  - Sem dependências obrigatórias além de httpx (já na stack)

Backends de busca (search_backend):
  auto     — auto-detecção por ordem: brave → searxng → ddgs  (padrão)
  ddgs     — DuckDuckGo (MIT, sem API key, requer: pip install ddgs)
  searxng  — SearXNG self-hosted (AGPL-3, requer: docker run searxng/searxng)
  brave    — Brave Search API (requer BRAVE_API_KEY no .env)

Backends de extração (extract_backend):
  auto     — auto-detecção: crawl4ai → httpx  (padrão)
  httpx    — httpx + BeautifulSoup (leve, zero config, sempre disponível)
  crawl4ai — Markdown limpo para LLMs (MIT, requer: pip install crawl4ai)

Auto-detecção de busca (ordem de prioridade):
  1. BRAVE_API_KEY presente no .env/env → brave
  2. SEARXNG_URL no env OU web.searxng_url não é o default → searxng
  3. ddgs instalado → ddgs
  4. Nenhum disponível → WebError com instruções claras

Auto-detecção de extração:
  1. crawl4ai instalado → crawl4ai
  2. → httpx (sempre disponível)
"""

from __future__ import annotations

import importlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bauer.config_loader import WebSection

_SEARXNG_DEFAULT_URL = "http://localhost:8080"

# Guard SSRF central — mesmo padrão lazy de bauer/tools/web.py (o blocklist
# manual de _validate_url é o fallback quando o módulo não está importável).
try:
    from bauer.url_safety import UrlSafetyError, check_url as _check_url
    _URL_SAFETY_AVAILABLE = True
except ImportError:  # pragma: no cover - módulo sempre presente na stack padrão
    _URL_SAFETY_AVAILABLE = False

# Mensagens-sentinela de extração vazia — nomeadas (não string literal
# duplicada) porque bauer/tools/web.py precisa detectá-las pra decidir se
# tenta o fallback via browser real (páginas JS-renderizadas/SPA).
EMPTY_EXTRACT_HTTPX = "Conteúdo vazio ou não extraído."
EMPTY_EXTRACT_CRAWL4AI = "Conteúdo vazio ou não extraído pelo crawl4ai."


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
# Helpers de detecção
# ---------------------------------------------------------------------------

def _package_available(name: str) -> bool:
    """Retorna True se o pacote Python está instalado."""
    import sys
    # sys.modules[name] = None indica "bloqueado" — tratar como não disponível
    if name in sys.modules:
        return sys.modules[name] is not None
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ModuleNotFoundError):
        return False


def _env(key: str, cfg_val: str = "") -> str:
    """Retorna valor do .env/env ou fallback da config."""
    return os.environ.get(key, "") or cfg_val


def clean_html_text(html: str) -> str:
    """Remove script/style/nav/footer/header/aside e devolve texto legível.

    Compartilhado entre `_extract_httpx` (HTML estático via httpx) e o
    fallback via browser real em bauer/tools/web.py (SPA/JS-renderizada) —
    mesma limpeza, mesma qualidade de saída, independente da fonte do HTML.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    except Exception:
        # bs4 ausente/falhou — fallback SEM bs4 que ainda LIMPA de verdade:
        # remove blocos de script/style e depois todas as tags via regex. Sem
        # isto, uma página só de tags ("<html><body></body></html>") voltava
        # como HTML cru (bug pego só no CI, onde bs4 não estava instalado).
        import re as _re
        _no_blocks = _re.sub(
            r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html,
            flags=_re.IGNORECASE | _re.DOTALL,
        )
        text = _re.sub(r"<[^>]+>", " ", _no_blocks)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatcher principal
# ---------------------------------------------------------------------------

class WebDispatcher:
    """Seleciona e delega para backends de search e extract configurados.

    Uso:
        dispatcher = WebDispatcher(cfg.web)
        results = dispatcher.search("query", max_results=5)
        content = dispatcher.extract("https://...", max_chars=5000)

    Com "auto" (padrão), detecta o melhor backend disponível:
      - Busca:   brave (se BRAVE_API_KEY) → searxng (se URL) → ddgs
      - Extração: crawl4ai (se instalado) → httpx
    """

    def __init__(self, web_config: "WebSection | None" = None):
        self._cfg = web_config
        # Cache TTL de resultados (busca/extração) — repetições viram instantâneas.
        self._cache: dict = {}              # key -> (timestamp, value)
        self._http_client = None            # httpx.Client persistente (reuso de conexão)

    # --- Propriedades de config -----------------------------------------------

    @property
    def _raw_search_backend(self) -> str:
        return (self._cfg.search_backend if self._cfg else None) or "auto"

    @property
    def _raw_extract_backend(self) -> str:
        return (self._cfg.extract_backend if self._cfg else None) or "auto"

    @property
    def max_results(self) -> int:
        return self._cfg.max_results if self._cfg else 5

    @property
    def max_chars(self) -> int:
        return self._cfg.max_chars if self._cfg else 5000

    @property
    def timeout(self) -> int:
        return self._cfg.timeout_seconds if self._cfg else 15

    @property
    def _searxng_url(self) -> str:
        """URL do SearXNG — prioriza SEARXNG_URL do env."""
        return (
            _env("SEARXNG_URL")
            or (self._cfg.searxng_url if self._cfg else "")
            or _SEARXNG_DEFAULT_URL
        )

    @property
    def _brave_key(self) -> str:
        """API key Brave — prioriza BRAVE_API_KEY do env."""
        return _env("BRAVE_API_KEY", self._cfg.brave_api_key if self._cfg else "")

    @property
    def _wikipedia_lang(self) -> str:
        """Idioma da Wikipedia (default 'en' — mais completa)."""
        return (getattr(self._cfg, "wikipedia_lang", "") if self._cfg else "") or "en"

    @property
    def cache_ttl(self) -> int:
        """TTL do cache de busca/extração em segundos (0 = desabilitado)."""
        v = getattr(self._cfg, "cache_ttl_seconds", None) if self._cfg else None
        return 300 if v is None else int(v)

    # --- HTTP client persistente (reuso de conexão / keep-alive) -------------

    @property
    def _http(self):
        """httpx.Client persistente — reusa conexão TLS entre chamadas.

        follow_redirects fica como default (False), igual ao httpx.get antigo —
        a extração passa follow_redirects=True por chamada quando precisa.
        """
        import httpx
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self.timeout)
        return self._http_client

    def close(self) -> None:
        """Fecha o client HTTP persistente (chamar no fim da sessão)."""
        if self._http_client is not None:
            try:
                self._http_client.close()
            except Exception:
                pass
            self._http_client = None

    # --- Cache TTL -----------------------------------------------------------

    def _cache_get(self, key: tuple):
        """Retorna valor cacheado se dentro do TTL, senão None."""
        if self.cache_ttl <= 0:
            return None
        import time as _t
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, value = entry
        if _t.monotonic() - ts < self.cache_ttl:
            return value
        self._cache.pop(key, None)  # expirado
        return None

    def _cache_put(self, key: tuple, value) -> None:
        if self.cache_ttl <= 0:
            return
        import time as _t
        self._cache[key] = (_t.monotonic(), value)

    # --- Auto-detecção --------------------------------------------------------

    @property
    def search_backend(self) -> str:
        """Backend de busca resolvido (após auto-detecção se necessário)."""
        raw = self._raw_search_backend.lower()
        if raw != "auto":
            return raw
        return self._detect_search_backend()

    @property
    def extract_backend(self) -> str:
        """Backend de extração resolvido (após auto-detecção se necessário)."""
        raw = self._raw_extract_backend.lower()
        if raw != "auto":
            return raw
        return self._detect_extract_backend()

    def _detect_search_backend(self) -> str:
        """Detecta o melhor backend de busca disponível.

        Ordem (inspirada no Hermes Agent, com fallback open-source garantido):
          1. brave     — se BRAVE_API_KEY presente no env
          2. searxng   — se SEARXNG_URL no env ou searxng_url não é o default
          3. ddgs      — se pacote instalado
          4. wikipedia — fallback SEMPRE disponível (open/CC BY-SA, sem chave,
                         sem dependência). Garante que web_search nunca falhe
                         por falta de setup — preciso para fatos/entidades.
        """
        if self._brave_key:
            return "brave"

        searxng_configured = (
            _env("SEARXNG_URL")
            or (self._cfg and self._cfg.searxng_url and self._cfg.searxng_url != _SEARXNG_DEFAULT_URL)
        )
        if searxng_configured:
            return "searxng"

        if _package_available("ddgs"):
            return "ddgs"

        # Fallback open-source de último recurso: Wikipedia (zero setup).
        return "wikipedia"

    def _detect_extract_backend(self) -> str:
        """Detecta o melhor backend de extração disponível.

        Ordem:
          1. crawl4ai — se instalado (Markdown limpo, melhor para LLMs)
          2. httpx    — sempre disponível (fallback leve)
        """
        if _package_available("crawl4ai"):
            return "crawl4ai"
        return "httpx"

    def detected_backends(self) -> dict[str, str]:
        """Retorna backends detectados para exibição em bauer doctor / status."""
        try:
            search = self.search_backend
            search_reason = self._detection_reason_search()
        except WebError as e:
            search = "none"
            search_reason = str(e).splitlines()[0]

        extract = self.extract_backend
        extract_reason = self._detection_reason_extract()

        return {
            "search": search,
            "search_reason": search_reason,
            "extract": extract,
            "extract_reason": extract_reason,
        }

    def _detection_reason_search(self) -> str:
        if self._raw_search_backend != "auto":
            return f"configurado manualmente: {self._raw_search_backend}"
        if self._brave_key:
            return "BRAVE_API_KEY detectado"
        searxng_configured = _env("SEARXNG_URL") or (
            self._cfg and self._cfg.searxng_url != _SEARXNG_DEFAULT_URL
        )
        if searxng_configured:
            return f"SEARXNG_URL detectado: {self._searxng_url}"
        if _package_available("ddgs"):
            return "pacote ddgs instalado"
        return f"wikipedia ({self._wikipedia_lang}) — fallback open-source"

    def _detection_reason_extract(self) -> str:
        if self._raw_extract_backend != "auto":
            return f"configurado manualmente: {self._raw_extract_backend}"
        if _package_available("crawl4ai"):
            return "pacote crawl4ai instalado"
        return "httpx (padrão leve)"

    # --- Search ---------------------------------------------------------------

    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        """Executa busca usando o backend detectado/configurado (com cache TTL)."""
        n = max_results or self.max_results
        backend = self.search_backend  # pode levantar WebError se "auto" e sem backend

        _ckey = ("search", backend, query, n)
        _cached = self._cache_get(_ckey)
        if _cached is not None:
            return _cached

        results = self._search_dispatch(backend, query, n)
        self._cache_put(_ckey, results)
        return results

    def _search_dispatch(self, backend: str, query: str, n: int) -> list[SearchResult]:
        if backend == "ddgs":
            return self._search_ddgs(query, n)
        elif backend == "searxng":
            return self._search_searxng(query, n)
        elif backend == "brave":
            return self._search_brave(query, n)
        elif backend == "wikipedia":
            return self._search_wikipedia(query, n)
        else:
            raise WebError(
                f"Backend de busca '{backend}' desconhecido. "
                "Opções: auto, ddgs, searxng, brave, wikipedia"
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
        """Extrai conteúdo de URL usando o backend detectado/configurado (com cache TTL)."""
        n = max_chars or self.max_chars
        self._validate_url(url)
        backend = self.extract_backend

        _ckey = ("extract", backend, url, n)
        _cached = self._cache_get(_ckey)
        if _cached is not None:
            return _cached

        if backend == "httpx":
            out = self._extract_httpx(url, n)
        elif backend == "crawl4ai":
            out = self._extract_crawl4ai(url, n)
        else:
            raise WebError(
                f"Backend de extração '{backend}' desconhecido. "
                "Opções: auto, httpx, crawl4ai"
            )
        self._cache_put(_ckey, out)
        return out

    # --- Backends de busca ----------------------------------------------------

    def _search_ddgs(self, query: str, max_results: int) -> list[SearchResult]:
        """DuckDuckGo via biblioteca ddgs (MIT, sem API key)."""
        try:
            from ddgs import DDGS
        except ImportError:
            raise WebError(
                "Backend 'ddgs' não instalado.\n"
                "Instale com: pip install ddgs\n"
                "Ou configure outro backend: web.search_backend: searxng"
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
        base_url = self._searxng_url.rstrip("/")
        try:
            resp = self._http.get(
                f"{base_url}/search",
                params={"q": query, "format": "json",
                        "engines": "google,bing,duckduckgo", "safesearch": "1"},
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.ConnectError:
            raise WebError(
                f"Não foi possível conectar ao SearXNG em {base_url}.\n"
                "Certifique-se de que está rodando:\n"
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
        """Brave Search API (requer BRAVE_API_KEY)."""
        import httpx
        api_key = self._brave_key
        if not api_key:
            raise WebError(
                "Backend 'brave' requer BRAVE_API_KEY.\n"
                "Adicione no .env: BRAVE_API_KEY=sua-chave\n"
                "Obtenha em: https://api.search.brave.com/\n"
                "Ou use outro backend: web.search_backend: ddgs"
            )
        try:
            resp = self._http.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results, "safesearch": "moderate"},
                headers={"Accept": "application/json", "X-Subscription-Token": api_key},
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

    def _search_wikipedia(self, query: str, max_results: int) -> list[SearchResult]:
        """Wikipedia via MediaWiki API (open/CC BY-SA, sem chave, sem dependência).

        Preciso para fatos, entidades e definições. Usa list=search e monta a
        URL canônica do artigo. É o fallback open-source garantido do 'auto'.
        """
        import html
        import re as _re
        import httpx

        lang = self._wikipedia_lang
        base = f"https://{lang}.wikipedia.org/w/api.php"
        try:
            resp = self._http.get(
                base,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": max_results,
                    "format": "json",
                    "srprop": "snippet",
                },
                timeout=self.timeout,
                headers={"User-Agent": "BauerAgent/1.0 (https://github.com/henriqueferraz86/bauer-agent)"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise WebError(f"Erro Wikipedia (HTTP {exc.response.status_code}): {exc}") from exc
        except Exception as exc:
            raise WebError(f"Erro na busca Wikipedia: {exc}") from exc

        def _clean(snippet: str) -> str:
            # remove tags HTML (<span class="searchmatch">…</span>) e desescapa entidades
            return html.unescape(_re.sub(r"<[^>]+>", "", snippet or "")).strip()

        results = []
        for r in data.get("query", {}).get("search", [])[:max_results]:
            title = r.get("title", "").strip()
            if not title:
                continue
            url = f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"
            results.append(SearchResult(
                title=title,
                url=url,
                snippet=_clean(r.get("snippet", "")),
                engine="wikipedia",
            ))
        return results

    # --- Backends de extração -------------------------------------------------

    # Teto de saltos de redirect — igual ao de bauer/tools/web.py (http_request).
    _MAX_REDIRECTS = 5

    def _get_revalidating_redirects(self, url: str):
        """GET que segue redirects MANUALMENTE, revalidando cada salto.

        SSRF via redirect: `_validate_url` só olha a URL que o chamador passou.
        Com `follow_redirects=True` o httpx seguiria um 3xx para
        http://169.254.169.254/ (metadata da cloud) ou um IP RFC-1918 sem que
        nenhum guard fosse consultado de novo — o conteúdo interno voltaria como
        texto direto pro contexto do modelo. Aqui cada `Location` passa pelo
        MESMO `_validate_url` ANTES de ser seguido, então a request maliciosa
        nunca chega a sair. Mesma estratégia já usada na tool `http_request`.
        """
        headers = {"User-Agent": "Mozilla/5.0 (compatible; BauerAgent/1.0)"}
        resp = self._http.get(
            url, timeout=self.timeout, follow_redirects=False, headers=headers,
        )
        hops = 0
        while resp.is_redirect and hops < self._MAX_REDIRECTS:
            location = resp.headers.get("location")
            if not location:
                break
            next_url = str(resp.url.join(location))  # resolve Location relativo
            self._validate_url(next_url)             # levanta WebError se interno
            resp = self._http.get(
                next_url, timeout=self.timeout, follow_redirects=False,
                headers=headers,
            )
            hops += 1
        if resp.is_redirect and hops >= self._MAX_REDIRECTS:
            # Estourar o teto tem que ser erro explícito. Devolver a resposta
            # 3xx aqui seria pior que inútil: `raise_for_status()` não levanta
            # em 3xx e o corpo de um redirect não tem content-type de texto, o
            # chamador acabaria reportando "conteúdo binário" para o que na
            # verdade é um loop de redirect.
            raise WebError(
                f"Redirect demais ao acessar {url} "
                f"(limite de {self._MAX_REDIRECTS} saltos)."
            )
        return resp

    def _extract_httpx(self, url: str, max_chars: int) -> str:
        """httpx + BeautifulSoup (padrão, leve, zero config)."""
        import httpx
        try:
            resp = self._get_revalidating_redirects(url)
            resp.raise_for_status()
        except WebError:
            raise
        except httpx.TimeoutException:
            raise WebError(f"Timeout ao acessar {url}")
        except httpx.HTTPStatusError as exc:
            raise WebError(f"HTTP {exc.response.status_code} ao acessar {url}")
        except Exception as exc:
            raise WebError(f"Erro ao acessar {url}: {exc}")

        content_type = resp.headers.get("content-type", "")
        if "text" not in content_type and "html" not in content_type:
            return f"[Conteúdo binário — content-type: {content_type}]"

        text = clean_html_text(resp.text)

        if not text:
            return EMPTY_EXTRACT_HTTPX
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncado — limite de {max_chars} chars]"
        return text

    def _extract_crawl4ai(self, url: str, max_chars: int) -> str:
        """crawl4ai — extração LLM-friendly em Markdown (MIT).

        Requer: pip install crawl4ai && crawl4ai-setup

        SSRF via redirect (limitação conhecida): o crawl4ai dirige um browser
        headless e segue redirects internamente — não há hook para revalidar
        cada salto como em `_get_revalidating_redirects`. O que dá para fazer é
        barrar a URL FINAL: se o crawler terminou num host interno, o conteúdo
        é descartado e nunca entra no contexto do modelo. A request em si já
        saiu (SSRF cego), mas a exfiltração de metadata/rede interna — o dano
        real — fica bloqueada.
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
        async def _crawl() -> tuple[str, str]:
            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=url)
                text = result.markdown or result.extracted_content or ""
                return text, str(getattr(result, "url", "") or "")
        try:
            text, final_url = asyncio.run(_crawl())
        except Exception as exc:
            raise WebError(f"Erro crawl4ai em {url}: {exc}") from exc

        if final_url and final_url != url:
            self._validate_url(final_url)  # levanta WebError se interno

        if not text:
            return EMPTY_EXTRACT_CRAWL4AI
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncado — limite de {max_chars} chars]"
        return text

    # --- Validação de URL -----------------------------------------------------

    def _validate_url(self, url: str) -> None:
        """Bloqueia URLs para hosts internos/privados.

        Delega para `bauer.url_safety` (fonte única de verdade do guard SSRF,
        usada também pelas tools web_fetch/http_request): além do blocklist
        manual abaixo ele cobre endpoints de metadata por nome
        (metadata.google.internal), fc00::/7, CGNAT e — o que mais importa —
        RESOLVE o hostname e recheca os IPs, pegando o caso de um nome público
        que aponta para 127.0.0.1. O blocklist manual fica como fallback para
        quando o módulo não estiver importável.
        """
        import ipaddress
        import urllib.parse as _urlparse

        if not url.startswith(("http://", "https://")):
            raise WebError("URL deve começar com http:// ou https://")

        if _URL_SAFETY_AVAILABLE:
            try:
                _check_url(url)
            except UrlSafetyError as exc:
                raise WebError(f"Acesso bloqueado (SSRF): {exc}") from exc
            return

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
            pass
