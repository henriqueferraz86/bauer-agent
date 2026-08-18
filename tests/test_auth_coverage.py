"""Testes para bauer/auth.py — AuthManager, TokenStore, OAuth."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from bauer.auth import (
    AuthAborted,
    AuthToken,
    AuthManager,
    TokenStore,
    OAuthCallbackServer,
    _OAuthCallbackHandler,
    _encrypt_token,
    _decrypt_token,
    _generate_pkce,
    cmd_login,
    cmd_logout,
    cmd_status,
    cmd_status_all_sources,
    cmd_list_providers,
    _switch_config_to_provider,
    _parse_pasted_callback,
    _safe_print,
    browser_available,
    is_wsl,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_token(provider: str = "openai-api", api_key: str = "sk-test") -> AuthToken:
    return AuthToken(
        provider=provider,
        access_token="access-tok",
        api_key=api_key,
        api_base="https://api.openai.com/v1",
    )


def _make_manager(tmp_path: Path) -> AuthManager:
    mgr = AuthManager(base_dir=tmp_path / "auth")
    return mgr


# ─── Helpers unitários ───────────────────────────────────────────────────────

class TestEncryptDecrypt:
    def test_encrypt_decrypt_roundtrip(self):
        token = "sk-test-12345"
        key = "mykey"
        encrypted = _encrypt_token(token, key)
        assert encrypted != token
        decrypted = _decrypt_token(encrypted, key)
        assert decrypted == token

    def test_empty_key_returns_original(self):
        token = "sk-test"
        assert _encrypt_token(token, "") == token
        assert _decrypt_token(token, "") == token


class TestGeneratePkce:
    def test_returns_pair(self):
        verifier, challenge = _generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert verifier != challenge

    def test_unique_per_call(self):
        v1, c1 = _generate_pkce()
        v2, c2 = _generate_pkce()
        assert v1 != v2


# ─── AuthToken ───────────────────────────────────────────────────────────────

class TestAuthToken:
    def test_is_not_expired_without_expires_at(self):
        token = AuthToken(provider="test", access_token="tok")
        assert token.is_expired is False

    def test_is_expired_when_past(self):
        token = AuthToken(provider="test", access_token="tok", expires_at=time.time() - 3600)
        assert token.is_expired is True

    def test_is_not_expired_when_future(self):
        token = AuthToken(provider="test", access_token="tok", expires_at=time.time() + 3600)
        assert token.is_expired is False

    def test_to_dict_contains_fields(self):
        token = _make_token()
        d = token.to_dict()
        assert d["provider"] == "openai-api"
        assert d["access_token"] == "access-tok"

    def test_from_dict_roundtrip(self):
        token = _make_token()
        d = token.to_dict()
        token2 = AuthToken.from_dict(d)
        assert token2.provider == token.provider
        assert token2.access_token == token.access_token


# ─── TokenStore ──────────────────────────────────────────────────────────────

class TestTokenStore:
    def test_save_and_load(self, tmp_path: Path):
        store = TokenStore(tmp_path / "auth")
        token = _make_token("openai-api", "sk-my-key")
        store.save(token)
        loaded = store.load("openai-api")
        assert loaded is not None
        assert loaded.provider == "openai-api"
        assert loaded.api_key == "sk-my-key"

    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        store = TokenStore(tmp_path / "auth")
        assert store.load("nonexistent") is None

    def test_list_providers(self, tmp_path: Path):
        store = TokenStore(tmp_path / "auth")
        store.save(_make_token("openai-api"))
        store.save(_make_token("anthropic"))
        providers = store.list_providers()
        assert "openai-api" in providers
        assert "anthropic" in providers

    def test_delete_existing(self, tmp_path: Path):
        store = TokenStore(tmp_path / "auth")
        store.save(_make_token("openai-api"))
        assert store.delete("openai-api") is True
        assert store.load("openai-api") is None

    def test_delete_nonexistent_returns_false(self, tmp_path: Path):
        store = TokenStore(tmp_path / "auth")
        assert store.delete("nonexistent") is False

    def test_load_all_empty(self, tmp_path: Path):
        store = TokenStore(tmp_path / "auth")
        assert store.load_all() == {}

    def test_load_all_corrupt_file(self, tmp_path: Path):
        auth_dir = tmp_path / "auth"
        auth_dir.mkdir()
        (auth_dir / "auth.json").write_text("not valid json", encoding="utf-8")
        store = TokenStore(auth_dir)
        assert store.load_all() == {}

    def test_save_token_with_refresh_token(self, tmp_path: Path):
        store = TokenStore(tmp_path / "auth")
        token = AuthToken(
            provider="openai",
            access_token="at-123",
            refresh_token="rt-456",
        )
        store.save(token)
        loaded = store.load("openai")
        assert loaded is not None
        assert loaded.refresh_token == "rt-456"


# ─── AuthManager.login_api_key ───────────────────────────────────────────────

class TestAuthManagerLoginApiKey:
    def test_saves_and_returns_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        token = mgr.login_api_key("anthropic", "sk-ant-key")
        assert token.provider == "anthropic"
        assert token.api_key == "sk-ant-key"

    def test_custom_provider_with_api_base(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        token = mgr.login_api_key("custom", "my-key", "http://localhost:11434/v1")
        assert token.api_base == "http://localhost:11434/v1"


# ─── AuthManager.status / logout / get_client ────────────────────────────────

class TestAuthManagerStatus:
    def test_status_empty(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        assert mgr.status() == {}

    def test_status_with_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mgr.login_api_key("anthropic", "sk-test")
        status = mgr.status()
        assert "anthropic" in status
        assert status["anthropic"]["type"] == "api_key"

    def test_status_oauth_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        # OAuth token has no api_key
        token = AuthToken(provider="openai", access_token="jwt-token")
        mgr.store.save(token)
        status = mgr.status()
        assert status["openai"]["type"] == "oauth"


class TestAuthManagerLogout:
    def test_logout_specific_provider(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mgr.login_api_key("anthropic", "sk-test")
        assert mgr.logout("anthropic") is True
        assert mgr.status() == {}

    def test_logout_all(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mgr.login_api_key("anthropic", "sk1")
        mgr.login_api_key("groq", "sk2")
        mgr.logout()
        assert mgr.status() == {}


class TestAuthManagerGetClient:
    def test_get_client_not_authenticated_raises(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="Nao autenticado"):
            mgr.get_client("anthropic")

    def test_get_client_with_api_key(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mgr.login_api_key("anthropic", "sk-test")
        client = mgr.get_client("anthropic")
        assert client is not None

    def test_get_client_with_oauth_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        token = AuthToken(provider="openai", access_token="jwt-token")
        mgr.store.save(token)
        client = mgr.get_client("openai")
        assert client is not None


class TestAuthManagerClose:
    def test_close_does_not_raise(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mgr.close()  # should not raise


# ─── AuthManager.refresh ─────────────────────────────────────────────────────

class TestAuthManagerRefresh:
    def test_refresh_returns_none_when_no_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        result = mgr.refresh("nonexistent")
        assert result is None

    def test_refresh_returns_none_when_no_refresh_token(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        token = AuthToken(provider="openai-api", access_token="at", refresh_token=None)
        mgr.store.save(token)
        result = mgr.refresh("openai-api")
        assert result is None

    def test_refresh_returns_none_for_provider_without_token_url(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        # custom provider has no token_url
        token = AuthToken(provider="custom", access_token="at", refresh_token="rt")
        mgr.store.save(token)
        result = mgr.refresh("custom")
        assert result is None

    def test_refresh_success_with_mock(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        token = AuthToken(
            provider="openai",
            access_token="old-at",
            refresh_token="rt-123",
            expires_at=time.time() - 100,
        )
        mgr.store.save(token)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "access_token": "new-at",
            "refresh_token": "new-rt",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        mgr._http.post = MagicMock(return_value=mock_resp)

        result = mgr.refresh("openai")
        assert result is not None
        assert result.access_token == "new-at"

    def test_refresh_exception_returns_none(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        token = AuthToken(
            provider="openai",
            access_token="old-at",
            refresh_token="rt-123",
        )
        mgr.store.save(token)
        mgr._http.post = MagicMock(side_effect=Exception("network error"))
        result = mgr.refresh("openai")
        assert result is None


# ─── AuthManager._exchange_code ──────────────────────────────────────────────

class TestExchangeCode:
    def test_exchange_code_success(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "access_token": "at-token",
            "refresh_token": "rt-token",
            "expires_in": 3600,
        }
        with patch("httpx.post", return_value=mock_resp):
            result = mgr._exchange_code(
                issuer="https://auth.openai.com",
                client_id="client-123",
                redirect_uri="http://localhost:1455/auth/callback",
                code_verifier="verifier",
                code="auth-code-123",
            )
        assert result["access_token"] == "at-token"

    def test_exchange_code_http_error_raises(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 401")
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(Exception):
                mgr._exchange_code(
                    issuer="https://auth.openai.com",
                    client_id="client-123",
                    redirect_uri="http://localhost:1455/auth/callback",
                    code_verifier="verifier",
                    code="bad-code",
                )


# ─── AuthManager._obtain_api_key ─────────────────────────────────────────────

class TestObtainApiKey:
    def test_returns_api_key_on_success(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "sk-api-key-123"}
        with patch("httpx.post", return_value=mock_resp):
            result = mgr._obtain_api_key(
                issuer="https://auth.openai.com",
                client_id="client-123",
                id_token="jwt-id-token",
            )
        assert result == "sk-api-key-123"

    def test_returns_none_on_non_200(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("httpx.post", return_value=mock_resp):
            result = mgr._obtain_api_key(
                issuer="https://auth.openai.com",
                client_id="client-123",
                id_token="jwt-id-token",
            )
        assert result is None

    def test_returns_none_on_exception(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        with patch("httpx.post", side_effect=Exception("network down")):
            result = mgr._obtain_api_key(
                issuer="https://auth.openai.com",
                client_id="client-123",
                id_token="jwt",
            )
        assert result is None


# ─── AuthManager.login_interactive ───────────────────────────────────────────

class TestLoginInteractive:
    @pytest.fixture(autouse=True)
    def _clean_provider_envs(self, monkeypatch):
        """Remove provider API keys do env para evitar o prompt 'Usar esta chave?'."""
        for key in (
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
            "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "XAI_API_KEY",
            "TOGETHER_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY",
            "GITHUB_TOKEN", "COPILOT_TOKEN",
        ):
            monkeypatch.delenv(key, raising=False)
    def test_api_key_choice_via_number(self, tmp_path: Path):
        """Escolha '2' (openai-api) com API key."""
        mgr = _make_manager(tmp_path)
        inputs = iter(["2", "sk-my-key"])
        with patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            token = mgr.login_interactive()
        assert token.api_key == "sk-my-key"
        assert token.provider == "openai-api"

    def test_anthropic_choice(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        inputs = iter(["3", "sk-ant-key"])
        with patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            token = mgr.login_interactive()
        assert token.provider == "anthropic"

    def test_groq_choice(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        inputs = iter(["4", "gsk-key"])
        with patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            token = mgr.login_interactive()
        assert token.provider == "groq"

    def test_custom_choice(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        # Custom is option 13 in the current 13-item menu
        inputs = iter(["13", "my-api-key", "http://localhost:11434/v1"])
        with patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            token = mgr.login_interactive()
        assert token.provider == "custom"
        assert token.api_base == "http://localhost:11434/v1"

    def test_empty_api_key_raises(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        inputs = iter(["2", ""])
        with patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            with pytest.raises(ValueError, match="API key nao pode ser vazia"):
                mgr.login_interactive()

    def test_uses_env_key_when_available(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        inputs = iter(["2", "s"])  # choice + "s" = usar env key
        with patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-from-env"}):
            token = mgr.login_interactive()
        assert token.api_key == "sk-from-env"

    def test_provider_passed_directly_skips_menu(self, tmp_path: Path):
        """Quando provider é passado, não mostra menu."""
        mgr = _make_manager(tmp_path)
        inputs = iter(["sk-direct-key"])
        with patch("builtins.input", side_effect=lambda *a, **kw: next(inputs)):
            token = mgr.login_interactive(provider="anthropic")
        assert token.provider == "anthropic"


# ─── AuthManager._import_codex_token ─────────────────────────────────────────

class TestImportCodexToken:
    def test_import_from_json_file(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        codex_file = tmp_path / "auth.json"
        codex_data = {
            "tokens": {
                "access_token": "at-from-codex",
                "refresh_token": "rt-from-codex",
            }
        }
        codex_file.write_text(json.dumps(codex_data), encoding="utf-8")
        token = mgr._import_codex_token(codex_file)
        assert token.access_token == "at-from-codex"
        assert token.provider == "openai"

    def test_import_fallback_root_fields(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        codex_file = tmp_path / "auth.json"
        # access_token at root level
        codex_data = {"access_token": "at-root"}
        codex_file.write_text(json.dumps(codex_data), encoding="utf-8")
        token = mgr._import_codex_token(codex_file)
        assert token.access_token == "at-root"

    def test_import_from_toml_file(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        toml_file = tmp_path / "config.toml"
        toml_content = 'access_token = "at-from-toml"\nrefresh_token = "rt-from-toml"\n'
        toml_file.write_text(toml_content, encoding="utf-8")
        token = mgr._import_codex_token(toml_file)
        assert token.access_token == "at-from-toml"

    def test_missing_token_raises(self, tmp_path: Path):
        mgr = _make_manager(tmp_path)
        codex_file = tmp_path / "auth.json"
        codex_file.write_text(json.dumps({"no_tokens": True}), encoding="utf-8")
        with pytest.raises(ValueError, match="Erro ao importar"):
            mgr._import_codex_token(codex_file)


# ─── AuthManager._login_openai_via_codex ─────────────────────────────────────

class TestLoginOpenAIViaCodex:
    def test_no_codex_file_falls_through_to_oauth(self, tmp_path: Path):
        """Sem arquivo Codex, cai para login_oauth."""
        mgr = _make_manager(tmp_path)
        mock_token = _make_token("openai")
        with patch.object(mgr, "login_oauth", return_value=mock_token) as mock_oauth, \
             patch("pathlib.Path.home", return_value=tmp_path / "nonexistent"):
            result = mgr._login_openai_via_codex()
        mock_oauth.assert_called_once_with("openai", no_browser=None)

    def test_codex_file_found_user_says_yes(self, tmp_path: Path):
        """Arquivo encontrado, usuário aceita importar."""
        mgr = _make_manager(tmp_path)
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        auth_file = codex_dir / "auth.json"
        auth_file.write_text(json.dumps({"tokens": {"access_token": "at"}}), encoding="utf-8")

        mock_token = _make_token("openai")
        with patch("pathlib.Path.home", return_value=tmp_path), \
             patch("builtins.input", return_value="s"), \
             patch.object(mgr, "_import_codex_token", return_value=mock_token) as mock_import:
            result = mgr._login_openai_via_codex()
        mock_import.assert_called_once()

    def test_codex_file_found_user_says_no(self, tmp_path: Path):
        """Arquivo encontrado, usuário recusa → usa OAuth."""
        mgr = _make_manager(tmp_path)
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        auth_file = codex_dir / "auth.json"
        auth_file.write_text(json.dumps({"tokens": {"access_token": "at"}}), encoding="utf-8")

        mock_token = _make_token("openai")
        with patch("pathlib.Path.home", return_value=tmp_path), \
             patch("builtins.input", return_value="n"), \
             patch.object(mgr, "login_oauth", return_value=mock_token) as mock_oauth:
            result = mgr._login_openai_via_codex()
        mock_oauth.assert_called_once_with("openai", no_browser=None)


# ─── OAuthCallbackServer.wait_for_code ───────────────────────────────────────

class TestWaitForCode:
    def test_returns_code_when_set(self):
        server = OAuthCallbackServer(port=19998)
        _OAuthCallbackHandler.auth_code = "test-code-123"
        _OAuthCallbackHandler.state = "test-state-456"

        code, state = server.wait_for_code(timeout=5)

        assert code == "test-code-123"
        assert state == "test-state-456"
        # Should clear class vars
        assert _OAuthCallbackHandler.auth_code is None
        assert _OAuthCallbackHandler.state is None

    def test_returns_none_on_timeout(self):
        server = OAuthCallbackServer(port=19999)
        _OAuthCallbackHandler.auth_code = None
        _OAuthCallbackHandler.state = None

        # Small timeout to not block tests
        code, state = server.wait_for_code(timeout=0.05)
        assert code is None
        assert state is None


# ─── Saída em console legado ─────────────────────────────────────────────────

class TestSafePrint:
    """Console pt-BR do Windows é cp850: nao tem '✓' nem em-dash."""

    @staticmethod
    def _legacy_stdout(monkeypatch, encoding: str = "cp850"):
        import io
        import sys as _sys

        buf = io.BytesIO()
        stream = io.TextIOWrapper(buf, encoding=encoding, errors="strict")
        monkeypatch.setattr(_sys, "stdout", stream)
        return buf, stream

    def test_checkmark_does_not_crash_legacy_console(self, monkeypatch):
        """Regressao: este print roda DEPOIS do token salvo — crashar aqui
        derruba um login que ja tinha dado certo."""
        buf, stream = self._legacy_stdout(monkeypatch)

        _safe_print("[✓] API key de sessao obtida via token exchange.")

        stream.flush()
        out = buf.getvalue().decode("cp850")
        assert "[ok]" in out
        assert "API key de sessao obtida" in out

    def test_em_dash_downgraded(self, monkeypatch):
        buf, stream = self._legacy_stdout(monkeypatch)

        _safe_print("Usando access_token OAuth — requer billing")

        stream.flush()
        assert "OAuth - requer billing" in buf.getvalue().decode("cp850")

    def test_accents_survive_when_encoding_allows(self, monkeypatch):
        """cp850 tem 'ã' — nao pode virar '?' so porque houve fallback."""
        buf, stream = self._legacy_stdout(monkeypatch)

        _safe_print("[✓] API key de sessão obtida.")

        stream.flush()
        assert "sessão" in buf.getvalue().decode("cp850")

    def test_utf8_console_keeps_original(self, monkeypatch):
        buf, stream = self._legacy_stdout(monkeypatch, encoding="utf-8")

        _safe_print("[✓] tudo certo — mesmo texto")

        stream.flush()
        assert "[✓] tudo certo — mesmo texto" in buf.getvalue().decode("utf-8")

    def test_raw_print_would_have_crashed(self, monkeypatch):
        """Prova que o cenario e real, nao teatro de teste."""
        buf, stream = self._legacy_stdout(monkeypatch)
        with pytest.raises(UnicodeEncodeError):
            print("[✓] isto derruba o login")

    def test_no_raw_print_left_in_auth_module(self):
        """Qualquer print() cru novo no fluxo de auth reintroduz o bug."""
        import ast
        from pathlib import Path as _Path

        import bauer.auth as auth_mod

        tree = ast.parse(_Path(auth_mod.__file__).read_text(encoding="utf-8"))

        # O único lugar onde print() cru é legítimo é dentro do _safe_print.
        allowed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_safe_print":
                allowed = set(range(node.lineno, (node.end_lineno or node.lineno) + 1))

        assert allowed, "_safe_print sumiu do módulo"

        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
            and node.lineno not in allowed
        ]
        assert offenders == [], f"use _safe_print nas linhas: {offenders}"


# ─── Fluxo headless (--no-browser) ───────────────────────────────────────────

class TestParsePastedCallback:
    def test_full_callback_url(self):
        url = "http://localhost:1455/auth/callback?code=abc123&state=xyz789"
        assert _parse_pasted_callback(url) == ("abc123", "xyz789")

    def test_url_with_quotes_and_spaces(self):
        url = '  "http://localhost:1455/auth/callback?code=abc123&state=xyz789"  '
        assert _parse_pasted_callback(url) == ("abc123", "xyz789")

    def test_bare_code_has_no_state(self):
        assert _parse_pasted_callback("abc123") == ("abc123", None)

    def test_query_string_only(self):
        assert _parse_pasted_callback("?code=abc123&state=s") == ("abc123", "s")

    def test_empty_returns_none(self):
        assert _parse_pasted_callback("   ") == (None, None)

    def test_prose_is_not_a_code(self):
        assert _parse_pasted_callback("nao consegui abrir") == (None, None)

    def test_url_without_code_returns_none(self):
        assert _parse_pasted_callback("http://localhost:1455/auth/callback?error=denied") == (None, None)


class TestBrowserAvailable:
    @staticmethod
    def _clean_env(monkeypatch):
        for var in ("BAUER_NO_BROWSER", "BROWSER", "DISPLAY", "WAYLAND_DISPLAY",
                    "WSL_DISTRO_NAME", "WSL_INTEROP"):
            monkeypatch.delenv(var, raising=False)

    def test_env_override_disables(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setenv("BAUER_NO_BROWSER", "1")
        assert browser_available() is False

    def test_windows_always_true(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setattr("bauer.auth.sys.platform", "win32")
        assert browser_available() is True

    def test_headless_linux_is_false(self, monkeypatch):
        """Host via SSH sem X/Wayland — o caso do xdg-open que nao acha browser."""
        self._clean_env(monkeypatch)
        monkeypatch.setattr("bauer.auth.sys.platform", "linux")
        monkeypatch.setattr("bauer.auth.is_wsl", lambda: False)
        assert browser_available() is False

    def test_linux_with_display_and_browser_is_true(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setattr("bauer.auth.sys.platform", "linux")
        monkeypatch.setattr("bauer.auth.is_wsl", lambda: False)
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setattr(
            "bauer.auth.shutil.which",
            lambda n: "/usr/bin/firefox" if n == "firefox" else None,
        )
        assert browser_available() is True

    def test_ssh_x_forwarding_without_browser_is_false(self, monkeypatch):
        """REGRESSÃO: `ssh -X` num servidor headless preenche o DISPLAY e nao ha
        browser nenhum. Tratar DISPLAY como prova suficiente travou o login."""
        self._clean_env(monkeypatch)
        monkeypatch.setattr("bauer.auth.sys.platform", "linux")
        monkeypatch.setattr("bauer.auth.is_wsl", lambda: False)
        monkeypatch.setenv("DISPLAY", "localhost:10.0")
        monkeypatch.setattr("bauer.auth.shutil.which", lambda n: None)
        assert browser_available() is False

    def test_sensible_browser_alone_does_not_count(self, monkeypatch):
        """/usr/bin/sensible-browser existe em toda Ubuntu e so delega —
        conta-lo reintroduz o bug (era o unico presente no Beelink)."""
        self._clean_env(monkeypatch)
        monkeypatch.setattr("bauer.auth.sys.platform", "linux")
        monkeypatch.setattr("bauer.auth.is_wsl", lambda: False)
        monkeypatch.setenv("DISPLAY", "localhost:10.0")
        monkeypatch.setattr(
            "bauer.auth.shutil.which",
            lambda n: "/usr/bin/sensible-browser" if n == "sensible-browser" else None,
        )
        assert browser_available() is False

    def test_xdg_open_alone_does_not_count(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setattr("bauer.auth.sys.platform", "linux")
        monkeypatch.setattr("bauer.auth.is_wsl", lambda: False)
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setattr(
            "bauer.auth.shutil.which",
            lambda n: "/usr/bin/xdg-open" if n == "xdg-open" else None,
        )
        assert browser_available() is False

    def test_wsl_with_wslview_is_true(self, monkeypatch):
        self._clean_env(monkeypatch)
        monkeypatch.setattr("bauer.auth.sys.platform", "linux")
        monkeypatch.setattr("bauer.auth.is_wsl", lambda: True)
        monkeypatch.setattr("bauer.auth.shutil.which", lambda name: "/usr/bin/wslview")
        assert browser_available() is True

    def test_is_wsl_reads_proc_version(self, monkeypatch):
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.delenv("WSL_INTEROP", raising=False)
        monkeypatch.setattr(
            "bauer.auth.Path.read_text",
            lambda self, *a, **kw: "Linux version 5.15 microsoft-standard-WSL2",
        )
        assert is_wsl() is True


class TestWaitForPastedCode:
    def setup_method(self):
        _OAuthCallbackHandler.auth_code = None
        _OAuthCallbackHandler.state = None

    def test_accepts_pasted_url(self, monkeypatch):
        server = OAuthCallbackServer(port=19997)
        monkeypatch.setattr(
            "builtins.input",
            lambda _prompt="": "http://localhost:1455/auth/callback?code=pasted&state=st",
        )
        assert server.wait_for_pasted_code(timeout=5) == ("pasted", "st")

    def test_server_code_wins_without_input(self, monkeypatch):
        """Quem tem tunel SSH recebe o callback direto — nem chega a perguntar."""
        server = OAuthCallbackServer(port=19997)
        _OAuthCallbackHandler.auth_code = "from-tunnel"
        _OAuthCallbackHandler.state = "st"
        monkeypatch.setattr(
            "builtins.input", lambda _prompt="": pytest.fail("nao deveria pedir colagem")
        )
        assert server.wait_for_pasted_code(timeout=5) == ("from-tunnel", "st")

    def test_retries_on_garbage_then_accepts(self, monkeypatch):
        server = OAuthCallbackServer(port=19997)
        answers = iter(["isso nao e um code", "?code=ok&state=st"])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
        assert server.wait_for_pasted_code(timeout=5) == ("ok", "st")

    def test_eof_raises_aborted_not_timeout(self, monkeypatch):
        """stdin fechado nao e timeout — dizer 'tempo esgotado' manda a pessoa
        procurar problema de rede que nao existe."""
        server = OAuthCallbackServer(port=19997)

        def _raise(_prompt=""):
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise)
        with pytest.raises(AuthAborted, match="entrada padrao fechada"):
            server.wait_for_pasted_code(timeout=5)

    def test_ctrl_c_raises_aborted(self, monkeypatch):
        server = OAuthCallbackServer(port=19997)

        def _raise(_prompt=""):
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _raise)
        with pytest.raises(AuthAborted, match="cancelado pelo usuario"):
            server.wait_for_pasted_code(timeout=5)

    def test_aborted_is_not_confused_with_timeout(self):
        assert not issubclass(AuthAborted, TimeoutError)


class TestWaitForCodeHint:
    def setup_method(self):
        _OAuthCallbackHandler.auth_code = None
        _OAuthCallbackHandler.state = None

    def test_hint_printed_when_callback_never_arrives(self, capsys):
        """Browser que 'abriu' mas nao abriu: em vez de congelar, orienta."""
        server = OAuthCallbackServer(port=19323)

        code, _ = server.wait_for_code(timeout=0.4, hint_after=0.1)

        assert code is None
        out = capsys.readouterr().out
        assert "--no-browser" in out

    def test_no_hint_when_callback_arrives(self, capsys):
        server = OAuthCallbackServer(port=19323)
        _OAuthCallbackHandler.auth_code = "chegou"
        _OAuthCallbackHandler.state = "st"

        code, _ = server.wait_for_code(timeout=5, hint_after=0.1)

        assert code == "chegou"
        assert "--no-browser" not in capsys.readouterr().out

    def test_hint_printed_only_once(self, capsys):
        server = OAuthCallbackServer(port=19323)

        server.wait_for_code(timeout=0.6, hint_after=0.05)

        assert capsys.readouterr().out.count("--no-browser") == 1

    def test_silent_by_default(self, capsys):
        """Sem hint_after o comportamento antigo continua identico."""
        server = OAuthCallbackServer(port=19323)

        server.wait_for_code(timeout=0.2)

        assert capsys.readouterr().out == ""


class TestServerReleasesPort:
    def test_stop_closes_socket(self):
        """shutdown() sozinho encerra o loop mas deixa a porta escutando."""
        server = OAuthCallbackServer(port=19321)
        server.start()
        sock = server.server.socket

        server.stop()

        assert sock.fileno() == -1, "server_close() nao foi chamado — porta presa"
        assert server.server is None

    def test_stop_is_idempotent(self):
        server = OAuthCallbackServer(port=19322)
        server.start()
        server.stop()
        server.stop()   # nao pode travar nem estourar

    def test_abort_at_prompt_still_releases_port(self, tmp_path: Path, monkeypatch):
        """Ctrl-C no prompt nao pode deixar a porta presa para a retentativa."""
        mgr = _make_manager(tmp_path)
        stopped: list[bool] = []

        class _RecordingServer(_FakeServer):
            def wait_for_pasted_code(self, timeout: int = 300, prompt=None):
                raise AuthAborted("Login cancelado pelo usuario.")

            def stop(self) -> None:
                stopped.append(True)

        monkeypatch.setattr("bauer.auth.OAuthCallbackServer", _RecordingServer)

        with pytest.raises(AuthAborted):
            mgr.login_oauth("openai", no_browser=True)

        assert stopped == [True], "server.stop() pulado quando o login aborta"

    def test_timeout_returns_none(self, monkeypatch):
        server = OAuthCallbackServer(port=19997)
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        # Enter vazio abre janela de 2s para o callback; timeout curto corta o loop.
        assert server.wait_for_pasted_code(timeout=0.01) == (None, None)


class _FakeServer:
    """Servidor que não faz bind — só devolve o que o teste mandar."""

    def __init__(self, port: int):
        self.port = port
        self.stopped = False
        self.started = False

    def start(self) -> None:
        self.started = True

    def try_start(self) -> bool:
        self.started = True
        return False

    def stop(self) -> None:
        self.stopped = True

    def wait_for_pasted_code(self, timeout: int = 300, prompt=None):
        return "pasted-code", None

    def wait_for_code(self, timeout: int = 300):
        raise AssertionError("modo headless nao deve esperar callback")


class TestLoginOAuthHeadless:
    def test_no_browser_pastes_code_and_never_opens_browser(self, tmp_path: Path, monkeypatch):
        mgr = _make_manager(tmp_path)
        monkeypatch.setattr("bauer.auth.OAuthCallbackServer", _FakeServer)
        monkeypatch.setattr(
            "bauer.auth.open_browser",
            lambda url: pytest.fail("--no-browser nao pode tentar abrir browser"),
        )
        monkeypatch.setattr(
            mgr, "_exchange_code",
            lambda **kw: {"access_token": "at-headless", "expires_in": 3600},
        )
        monkeypatch.setattr(mgr, "_obtain_api_key", lambda *a, **kw: None)

        token = mgr.login_oauth("openai", no_browser=True)

        assert token.access_token == "at-headless"
        assert mgr.store.load("openai") is not None

    def test_autodetect_headless_when_no_browser(self, tmp_path: Path, monkeypatch):
        """Sem flag: browser_available() False já leva ao fluxo de colagem."""
        mgr = _make_manager(tmp_path)
        monkeypatch.setattr("bauer.auth.OAuthCallbackServer", _FakeServer)
        monkeypatch.setattr("bauer.auth.browser_available", lambda: False)
        monkeypatch.setattr(
            "bauer.auth.open_browser", lambda url: pytest.fail("nao deve abrir browser")
        )
        monkeypatch.setattr(
            mgr, "_exchange_code",
            lambda **kw: {"access_token": "at-auto", "expires_in": 3600},
        )
        monkeypatch.setattr(mgr, "_obtain_api_key", lambda *a, **kw: None)

        assert mgr.login_oauth("openai").access_token == "at-auto"

    def test_state_mismatch_still_raises_when_state_present(self, tmp_path: Path, monkeypatch):
        class _MismatchServer(_FakeServer):
            def wait_for_pasted_code(self, timeout: int = 300, prompt=None):
                return "code", "state-de-outra-sessao"

        mgr = _make_manager(tmp_path)
        monkeypatch.setattr("bauer.auth.OAuthCallbackServer", _MismatchServer)
        monkeypatch.setattr("bauer.auth.open_browser", lambda url: True)

        with pytest.raises(ValueError, match="State mismatch"):
            mgr.login_oauth("openai", no_browser=True)

    def test_browser_path_falls_back_to_paste_when_open_fails(
        self, tmp_path: Path, monkeypatch
    ):
        """open_browser() False nao pode deixar o login esperando 300s a toa."""
        mgr = _make_manager(tmp_path)
        monkeypatch.setattr("bauer.auth.OAuthCallbackServer", _FakeServer)
        monkeypatch.setattr("bauer.auth.open_browser", lambda url: False)
        monkeypatch.setattr(
            mgr, "_exchange_code",
            lambda **kw: {"access_token": "at-fallback", "expires_in": 3600},
        )

        # _FakeServer.wait_for_code levanta se for chamado — o fallback tem que
        # ir para wait_for_pasted_code.
        assert mgr.login_oauth("openai", no_browser=False).access_token == "at-fallback"

    def test_timeout_raises(self, tmp_path: Path, monkeypatch):
        class _EmptyServer(_FakeServer):
            def wait_for_pasted_code(self, timeout: int = 300, prompt=None):
                return None, None

        mgr = _make_manager(tmp_path)
        monkeypatch.setattr("bauer.auth.OAuthCallbackServer", _EmptyServer)

        with pytest.raises(TimeoutError):
            mgr.login_oauth("openai", no_browser=True)


# ─── cmd_* functions ─────────────────────────────────────────────────────────

class TestCmdLogin:
    def test_cmd_login_success(self, tmp_path: Path):
        mock_token = _make_token("anthropic")
        mock_auth = MagicMock()
        mock_auth.login_interactive.return_value = mock_token

        with patch("bauer.auth.AuthManager", return_value=mock_auth), \
             patch("bauer.auth._switch_config_to_provider") as mock_switch:
            cmd_login("anthropic")

        mock_auth.login_interactive.assert_called_once_with("anthropic", no_browser=None)
        mock_switch.assert_called_once_with("anthropic")
        mock_auth.close.assert_called_once()

    def test_cmd_login_abort_is_not_reported_as_error(self, tmp_path: Path, capsys):
        mock_auth = MagicMock()
        mock_auth.login_interactive.side_effect = AuthAborted("Login cancelado pelo usuario.")

        with patch("bauer.auth.AuthManager", return_value=mock_auth):
            cmd_login("openai")

        out = capsys.readouterr().out
        assert "Login cancelado" in out
        assert "Erro na autenticacao" not in out
        mock_auth.close.assert_called_once()

    def test_cmd_login_exception_handled(self, tmp_path: Path):
        mock_auth = MagicMock()
        mock_auth.login_interactive.side_effect = RuntimeError("auth failed")

        with patch("bauer.auth.AuthManager", return_value=mock_auth):
            cmd_login()  # Should not raise

        mock_auth.close.assert_called_once()


class TestCmdStatus:
    def test_cmd_status_empty(self):
        mock_auth = MagicMock()
        mock_auth.status.return_value = {}

        with patch("bauer.auth.AuthManager", return_value=mock_auth):
            cmd_status()  # Should not raise

        mock_auth.close.assert_called_once()

    def test_cmd_status_with_providers(self):
        mock_auth = MagicMock()
        mock_auth.status.return_value = {
            "anthropic": {
                "provider": "anthropic",
                "type": "api_key",
                "expired": False,
                "api_base": "https://api.anthropic.com",
                "has_refresh": False,
            }
        }
        with patch("bauer.auth.AuthManager", return_value=mock_auth):
            cmd_status()

        mock_auth.close.assert_called_once()


class TestCmdStatusAllSources:
    """bauer auth status --all-sources (Plano 041) — proveniencia, sem imprimir segredo."""

    def test_no_providers_configured_anywhere(self):
        mock_auth = MagicMock()
        mock_auth.store.list_providers.return_value = []
        mock_pool = MagicMock()
        mock_pool.list_providers.return_value = []

        with patch("bauer.auth.AuthManager", return_value=mock_auth), \
             patch("bauer.credential_pool._cpool", return_value=mock_pool):
            cmd_status_all_sources()  # não deve lançar

        mock_auth.close.assert_called_once()

    def test_shows_provenance_bauer_auth_wins(self):
        """Provider com token nao-JWT em bauer auth: fonte em uso = bauer auth,
        mesmo que bauer credential tambem tenha valor (precedencia real de
        _build_client)."""
        mock_auth = MagicMock()
        mock_auth.store.list_providers.return_value = ["groq"]
        mock_auth.store.load.return_value = AuthToken(
            provider="groq", access_token="", api_key="gsk-xxx",
        )
        mock_pool = MagicMock()
        mock_pool.list_providers.return_value = ["groq"]
        mock_pool.get.return_value = "pool-value"

        with patch("bauer.auth.AuthManager", return_value=mock_auth), \
             patch("bauer.credential_pool._cpool", return_value=mock_pool):
            cmd_status_all_sources()

        mock_auth.close.assert_called_once()

    def test_jwt_token_falls_through_to_credential(self):
        """Token JWT (Codex) nao serve como API key — fonte em uso deve cair
        para bauer credential, nao bauer auth."""
        mock_auth = MagicMock()
        mock_auth.store.list_providers.return_value = ["openai-api"]
        mock_auth.store.load.return_value = AuthToken(
            provider="openai-api", access_token="jwt-token",
            extra={"type": "jwt"},
        )
        mock_pool = MagicMock()
        mock_pool.list_providers.return_value = []
        mock_pool.get.return_value = "pool-value"

        with patch("bauer.auth.AuthManager", return_value=mock_auth), \
             patch("bauer.credential_pool._cpool", return_value=mock_pool):
            cmd_status_all_sources()

        mock_auth.close.assert_called_once()


class TestCmdLogout:
    def test_cmd_logout_specific_provider(self):
        mock_auth = MagicMock()
        mock_auth.logout.return_value = True

        with patch("bauer.auth.AuthManager", return_value=mock_auth):
            cmd_logout("anthropic")

        mock_auth.logout.assert_called_once_with("anthropic")
        mock_auth.close.assert_called_once()

    def test_cmd_logout_not_found(self):
        mock_auth = MagicMock()
        mock_auth.logout.return_value = False

        with patch("bauer.auth.AuthManager", return_value=mock_auth):
            cmd_logout("nonexistent")

        mock_auth.close.assert_called_once()

    def test_cmd_logout_all(self):
        mock_auth = MagicMock()
        mock_auth.logout.return_value = True

        with patch("bauer.auth.AuthManager", return_value=mock_auth):
            cmd_logout()

        mock_auth.logout.assert_called_once_with()
        mock_auth.close.assert_called_once()


class TestCmdListProviders:
    def test_cmd_list_providers_runs(self):
        cmd_list_providers()  # Should not raise


# ─── _switch_config_to_provider ──────────────────────────────────────────────

class TestSwitchConfigToProvider:
    def test_returns_if_no_config_file(self, tmp_path: Path):
        # No config.yaml exists in tmp_path, function returns early
        import os
        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            _switch_config_to_provider("anthropic")  # Should not raise
        finally:
            os.chdir(original_cwd)

    def test_updates_config_yaml(self, tmp_path: Path):
        import yaml
        import os

        config_data = {
            "model": {
                "provider": "ollama",
                "name": "phi4-mini",
                "requested_context": 4096,
                "minimum_context": 512,
            }
        }
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            _switch_config_to_provider("anthropic")
            # Read back
            with open(config_file, "r", encoding="utf-8") as f:
                updated = yaml.safe_load(f)
            assert updated["model"]["provider"] == "anthropic"
        finally:
            os.chdir(original_cwd)

    def test_returns_early_for_unknown_provider(self, tmp_path: Path):
        import yaml
        import os

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"model": {"provider": "ollama", "name": "phi4-mini"}}))

        original_cwd = Path.cwd()
        os.chdir(tmp_path)
        try:
            _switch_config_to_provider("unknown-provider")  # Should not raise
        finally:
            os.chdir(original_cwd)
