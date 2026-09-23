"""Broker não bloqueante para o OAuth OpenAI/ChatGPT usado pelo Bauer CLI.

O browser recebe somente a URL de autorização. PKCE, state, code e tokens
permanecem no backend; o callback local conclui a mesma transação usada por
``bauer auth login -p openai`` e persiste via ``TokenStore``.
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .auth import AuthManager, OAuthAuthorization


logger = logging.getLogger("bauer.openai_browser_auth")


class OpenAIBrowserAuthError(RuntimeError):
    """Falha segura e apresentável do broker OAuth."""


class OpenAIBrowserAuthBusy(OpenAIBrowserAuthError):
    """A porta de callback não pôde ser iniciada."""


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class OpenAIBrowserAuthBroker:
    """Coordena uma transação PKCE e um callback local por processo."""

    def __init__(
        self,
        *,
        manager: AuthManager | None = None,
        callback_host: str | None = None,
        callback_port: int = 1455,
        ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.manager = manager or AuthManager()
        self.callback_host = callback_host or os.environ.get(
            "BAUER_OAUTH_CALLBACK_HOST", "127.0.0.1"
        )
        self.callback_port = int(callback_port)
        self.ttl_seconds = max(30, int(ttl_seconds))
        self._clock = clock
        self._lock = threading.RLock()
        self._pending: OAuthAuthorization | None = None
        self._phase = "disconnected"
        self._error = ""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://localhost:{self.callback_port}/auth/callback"

    def start(self) -> dict[str, Any]:
        with self._lock:
            self._expire_pending()
            self._ensure_server()
            if self._pending is not None and self._phase == "pending":
                return {
                    "authorization_url": self._pending.authorization_url,
                    "expires_at": int(
                        self._pending.created_at + self.ttl_seconds
                    ),
                }
            authorization = replace(
                self.manager.begin_oauth("openai", redirect_uri=self.redirect_uri),
                created_at=self._clock(),
            )
            self._pending = authorization
            self._phase = "pending"
            self._error = ""
            return {
                "authorization_url": authorization.authorization_url,
                "expires_at": int(authorization.created_at + self.ttl_seconds),
            }

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._expire_pending()
            token = self.manager.store.load("openai")
            phase = self._phase
            if token is not None and phase not in {"pending", "completing", "error"}:
                phase = "connected"
            elif token is None and phase == "connected":
                phase = "disconnected"
            return {
                "experimental": True,
                "connected": token is not None,
                "status": phase,
                "auth_type": (
                    "api_key" if token is not None and token.api_key else
                    "chatgpt_oauth" if token is not None else ""
                ),
                "expired": bool(token and token.is_expired),
                "has_refresh": bool(token and token.refresh_token),
                "expires_at": token.expires_at if token else None,
                "error": self._error,
            }

    def logout(self) -> bool:
        with self._lock:
            self._pending = None
            self._phase = "disconnected"
            self._error = ""
            return self.manager.logout("openai")

    def close(self) -> None:
        with self._lock:
            server = self._server
            self._server = None
            self._pending = None
        if server is not None:
            server.shutdown()
            server.server_close()

    def handle_callback(self, path: str) -> tuple[int, str, str]:
        parsed = urlparse(path)
        if parsed.path != "/auth/callback":
            return 404, "Callback inválido", "Esta rota existe somente para o login OpenAI."
        params = parse_qs(parsed.query)
        returned_state = params.get("state", [""])[0]

        with self._lock:
            self._expire_pending()
            authorization = self._pending
            if authorization is None:
                return 400, "Login expirado", "Volte ao Settings e inicie o login novamente."
            if not returned_state or not hmac.compare_digest(
                returned_state, authorization.state
            ):
                return 400, "Login rejeitado", "A validação de segurança do login falhou."
            oauth_error = params.get("error", [""])[0]
            if oauth_error:
                self._pending = None
                self._phase = "error"
                self._error = "Login cancelado ou recusado pela OpenAI."
                return 400, "Login não concluído", self._error
            code = params.get("code", [""])[0]
            if not code:
                return 400, "Login inválido", "O callback não trouxe código de autorização."
            self._pending = None
            self._phase = "completing"
            self._error = ""

        try:
            self.manager.complete_oauth(
                authorization,
                code=code,
                returned_state=returned_state,
                require_state=True,
            )
        except Exception as exc:  # noqa: BLE001 - nunca expor detalhes/token no browser
            logger.warning("OpenAI browser OAuth failed (%s)", type(exc).__name__)
            with self._lock:
                self._phase = "error"
                self._error = "Não foi possível concluir o login. Tente novamente."
            return 502, "Falha no login", self._error

        with self._lock:
            self._phase = "connected"
        return 200, "OpenAI conectada", "Você pode fechar esta janela e voltar ao Bauer."

    def _expire_pending(self) -> None:
        if self._pending is None:
            return
        if self._clock() - self._pending.created_at <= self.ttl_seconds:
            return
        self._pending = None
        self._phase = "error"
        self._error = "O login expirou. Inicie novamente."

    def _ensure_server(self) -> None:
        if self._server is not None:
            return
        broker = self

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - API da stdlib
                status, title, message = broker.handle_callback(self.path)
                body = (
                    "<!doctype html><html lang='pt-BR'><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width'>"
                    f"<title>{title}</title><body style='font:16px system-ui;"
                    "max-width:560px;margin:15vh auto;padding:24px'>"
                    f"<h1>{title}</h1><p>{message}</p>"
                    "<script>if(window.opener){setTimeout(()=>window.close(),1200)}</script>"
                    "</body></html>"
                ).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                return

        try:
            server = _ReusableThreadingHTTPServer(
                (self.callback_host, self.callback_port), CallbackHandler
            )
        except OSError as exc:
            raise OpenAIBrowserAuthBusy(
                f"A porta {self.callback_port} está ocupada. Feche outro login Bauer/Codex e tente novamente."
            ) from exc
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="bauer-openai-oauth-callback",
            daemon=True,
        )
        self._thread.start()
