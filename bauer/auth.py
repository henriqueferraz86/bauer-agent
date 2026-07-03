"""Bauer Auth — Autenticação via browser para providers cloud.

Suporta:
  - OpenAI (ChatGPT OAuth / API Key)
  - Anthropic (API Key)
  - Providers customizados (OpenAI-compatible)

Fluxo OAuth:
  1. Inicia servidor local para receber callback
  2. Abre browser para login no provider
  3. Recebe token via callback
  4. Armazena de forma segura
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx


# ─── Configuração de Providers ───────────────────────────────────────────────

PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI (ChatGPT)",
        "auth_type": "oauth",
        "issuer": "https://auth.openai.com",
        "authorize_url": "https://auth.openai.com/oauth/authorize",
        "token_url": "https://auth.openai.com/oauth/token",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "scopes": "openid profile email offline_access api.connectors.read api.connectors.invoke",
        "api_base": "https://api.openai.com/v1",
        "port": 1455,
        "extra_params": {
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
        },
    },
    "openai-api": {
        "name": "OpenAI (API Key)",
        "auth_type": "api_key",
        "api_base": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
    },
    "anthropic": {
        "name": "Anthropic",
        "auth_type": "api_key",
        "api_base": "https://api.anthropic.com",
        "env_key": "ANTHROPIC_API_KEY",
    },
    "groq": {
        "name": "Groq",
        "auth_type": "api_key",
        "api_base": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
    },
    "openrouter": {
        "name": "OpenRouter",
        "auth_type": "api_key",
        "api_base": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
    },
    "custom": {
        "name": "Custom (OpenAI-compatible)",
        "auth_type": "api_key",
        "api_base": None,
        "env_key": None,
    },
    "mistral": {
        "name": "Mistral AI",
        "auth_type": "api_key",
        "api_base": "https://api.mistral.ai/v1",
        "env_key": "MISTRAL_API_KEY",
    },
    "xai": {
        "name": "xAI Grok",
        "auth_type": "api_key",
        "api_base": "https://api.x.ai/v1",
        "env_key": "XAI_API_KEY",
    },
    "together": {
        "name": "Together AI",
        "auth_type": "api_key",
        "api_base": "https://api.together.xyz/v1",
        "env_key": "TOGETHER_API_KEY",
    },
    "deepseek": {
        "name": "DeepSeek",
        "auth_type": "api_key",
        "api_base": "https://api.deepseek.com/v1",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "gemini": {
        "name": "Google Gemini",
        "auth_type": "api_key",
        "api_base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_key": "GEMINI_API_KEY",
    },
    "github": {
        "name": "GitHub Models",
        "auth_type": "device_flow",
        "api_base": "https://models.inference.ai.azure.com",
        "env_key": "GITHUB_TOKEN",
        # GitHub device flow para obter PAT
        "device_code_url": "https://github.com/login/device/code",
        "token_url": "https://github.com/login/oauth/access_token",
        "client_id": "Iv23li8gHkCtMXGWDB4Q",  # client_id público do GitHub CLI
        "scope": "read:user",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "auth_type": "device_flow",
        "api_base": "https://api.githubcopilot.com",
        "env_key": "COPILOT_TOKEN",
        # Device flow → OAuth token → troca por Copilot session token
        "device_code_url": "https://github.com/login/device/code",
        "token_url": "https://github.com/login/oauth/access_token",
        "client_id": "Iv1.b507a08c87ecfe98",  # VS Code Copilot extension (público)
        "scope": "read:user",
        # Depois do device flow, troca pelo token de sessão Copilot
        "copilot_token_url": "https://api.github.com/copilot_internal/v2/token",
    },
}


# ─── Crypto Helpers ──────────────────────────────────────────────────────────

def _generate_pkce() -> tuple[str, str]:
    """Gera code_verifier e code_challenge para PKCE."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    import base64
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Fernet encryption (SEG-2) ────────────────────────────────────────────────
# Usa criptografia real (AES-128-CBC + HMAC-SHA256) se o pacote `cryptography`
# estiver instalado. Caso contrário, cai back para XOR para não quebrar
# ambientes sem a dependência.
#
# Formato em disco:
#   Fernet: "fernet:<base64url_token>"
#   Legacy XOR: qualquer string sem o prefixo "fernet:"
#
# Retrocompatibilidade: ao carregar um token legacy, desofusca com XOR,
# re-encripta com Fernet e salva automaticamente.

_FERNET_PREFIX = "fernet:"


def _derive_fernet_key(raw_key: str) -> bytes:
    """Deriva chave Fernet de 32 bytes via PBKDF2-HMAC-SHA256."""
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    import base64
    salt = b"bauer-auth-v2"  # salt fixo por design (chave já é aleatória)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
    return base64.urlsafe_b64encode(kdf.derive(raw_key.encode()))


