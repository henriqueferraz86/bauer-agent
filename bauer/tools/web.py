"""Web tools: web_search, web_fetch, http_request.

Mixin herdado por ToolRouter. web_search/web_fetch usam self._web (WebDispatcher,
setado no __init__); http_request faz requisicao direta com guarda SSRF.
"""

from __future__ import annotations

import json

from .base import ToolError

# SSRF guard — mesmo padrao lazy do tool_router (fail-open se modulo ausente).
try:
    from ..url_safety import UrlSafetyError, is_safe_url as _is_safe_url
    _URL_SAFETY_AVAILABLE = True
except ImportError:
    _URL_SAFETY_AVAILABLE = False


class WebToolsMixin:
    """Ferramentas de rede: busca, fetch de URL e HTTP generico."""

    def _web_search(self, args: dict) -> str:
        query = args.get("query")
        if not query:
            raise ToolError("web_search requer 'query'.")
        max_results = min(self._coerce_int(args.get("max_results", 5), default=5, minimum=1), 10)

        from ..web.dispatcher import WebError
        try:
            return self._web.search_as_text(query, max_results=max_results)
        except WebError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Erro na busca web: {exc}") from exc

    def _web_fetch(self, args: dict) -> str:
        url = args.get("url")
        if not url:
            raise ToolError("web_fetch requer 'url'.")

        # Wave 4.5: SSRF prevention
        if _URL_SAFETY_AVAILABLE:
            try:
                _is_safe_url(url)
            except UrlSafetyError as exc:
                raise ToolError(f"[BLOCKED] SSRF: {exc}") from exc

        max_chars = self._coerce_int(args.get("max_chars", self._web.max_chars), default=self._web.max_chars, minimum=1)

        from ..web.dispatcher import EMPTY_EXTRACT_CRAWL4AI, EMPTY_EXTRACT_HTTPX, WebError
        try:
            result = self._web.extract(url, max_chars=max_chars)
        except WebError as exc:
            raise ToolError(str(exc)) from exc
        except Exception as exc:
            raise ToolError(f"Erro ao buscar URL: {exc}") from exc

        if result in (EMPTY_EXTRACT_HTTPX, EMPTY_EXTRACT_CRAWL4AI):
            # Extração estática veio vazia — típico de SPA JS-renderizada
            # (React/Next.js): o HTML que o servidor manda não tem texto
            # nenhum, só a casca que o JS preenche no navegador. Fallback
            # pro browser real (Playwright) que o Bauer já tem via as tools
            # browser_* — não depende de nada novo (crawl4ai usa a MESMA
            # tecnologia por baixo, ver docs/integrations/).
            browser_text = self._web_fetch_via_browser(url, max_chars)
            if browser_text:
                return browser_text
        return result

    def _web_fetch_via_browser(self, url: str, max_chars: int) -> str:
        """Fallback de `_web_fetch` para páginas onde a extração httpx veio
        vazia. Roda na thread dedicada do browser (G18 — Playwright sync é
        thread-affine, não pode rodar inline na thread chamadora de
        `execute()`). Retorna "" em qualquer falha (Playwright ausente,
        timeout, site bloqueou) — o chamador decide como degradar.

        SSRF via redirect: o Playwright segue redirects internamente, então a
        checagem de `_web_fetch` (só a URL inicial) não vale para onde a página
        realmente parou. Validamos `page.url` DEPOIS do goto e descartamos o
        conteúdo se o destino final for interno — o dano real é a metadata da
        cloud entrar no contexto do modelo, e isso fica barrado.
        """
        from ..web.dispatcher import clean_html_text

        def _do() -> tuple[str, str]:
            page = self._ensure_browser()
            page.goto(url, wait_until="networkidle", timeout=30_000)
            return page.content(), str(page.url or "")

        try:
            pool = self._get_browser_executor()
            html, final_url = pool.submit(_do).result(timeout=35)
        except Exception:
            return ""

        if final_url and final_url != url and _URL_SAFETY_AVAILABLE:
            try:
                _is_safe_url(final_url)
            except UrlSafetyError:
                return ""  # redirect terminou em host interno — descarta

        text = clean_html_text(html)
        if text and len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncado — limite de {max_chars} chars]"
        return text

    def _http_request(self, args: dict) -> str:
        url = args.get("url")
        method = str(args.get("method", "GET")).upper()
        headers = args.get("headers") or {}
        body = args.get("body")
        max_chars = self._coerce_int(args.get("max_chars", 5000), default=5000, minimum=1)

        if not url:
            raise ToolError("http_request requer 'url'.")
        if not url.startswith(("http://", "https://")):
            raise ToolError("URL deve comecar com http:// ou https://")
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
            raise ToolError(f"Metodo '{method}' nao suportado. Use: GET, POST, PUT, PATCH, DELETE.")

        # Wave 4.5: SSRF prevention (replaces manual blocklist)
        if _URL_SAFETY_AVAILABLE:
            try:
                _is_safe_url(url)
            except UrlSafetyError as exc:
                raise ToolError(f"[BLOCKED] SSRF: {exc}") from exc
        else:
            # Fallback minimal blocklist when url_safety module unavailable
            import ipaddress as _ipaddress
            import urllib.parse as _urlparse
            _parsed = _urlparse.urlparse(url)
            _hostname = _parsed.hostname or ""
            _BLOCKED = ("localhost", "127.", "0.0.0.0", "::1")
            if any(_hostname.startswith(b) or _hostname == b.rstrip(".") for b in _BLOCKED):
                raise ToolError(f"Acesso bloqueado a host interno: '{_hostname}'")
            try:
                _addr = _ipaddress.ip_address(_hostname)
                if _addr.is_private or _addr.is_loopback or _addr.is_link_local:
                    raise ToolError(f"Acesso bloqueado a endereco IP privado: '{_hostname}'")
            except ValueError:
                pass

        import httpx

        if not isinstance(headers, dict):
            raise ToolError("http_request: 'headers' deve ser um objeto JSON.")

        # Prepara body
        json_body = None
        content_body = None
        if body is not None:
            if isinstance(body, dict):
                json_body = body
            else:
                content_body = str(body).encode()

        try:
            from ..http_shared import shared_ssl_context
            _ssl = shared_ssl_context()
            # SSRF via redirect: com follow_redirects=True o httpx seguiria um
            # 3xx para http://169.254.169.254/ (metadata) ou um IP RFC-1918 SEM
            # revalidar — burlando o _is_safe_url que só olhou a URL inicial.
            # Seguimos manualmente, revalidando cada Location ANTES de ir nele.
            _MAX_REDIRECTS = 5
            resp = httpx.request(
                method, url, headers=headers, json=json_body, content=content_body,
                timeout=15.0, follow_redirects=False, verify=_ssl,
            )
            _hops = 0
            while resp.is_redirect and _hops < _MAX_REDIRECTS:
                location = resp.headers.get("location")
                if not location:
                    break
                next_url = str(resp.url.join(location))  # resolve relativo
                if _URL_SAFETY_AVAILABLE:
                    try:
                        _is_safe_url(next_url)
                    except UrlSafetyError as exc:
                        raise ToolError(f"[BLOCKED] SSRF em redirect: {exc}") from exc
                resp = httpx.request(
                    method, next_url, headers=headers, json=json_body,
                    content=content_body, timeout=15.0, follow_redirects=False, verify=_ssl,
                )
                _hops += 1
        except ToolError:
            raise
        except httpx.TimeoutException:
            raise ToolError(f"Timeout ao acessar {url}")
        except Exception as exc:
            raise ToolError(f"Erro na requisicao: {exc}")

        # Monta resposta
        content_type = resp.headers.get("content-type", "")
        lines = [
            f"HTTP {resp.status_code} {resp.reason_phrase}",
            f"Content-Type: {content_type}",
            f"Content-Length: {resp.headers.get('content-length', 'n/a')}",
            "---",
        ]

        if "json" in content_type:
            try:
                body_text = json.dumps(resp.json(), ensure_ascii=False, indent=2)
            except Exception:
                body_text = resp.text
        elif "text" in content_type or "html" in content_type or "xml" in content_type:
            body_text = resp.text
        else:
            body_text = f"[Conteudo binario — content-type: {content_type}]"

        if len(body_text) > max_chars:
            body_text = body_text[:max_chars] + f"\n[... truncado, limite de {max_chars} chars]"

        lines.append(body_text)
        return "\n".join(lines)