def _try_get_fernet(raw_key: str):
    """Retorna objeto Fernet. Lança ImportError com mensagem clara se biblioteca ausente."""
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ImportError(
            "A biblioteca 'cryptography' é necessária para armazenar tokens de forma segura. "
            "Instale com: pip install 'bauer-agent[keychain]' ou pip install cryptography>=41.0"
        ) from exc
    key = _derive_fernet_key(raw_key)
    return Fernet(key)


def _xor_encrypt(token: str, key: str) -> str:
    """XOR legacy — mantido como fallback."""
    import base64
    encrypted = bytes(b ^ ord(key[i % len(key)]) for i, b in enumerate(token.encode()))
    return base64.b64encode(encrypted).decode()


def _xor_decrypt(encrypted: str, key: str) -> str:
    """Desofusca XOR legacy."""
    import base64
    decoded = base64.b64decode(encrypted)
    return bytes(b ^ ord(key[i % len(key)]) for i, b in enumerate(decoded)).decode()


def _encrypt_token(token: str, key: str) -> str:
    """Encripta token com Fernet (AES-CBC + HMAC). Requer biblioteca 'cryptography'."""
    if not key or not token:
        return token
    fernet = _try_get_fernet(key)  # lança ImportError se cryptography ausente
    return _FERNET_PREFIX + fernet.encrypt(token.encode()).decode()


def _decrypt_token(encrypted: str, key: str) -> str:
    """Decripta token — suporta Fernet e XOR legacy."""
    if not key or not encrypted:
        return encrypted
    if encrypted.startswith(_FERNET_PREFIX):
        fernet = _try_get_fernet(key)
        if fernet is None:
            raise ValueError(
                "Token encriptado com Fernet mas 'cryptography' não está instalado. "
                "Execute: pip install cryptography"
            )
        try:
            return fernet.decrypt(encrypted[len(_FERNET_PREFIX):].encode()).decode()
        except Exception as exc:
            raise ValueError(f"Falha ao decriptar token Fernet: {exc}") from exc
    # Legacy XOR — tenta desofuscar
    try:
        return _xor_decrypt(encrypted, key)
    except Exception:
        return encrypted  # já estava em plaintext (sem encriptação)


def _extract_chatgpt_account_id(id_token: str | None) -> str:
    """Decodifica o JWT id_token (sem verificar assinatura) e extrai o
    chatgpt_account_id do claim `https://api.openai.com/auth`.

    Retorna "" se não encontrar — o backend pode aceitar sem o header em
    algumas contas, e o erro fica explícito na primeira chamada.
    """
    if not id_token:
        return ""
    try:
        import base64 as _b64
        import json as _json
        parts = id_token.split(".")
        if len(parts) < 2:
            return ""
        payload_b64 = parts[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # padding base64url
        payload = _json.loads(_b64.urlsafe_b64decode(payload_b64))
        auth_claim = payload.get("https://api.openai.com/auth", {})
        return (
            auth_claim.get("chatgpt_account_id")
            or auth_claim.get("chatgpt_user_id")
            or ""
        )
    except Exception:
        return ""


# ─── Token Storage ───────────────────────────────────────────────────────────

@dataclass
class AuthToken:
    """Token de autenticação."""
    provider: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    token_type: str = "Bearer"
    scope: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
            "scope": self.scope,
            "api_key": self.api_key,
            "api_base": self.api_base,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuthToken:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TokenStore:
    """Armazenamento seguro de tokens."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or Path.home() / ".bauer"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.tokens_file = self.base_dir / "auth.json"
        self._obfuscation_key = self._get_or_create_key()

    def _get_or_create_key(self) -> str:
        """Chave de ofuscação baseada no machine ID."""
        key_file = self.base_dir / ".auth_key"
        if key_file.exists():
            return key_file.read_text().strip()
        key = secrets.token_hex(16)
        key_file.write_text(key)
        return key

    def save(self, token: AuthToken) -> None:
        """Salva token de forma segura."""
        tokens = self.load_all()
        tokens[token.provider] = token.to_dict()

        # Ofuscar tokens sensíveis
        secure_data = {}
        for provider, data in tokens.items():
            secure_data[provider] = data.copy()
            if "access_token" in data and data["access_token"]:
                secure_data[provider]["access_token"] = _encrypt_token(
                    data["access_token"], self._obfuscation_key
                )
            if "refresh_token" in data and data.get("refresh_token"):
                secure_data[provider]["refresh_token"] = _encrypt_token(
                    data["refresh_token"], self._obfuscation_key
                )
            if "api_key" in data and data.get("api_key"):
                secure_data[provider]["api_key"] = _encrypt_token(
                    data["api_key"], self._obfuscation_key
                )

        with open(self.tokens_file, "w", encoding="utf-8") as f:
            json.dump(secure_data, f, indent=2)

    def load(self, provider: str) -> AuthToken | None:
        """Carrega token de um provider."""
        tokens = self.load_all()
        if provider not in tokens:
            return None

        data = tokens[provider].copy()

        # Desofuscar
        if "access_token" in data and data["access_token"]:
            data["access_token"] = _decrypt_token(
                data["access_token"], self._obfuscation_key
            )
        if "refresh_token" in data and data.get("refresh_token"):
            data["refresh_token"] = _decrypt_token(
                data["refresh_token"], self._obfuscation_key
            )
        if "api_key" in data and data.get("api_key"):
            data["api_key"] = _decrypt_token(
                data["api_key"], self._obfuscation_key
            )

        return AuthToken.from_dict(data)

    def load_all(self) -> dict[str, dict[str, Any]]:
        """Carrega todos os tokens."""
        if not self.tokens_file.exists():
            return {}
        try:
            return json.loads(self.tokens_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def delete(self, provider: str) -> bool:
        """Remove token de um provider."""
        tokens = self.load_all()
        if provider not in tokens:
            return False
        del tokens[provider]
        with open(self.tokens_file, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        return True

    def list_providers(self) -> list[str]:
        """Lista providers autenticados."""
        return list(self.load_all().keys())


# ─── OAuth Server ────────────────────────────────────────────────────────────

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handler para receber callback OAuth."""

    auth_code: str | None = None
    state: str | None = None
    actual_port: int = 1455
    success_served: bool = False   # True depois que /success foi entregue ao browser

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/auth/callback" and "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            _OAuthCallbackHandler.state = params.get("state", [None])[0]

            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.actual_port}/success")
            self.end_headers()
        elif parsed.path == "/success":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Autenticado com sucesso!</h1>"
                b"<p>Voce pode fechar esta janela e voltar ao terminal.</p>"
                b"<script>setTimeout(() => window.close(), 2000);</script>"
                b"</body></html>"
            )
            _OAuthCallbackHandler.success_served = True
        elif "error" in params:
            error = params.get("error", ["unknown"])[0]
            desc = params.get("error_description", [""])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                f"<html><body><h1>Erro na autenticacao</h1>"
                f"<p><b>{error}</b></p>"
                f"<p>{desc}</p></body></html>".encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suprime logs do servidor."""
        pass


class OAuthCallbackServer:
    """Servidor local para receber callback OAuth."""

    def __init__(self, port: int = 1455):
        self.port = port
        self.server: HTTPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        """Inicia servidor em background."""
        _OAuthCallbackHandler.actual_port = self.port
        self.server = HTTPServer(("127.0.0.1", self.port), _OAuthCallbackHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # Aguardar servidor estar pronto
        time.sleep(0.5)

    def stop(self) -> None:
        """Para o servidor."""
        if self.server:
            self.server.shutdown()

    def wait_for_code(self, timeout: int = 300) -> tuple[str | None, str | None]:
        """Aguarda o código de autorização e a entrega da página /success ao browser."""
        start = time.time()
        # Fase 1: espera o /auth/callback ser recebido
        while time.time() - start < timeout:
            if _OAuthCallbackHandler.auth_code:
                break
            time.sleep(0.1)

        code = _OAuthCallbackHandler.auth_code
        state = _OAuthCallbackHandler.state
        _OAuthCallbackHandler.auth_code = None
        _OAuthCallbackHandler.state = None

        if not code:
            return None, None

        # Fase 2: aguarda o browser receber /success (até 3s extra)
        success_deadline = time.time() + 3.0
        while time.time() < success_deadline:
            if _OAuthCallbackHandler.success_served:
                break
            time.sleep(0.05)
        _OAuthCallbackHandler.success_served = False

        return code, state


# ─── Auth Manager ────────────────────────────────────────────────────────────

class AuthManager:
    """Gerencia autenticação com providers."""

    def __init__(self, base_dir: Path | None = None):
        self.store = TokenStore(base_dir)
        # Lazy: criar httpx.Client custa ~260ms de SSL context no Windows, e
        # AuthManager é instanciado a cada _build_client (62× no startup com
        # a lista de fallbacks) — a maioria nunca faz requisição nenhuma.
        self._http_client: "httpx.Client | None" = None

    @property
    def _http(self) -> "httpx.Client":
        if self._http_client is None:
            from .http_shared import shared_ssl_context
            self._http_client = httpx.Client(
                timeout=30, follow_redirects=True, verify=shared_ssl_context()
            )
        return self._http_client

    def login_oauth(self, provider: str, port: int | None = None) -> AuthToken:
        """Login via OAuth Authorization Code Flow com PKCE — igual ao Codex CLI.

        Fluxo:
        1. Gera PKCE (code_verifier + code_challenge)
        2. Inicia servidor local na porta 1455
        3. Abre browser para auth.openai.com
        4. Usuário loga no ChatGPT
        5. Recebe callback com código
        6. Troca código por tokens
        """
        if provider not in PROVIDERS:
            raise ValueError(f"Provider '{provider}' nao suporta OAuth")

        config = PROVIDERS[provider]
        if config["auth_type"] != "oauth":
            raise ValueError(f"Provider '{provider}' nao usa OAuth")

        issuer = config["issuer"]
        client_id = config["client_id"]
        scopes = config["scopes"]
        extra_params = config.get("extra_params", {})
        actual_port = port or config.get("port", 1455)

        # PKCE
        pkce = _generate_pkce()
        code_verifier, code_challenge = pkce
        state = secrets.token_urlsafe(32)

        # Redirect URI
        redirect_uri = f"http://localhost:{actual_port}/auth/callback"

        # Construir URL de autorização (igual ao Codex CLI)
        auth_params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        # Adicionar parâmetros extras
        auth_params.update(extra_params)

        # URL encode
        query_string = "&".join(
            f"{k}={quote(str(v), safe='')}"
            for k, v in auth_params.items()
        )
        auth_url = f"{issuer}/oauth/authorize?{query_string}"

        # Iniciar servidor de callback
        server = OAuthCallbackServer(actual_port)
        server.start()

        print(f"\n{'='*60}")
        print(f"Autenticar com {config['name']}")
        print(f"{'='*60}")
        print("\nAbrindo browser para login...")
        print("\nSe o browser nao abrir, acesse:")
        print(f"  {auth_url}")
        print("\nAguardando autenticacao...")

        # Abrir browser
        webbrowser.open(auth_url)

        # Aguardar callback + /success ser servido ao browser
        code, returned_state = server.wait_for_code(timeout=300)
        server.stop()

        if not code:
            raise TimeoutError("Tempo esgotado aguardando autenticacao")

        if returned_state != state:
            raise ValueError("State mismatch - possivel ataque CSRF")

        # Trocar code por token
        token_data = self._exchange_code(
            issuer=issuer,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=code_verifier,
            code=code,
        )

        # Extrai o chatgpt_account_id do id_token (JWT) — necessário para o
        # backend ChatGPT (Responses API) billar na assinatura, igual ao Codex.
        _account_id = _extract_chatgpt_account_id(token_data.get("id_token"))

        token = AuthToken(
            provider=provider,
            access_token=token_data["access_token"],
            refresh_token=token_data.get("refresh_token"),
            expires_at=time.time() + token_data.get("expires_in", 3600),
            token_type=token_data.get("token_type", "Bearer"),
            api_base=config.get("api_base"),
            extra={
                "id_token": token_data.get("id_token"),
                "chatgpt_account_id": _account_id,
            },
        )

        self.store.save(token)

        # Tentar obter API key via token exchange (igual Codex CLI)
        # Nota: requer organization_id na conta OpenAI.
        # Contas pessoais (sem org) recebem 401/403 aqui — fallback para access_token.
        id_token = token_data.get("id_token")
        if id_token:
            api_key = self._obtain_api_key(issuer, client_id, id_token)
            if api_key:
                token.api_key = api_key
                self.store.save(token)
                print("[✓] API key de sessão obtida via token exchange.")
            else:
                # Sem API key — usará access_token como Bearer.
                # Isso funciona para autenticação mas requer billing na conta API da OpenAI.
                print(
                    "[!] Token exchange nao retornou API key (normal para contas pessoais sem org).\n"
                    "    Usando access_token OAuth — requer billing em platform.openai.com/settings/billing\n"
                    "    para usar a API developer. ChatGPT Plus (assinatura web) e separado."
                )

        return token

    def _exchange_code(
        self,
        issuer: str,
        client_id: str,
        redirect_uri: str,
        code_verifier: str,
        code: str,
    ) -> dict[str, Any]:
        """Troca authorization code por tokens (igual ao Codex CLI)."""
        token_url = f"{issuer}/oauth/token"

        data = (
            f"grant_type=authorization_code"
            f"&code={quote(code, safe='')}"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            f"&client_id={quote(client_id, safe='')}"
            f"&code_verifier={quote(code_verifier, safe='')}"
        )

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }

        resp = httpx.post(token_url, content=data, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _obtain_api_key(self, issuer: str, client_id: str, id_token: str) -> str | None:
        """Obtém API key via token exchange (igual Codex CLI).

        Nota: requer organization_id na conta OpenAI.
        Contas pessoais sem org podem não funcionar.
        """
        try:
            token_url = f"{issuer}/oauth/token"

            data = (
                f"grant_type={quote('urn:ietf:params:oauth:grant-type:token-exchange', safe='')}"
                f"&client_id={quote(client_id, safe='')}"
                f"&requested_token={quote('openai-api-key', safe='')}"
                f"&subject_token={quote(id_token, safe='')}"
                f"&subject_token_type={quote('urn:ietf:params:oauth:token-type:id_token', safe='')}"
            )

            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
            }

            resp = httpx.post(token_url, content=data, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json().get("access_token")
        except Exception:
            pass
        return None

    def login_device_flow(self, provider: str) -> AuthToken:
        """Login via GitHub Device Flow — sem servidor local, sem redirect.

        Fluxo:
          1. Solicita device_code ao GitHub
          2. Exibe user_code + URL para o usuário
          3. Faz polling até aprovação
          4. Para 'copilot': troca oauth token por session token Copilot
          5. Armazena token seguro

        Compatível com: github (GitHub Models via PAT) e copilot.
        """
        from rich.console import Console
        from rich.panel import Panel
        console = Console()

        config = PROVIDERS.get(provider)
        if not config or config["auth_type"] != "device_flow":
            raise ValueError(f"Provider '{provider}' nao suporta Device Flow")

        client_id   = config["client_id"]
        scope       = config["scope"]
        device_url  = config["device_code_url"]
        token_url   = config["token_url"]

        # ── 1. Solicitar device code ──────────────────────────────────────
        resp = httpx.post(
            device_url,
            data={"client_id": client_id, "scope": scope},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        device_code      = data["device_code"]
        user_code        = data["user_code"]
        verification_uri = data.get("verification_uri", "https://github.com/login/device")
        expires_in       = int(data.get("expires_in", 900))
        interval         = int(data.get("interval", 5))

        # ── 2. Mostrar instruções ─────────────────────────────────────────
        console.print()
        console.print(Panel(
            f"[bold]Autenticar com {config['name']}[/bold]\n\n"
            f"  1. Acesse: [bold cyan]{verification_uri}[/bold cyan]\n"
            f"  2. Insira o código: [bold yellow]{user_code}[/bold yellow]\n\n"
            f"  [dim]O código expira em {expires_in // 60} minutos.[/dim]",
            title="[bold]GitHub Device Flow[/bold]",
            border_style="cyan",
        ))

        # Tenta abrir browser automaticamente
        try:
            webbrowser.open(verification_uri)
            console.print("[dim]Browser aberto automaticamente...[/dim]")
        except Exception:
            pass

        console.print("\n[dim]Aguardando aprovação...[/dim]")

        # ── 3. Polling ────────────────────────────────────────────────────
        deadline = time.time() + expires_in
        github_token: str | None = None

        while time.time() < deadline:
            time.sleep(interval)
            poll = httpx.post(
                token_url,
                data={
                    "client_id":   client_id,
                    "device_code": device_code,
                    "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
                timeout=15,
            )
            poll_data = poll.json()
            error = poll_data.get("error", "")

            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval = min(interval + 5, 30)
                continue
            elif error == "expired_token":
                raise TimeoutError("Código de dispositivo expirou. Tente novamente.")
            elif error == "access_denied":
                raise PermissionError("Autenticação negada pelo usuário.")
            elif error:
                raise RuntimeError(f"Erro no device flow: {error} — {poll_data.get('error_description', '')}")
            elif "access_token" in poll_data:
                github_token = poll_data["access_token"]
                break

        if not github_token:
            raise TimeoutError("Tempo esgotado aguardando autenticação.")

        console.print("[green]✓ GitHub autorizado![/green]")

        # ── 4. Para Copilot: trocar por session token ─────────────────────
        if provider == "copilot":
            console.print("[dim]Obtendo token de sessão Copilot...[/dim]")
            copilot_data = self._exchange_copilot_token(github_token, config["copilot_token_url"])
            session_token  = copilot_data["token"]
            token_expires  = copilot_data.get("expires_at", time.time() + 1740)  # ~29 min

            token = AuthToken(
                provider=provider,
                access_token=session_token,
                api_key=session_token,
                expires_at=float(token_expires),
                api_base=config["api_base"],
                extra={
                    "github_token": github_token,   # guardado para re-exchange
                    "copilot_token_url": config["copilot_token_url"],
                },
            )
            console.print("[green]✓ Token Copilot obtido! Expira em ~30 min (auto-renovado).[/green]")
        else:
            # GitHub Models: usa o oauth token diretamente como PAT
            token = AuthToken(
                provider=provider,
                access_token=github_token,
                api_key=github_token,
                api_base=config["api_base"],
                extra={"scope": scope},
            )
            console.print("[green]✓ GitHub token salvo.[/green]")

        self.store.save(token)
        return token

    def _exchange_copilot_token(self, github_token: str, copilot_token_url: str) -> dict[str, Any]:
        """Troca GitHub OAuth token por Copilot session token.

        O Copilot session token expira em ~30 minutos e precisa ser renovado.
        O campo 'expires_at' no response é um Unix timestamp.
        """
        resp = httpx.get(
            copilot_token_url,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/json",
                "Editor-Version": "vscode/1.99.0",
                "Editor-Plugin-Version": "copilot-chat/0.26.0",
                "Copilot-Integration-Id": "vscode-chat",
                "User-Agent": "GitHubCopilotChat/0.26.0",
            },
            timeout=15,
        )
        if resp.status_code == 401:
            raise PermissionError(
                "Token GitHub sem acesso ao Copilot.\n"
                "Verifique se sua conta tem uma assinatura GitHub Copilot ativa:\n"
                "  https://github.com/features/copilot"
            )
        resp.raise_for_status()
        data = resp.json()
        if "token" not in data:
            raise RuntimeError(f"Resposta inesperada da API Copilot: {data}")
        return data

    def refresh_copilot_token(self, token: AuthToken) -> AuthToken | None:
        """Renova o Copilot session token usando o github_token guardado."""
        github_token   = token.extra.get("github_token")
        copilot_url    = token.extra.get("copilot_token_url",
                         "https://api.github.com/copilot_internal/v2/token")
        if not github_token:
            return None
        try:
            data = self._exchange_copilot_token(github_token, copilot_url)
            new_token = AuthToken(
                provider=token.provider,
                access_token=data["token"],
                api_key=data["token"],
                expires_at=float(data.get("expires_at", time.time() + 1740)),
                api_base=token.api_base,
                extra=token.extra,
            )
            self.store.save(new_token)
            return new_token
        except Exception:
            return None

    def login_api_key(
        self, provider: str, api_key: str, api_base: str | None = None
    ) -> AuthToken:
        """Login via API Key."""
        config = PROVIDERS.get(provider, PROVIDERS["custom"])

        token = AuthToken(
            provider=provider,
            access_token=api_key,
            api_key=api_key,
            api_base=api_base or config.get("api_base"),
        )

        self.store.save(token)
        return token

    def login_interactive(self, provider: str | None = None) -> AuthToken:
        """Login interativo — pergunta ao usuário."""
        # Usa Rich Console para garantir encoding correto no Windows (UTF-8, cp1252, etc.)
        from rich.console import Console
        from rich.table import Table
        _con = Console()

        if provider is None:
            table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
            table.add_column("#",  style="bold", width=4)
            table.add_column("Provider",    style="cyan",  width=12)
            table.add_column("Descricao",   style="white")
            table.add_column("Auth",        style="dim",   width=12)

            _rows = [
                ("1",  "openai",     "OpenAI (ChatGPT via Codex CLI)",      "OAuth"),
                ("2",  "openai-api", "OpenAI (API Key)",                     "API Key"),
                ("3",  "anthropic",  "Anthropic Claude",                     "API Key"),
                ("4",  "groq",       "Groq (ultra-rapido)",                  "API Key"),
                ("5",  "deepseek",   "DeepSeek (V3 / R1)",                   "API Key"),
                ("6",  "openrouter", "OpenRouter (200+ modelos)",             "API Key"),
                ("7",  "mistral",    "Mistral AI",                           "API Key"),
                ("8",  "xai",        "xAI Grok",                             "API Key"),
                ("9",  "together",   "Together AI",                          "API Key"),
                ("10", "gemini",     "Google Gemini",                        "API Key"),
                ("11", "github",     "GitHub Models (Device Flow -> PAT)",   "Device Flow"),
                ("12", "copilot",    "GitHub Copilot (Device Flow)",         "Device Flow"),
                ("13", "custom",     "Custom (OpenAI-compatible)",           "API Key"),
            ]
            for row in _rows:
                table.add_row(*row)

            _con.print()
            _con.print("[bold]Providers disponiveis:[/bold]")
            _con.print(table)

            choice = input("\nEscolha (1-13): ").strip()
            provider_map = {
                "1": "openai", "2": "openai-api", "3": "anthropic",
                "4": "groq", "5": "deepseek", "6": "openrouter",
                "7": "mistral", "8": "xai", "9": "together",
                "10": "gemini", "11": "github", "12": "copilot",
                "13": "custom",
            }
            provider = provider_map.get(choice, "openai-api")

        config = PROVIDERS.get(provider, PROVIDERS["custom"])

        if config["auth_type"] == "device_flow":
            return self.login_device_flow(provider)
        elif config["auth_type"] == "oauth":
            if provider == "openai":
                return self._login_openai_via_codex()
            return self.login_oauth(provider)
        else:
            # API Key
            env_key = config.get("env_key")
            env_value = os.environ.get(env_key, "") if env_key else ""

            if env_value:
                _con.print(f"\n[dim]Chave encontrada em {env_key}[/dim]")
                use_env = input("Usar esta chave? (s/n): ").strip().lower()
                if use_env in ("s", "sim", "y", "yes"):
                    return self.login_api_key(provider, env_value)

            api_key = input(f"\nInsira a API key para {config['name']}: ").strip()
            if not api_key:
                raise ValueError("API key nao pode ser vazia")

            api_base = None
            if provider == "custom":
                api_base = input("API base URL (ex: http://localhost:11434/v1): ").strip()

            return self.login_api_key(provider, api_key, api_base)

    def _login_openai_via_codex(self) -> AuthToken:
        """Importa token do Codex CLI se disponível."""
        codex_paths = [
            Path.home() / ".codex" / "auth.json",
            Path.home() / ".codex" / "config.toml",
        ]

        for codex_path in codex_paths:
            if codex_path.exists():
                print(f"\nCodex CLI encontrado: {codex_path}")
                use_codex = input("Importar token do Codex? (s/n): ").strip().lower()

                if use_codex in ("s", "sim", "y", "yes"):
                    return self._import_codex_token(codex_path)
                else:
                    # Usuário não quis importar, usar OAuth
                    return self.login_oauth("openai")

        # Se não encontrou Codex, usar OAuth diretamente
        return self.login_oauth("openai")

    def _import_codex_token(self, codex_path: Path) -> AuthToken:
        """Importa token do arquivo de configuração do Codex CLI.

        Nota: O token do Codex é um JWT para uso exclusivo do Codex CLI.
        Para usar a API da OpenAI diretamente, é necessária uma API key do Platform.
        """
        try:
            if codex_path.suffix == ".json":
                data = json.loads(codex_path.read_text(encoding="utf-8"))
                # Codex armazena em tokens.access_token ou tokens.accessToken
                tokens = data.get("tokens", {})
                access_token = tokens.get("access_token") or tokens.get("accessToken")
                refresh_token = tokens.get("refresh_token") or tokens.get("refreshToken")

                # Fallback: tenta raiz do objeto
                if not access_token:
                    access_token = data.get("access_token") or data.get("accessToken")
                if not refresh_token:
                    refresh_token = data.get("refresh_token") or data.get("refreshToken")
            else:
                # Formato TOML do config.toml
                # Tenta extrair manualmente (evita dependência de toml)
                content = codex_path.read_text(encoding="utf-8")
                access_token = None
                refresh_token = None
                for line in content.splitlines():
                    if "access_token" in line and "=" in line:
                        access_token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if "refresh_token" in line and "=" in line:
                        refresh_token = line.split("=", 1)[1].strip().strip('"').strip("'")

            if not access_token:
                raise ValueError("Token não encontrado no arquivo do Codex")

            # Aviso sobre limitações do token do Codex
            print("\n[!] Token do Codex CLI importado.")
            print("    Este token é para uso exclusivo do Codex CLI.")
            print("    Para usar a API da OpenAI diretamente, insira uma API key do Platform.")
            print("    Acesse: https://platform.openai.com/api-keys\n")

            token = AuthToken(
                provider="openai",
                access_token=access_token,
                refresh_token=refresh_token,
                api_base="https://api.openai.com/v1",
                extra={"source": "codex_cli", "type": "jwt"},
            )
            self.store.save(token)
            return token

        except Exception as e:
            raise ValueError(f"Erro ao importar token do Codex: {e}")

    def status(self) -> dict[str, dict[str, Any]]:
        """Mostra status de todos os providers autenticados."""
        result = {}
        for provider in self.store.list_providers():
            token = self.store.load(provider)
            if token:
                result[provider] = {
                    "provider": token.provider,
                    "type": "oauth" if token.api_key is None else "api_key",
                    "expired": token.is_expired,
                    "api_base": token.api_base,
                    "has_refresh": token.refresh_token is not None,
                }
        return result

    def logout(self, provider: str | None = None) -> bool:
        """Remove autenticação."""
        if provider:
            return self.store.delete(provider)
        # Remove todos
        for p in self.store.list_providers():
            self.store.delete(p)
        return True

    def get_client(self, provider: str) -> httpx.Client:
        """Retorna httpx.Client autenticado para o provider."""
        token = self.store.load(provider)
        if not token:
            raise ValueError(f"Nao autenticado com {provider}. Rode: bauer auth login {provider}")

        headers = {}
        if token.api_key:
            headers["Authorization"] = f"Bearer {token.api_key}"
        elif token.access_token:
            headers["Authorization"] = f"Bearer {token.access_token}"

        base_url = token.api_base or PROVIDERS.get(provider, {}).get("api_base", "")

        return httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=60,
        )

    def refresh(self, provider: str) -> AuthToken | None:
        """Tenta renovar token expirado."""
        token = self.store.load(provider)
        if not token or not token.refresh_token:
            return None

        config = PROVIDERS.get(provider, {})
        token_url = config.get("token_url")
        if not token_url:
            return None

        data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": config.get("client_id"),
        }

        try:
            resp = self._http.post(token_url, data=data)
            resp.raise_for_status()
            token_data = resp.json()

            # Preserva extra (id_token, chatgpt_account_id); re-extrai o
            # account_id se o refresh trouxer um id_token novo.
            new_extra = dict(token.extra or {})
            new_id_token = token_data.get("id_token")
            if new_id_token:
                new_extra["id_token"] = new_id_token
                _acct = _extract_chatgpt_account_id(new_id_token)
                if _acct:
                    new_extra["chatgpt_account_id"] = _acct

            new_token = AuthToken(
                provider=provider,
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token", token.refresh_token),
                expires_at=time.time() + token_data.get("expires_in", 3600),
                token_type=token_data.get("token_type", "Bearer"),
                api_base=token.api_base,
                api_key=token.api_key,
                extra=new_extra,
            )
            self.store.save(new_token)
            return new_token
        except Exception:
            return None

    def close(self) -> None:
        """Fecha conexões."""
        if self._http_client is not None:
            self._http_client.close()


# ─── Funções CLI ─────────────────────────────────────────────────────────────

def cmd_login(provider: str | None = None) -> None:
    """Comando: bauer auth login"""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    auth = AuthManager()

    try:
        token = auth.login_interactive(provider)

        console.print()
        console.print(Panel(
            f"[green]Autenticado com sucesso![/green]\n\n"
            f"Provider: [bold]{token.provider}[/bold]\n"
            f"Tipo: {'OAuth' if token.api_key is None else 'API Key'}\n"
            f"API Base: {token.api_base or 'padrao'}",
            title="Bauer Auth",
            border_style="green",
        ))

        # Atualiza config.yaml para usar o provider autenticado
        _switch_config_to_provider(token.provider)

    except Exception as e:
        console.print(f"[red]Erro na autenticacao:[/red] {e}")
    finally:
        auth.close()


def _switch_config_to_provider(provider: str) -> None:
    """Atualiza config.yaml para usar o provider autenticado."""
    from pathlib import Path
    import yaml

    config_path = Path("config.yaml")
    if not config_path.exists():
        return

    # Mapeamento provider -> config
    PROVIDER_CONFIG = {
        "openai":     {"provider": "openai",     "model": "gpt-4.1-nano",                  "context": 128000},
        "openai-api": {"provider": "openai",     "model": "gpt-4.1-nano",                  "context": 128000},
        "anthropic":  {"provider": "anthropic",  "model": "claude-3-5-haiku-20241022",      "context": 200000},
        "groq":       {"provider": "groq",       "model": "llama-3.3-70b-versatile",        "context": 128000},
        "deepseek":   {"provider": "deepseek",   "model": "deepseek-chat",                  "context": 64000},
        "openrouter": {"provider": "openrouter", "model": "openai/gpt-4.1-nano",            "context": 128000},
        "mistral":    {"provider": "mistral",    "model": "mistral-small-latest",           "context": 128000},
        "xai":        {"provider": "xai",        "model": "grok-3-mini",                    "context": 131072},
        "together":   {"provider": "together",   "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "context": 131072},
        "gemini":     {"provider": "gemini",     "model": "gemini-2.0-flash",               "context": 1048576},
        "github":     {"provider": "github",     "model": "gpt-4o-mini",                    "context": 128000},
        "copilot":    {"provider": "copilot",    "model": "gpt-4o",                         "context": 128000},
    }

    cfg = PROVIDER_CONFIG.get(provider)
    if not cfg:
        return

    # Ler config atual
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # Atualizar provider e modelo
    old_provider = config.get("model", {}).get("provider", "?")
    old_model = config.get("model", {}).get("name", "?")

    config["model"]["provider"] = cfg["provider"]
    config["model"]["name"] = cfg["model"]
    current_requested = int(config.get("model", {}).get("requested_context") or 16384)
    safe_context = min(cfg["context"], current_requested)

    config["model"]["requested_context"] = safe_context
    config["model"]["minimum_context"] = min(safe_context, 8192)
    config["model"]["think"] = False

    # Salvar
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    from rich.console import Console
    console = Console()
    console.print("\n[dim]Config atualizada:[/dim]")
    console.print(f"  Provider: {old_provider} -> [green]{cfg['provider']}[/green]")
    console.print(f"  Modelo:   {old_model} -> [green]{cfg['model']}[/green]")
    console.print(f"  Contexto: {cfg['context']} tokens")
    console.print("\n[dim]Rode 'bauer doctor' para validar.[/dim]")


def cmd_status() -> None:
    """Comando: bauer auth status"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    auth = AuthManager()

    try:
        status = auth.status()

        if not status:
            console.print("[yellow]Nenhum provider autenticado.[/yellow]")
            console.print("Use: [bold]bauer auth login[/bold]")
            return

        table = Table(title="Bauer Auth — Providers Autenticados")
        table.add_column("Provider", style="cyan")
        table.add_column("Tipo", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("API Base")

        for provider, info in status.items():
            status_text = "[red]Expirado[/red]" if info["expired"] else "[green]Ativo[/green]"
            table.add_row(
                provider,
                info["type"],
                status_text,
                info.get("api_base", "-") or "-",
            )

        console.print(table)
    finally:
        auth.close()


def cmd_logout(provider: str | None = None) -> None:
    """Comando: bauer auth logout"""
    from rich.console import Console

    console = Console()
    auth = AuthManager()

    try:
        if provider:
            if auth.logout(provider):
                console.print(f"[green]Desconectado de {provider}[/green]")
            else:
                console.print(f"[yellow]Provider {provider} nao encontrado[/yellow]")
        else:
            auth.logout()
            console.print("[green]Desconectado de todos os providers[/green]")
    finally:
        auth.close()


def cmd_list_providers() -> None:
    """Comando: bauer auth providers"""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Providers Disponíveis")

    table.add_column("ID", style="cyan")
    table.add_column("Nome", style="bold")
    table.add_column("Tipo")
    table.add_column("Exige API Key")

    for provider_id, config in PROVIDERS.items():
        table.add_row(
            provider_id,
            config["name"],
            config["auth_type"],
            "Nao (OAuth)" if config["auth_type"] == "oauth" else "Sim",
        )

    console.print(table)
