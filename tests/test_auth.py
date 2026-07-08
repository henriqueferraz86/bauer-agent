"""Testes para bauer/auth.py — helpers crypto, AuthToken, TokenStore, AuthManager."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.auth import (
    PROVIDERS,
    AuthManager,
    AuthToken,
    TokenStore,
    _decrypt_token,
    _encrypt_token,
    _generate_pkce,
    _OAuthCallbackHandler,
    OAuthCallbackServer,
)


# ─── _generate_pkce ──────────────────────────────────────────────────────────


def test_generate_pkce_returns_two_strings():
    verifier, challenge = _generate_pkce()
    assert isinstance(verifier, str)
    assert isinstance(challenge, str)


def test_generate_pkce_verifier_and_challenge_differ():
    verifier, challenge = _generate_pkce()
    assert verifier != challenge


def test_generate_pkce_challenge_is_base64():
    _, challenge = _generate_pkce()
    import base64
    # Challenge deve ser base64url sem padding
    try:
        base64.urlsafe_b64decode(challenge + "===")
        ok = True
    except Exception:
        ok = False
    assert ok


# ─── _encrypt_token / _decrypt_token ─────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    token = "sk-test-secret-token"
    key = "myobfuscationkey"
    encrypted = _encrypt_token(token, key)
    assert encrypted != token
    decrypted = _decrypt_token(encrypted, key)
    assert decrypted == token


def test_encrypt_no_key_passthrough():
    token = "plaintext"
    result = _encrypt_token(token, key="")
    assert result == token


def test_decrypt_no_key_passthrough():
    encrypted = "plaintext"
    result = _decrypt_token(encrypted, key="")
    assert result == encrypted


def test_encrypt_different_keys_different_results():
    token = "mytoken"
    enc1 = _encrypt_token(token, "key1")
    enc2 = _encrypt_token(token, "key2")
    assert enc1 != enc2


# ─── AuthToken ───────────────────────────────────────────────────────────────


def test_auth_token_not_expired_by_default():
    token = AuthToken(provider="openai", access_token="tok123")
    assert token.is_expired is False


def test_auth_token_expired_when_past():
    token = AuthToken(provider="openai", access_token="tok123", expires_at=time.time() - 10)
    assert token.is_expired is True


def test_auth_token_not_expired_when_future():
    token = AuthToken(provider="openai", access_token="tok123", expires_at=time.time() + 3600)
    assert token.is_expired is False


def test_auth_token_to_dict():
    token = AuthToken(
        provider="anthropic",
        access_token="sk-ant-xxx",
        api_key="sk-ant-xxx",
        api_base="https://api.anthropic.com",
    )
    d = token.to_dict()
    assert d["provider"] == "anthropic"
    assert d["access_token"] == "sk-ant-xxx"
    assert d["api_base"] == "https://api.anthropic.com"


def test_auth_token_from_dict_roundtrip():
    original = AuthToken(
        provider="groq",
        access_token="gsk_xxx",
        refresh_token="ref_xxx",
        expires_at=time.time() + 3600,
        api_key="gsk_xxx",
    )
    restored = AuthToken.from_dict(original.to_dict())
    assert restored.provider == original.provider
    assert restored.access_token == original.access_token
    assert restored.api_key == original.api_key


# ─── TokenStore ──────────────────────────────────────────────────────────────


def _make_store(tmp_path: Path) -> TokenStore:
    return TokenStore(base_dir=tmp_path / "auth")


def test_token_store_save_and_load(tmp_path: Path):
    store = _make_store(tmp_path)
    token = AuthToken(provider="openai-api", access_token="sk-test", api_key="sk-test")
    store.save(token)
    loaded = store.load("openai-api")
    assert loaded is not None
    assert loaded.access_token == "sk-test"
    assert loaded.api_key == "sk-test"


def test_token_store_load_not_found(tmp_path: Path):
    store = _make_store(tmp_path)
    assert store.load("nao-existe") is None


def test_token_store_load_all_empty(tmp_path: Path):
    store = _make_store(tmp_path)
    assert store.load_all() == {}


def test_token_store_load_all_returns_all(tmp_path: Path):
    store = _make_store(tmp_path)
    store.save(AuthToken(provider="anthropic", access_token="ant-xxx"))
    store.save(AuthToken(provider="groq", access_token="gsk-xxx"))
    all_tokens = store.load_all()
    assert "anthropic" in all_tokens
    assert "groq" in all_tokens


def test_token_store_delete_existing(tmp_path: Path):
    store = _make_store(tmp_path)
    store.save(AuthToken(provider="deepseek", access_token="ds-xxx"))
    assert store.delete("deepseek") is True
    assert store.load("deepseek") is None


def test_token_store_delete_not_found(tmp_path: Path):
    store = _make_store(tmp_path)
    assert store.delete("nao-existe") is False


def test_token_store_list_providers(tmp_path: Path):
    store = _make_store(tmp_path)
    store.save(AuthToken(provider="openai-api", access_token="sk-1"))
    store.save(AuthToken(provider="groq", access_token="gsk-1"))
    providers = store.list_providers()
    assert "openai-api" in providers
    assert "groq" in providers


def test_token_store_overwrites_existing(tmp_path: Path):
    store = _make_store(tmp_path)
    store.save(AuthToken(provider="anthropic", access_token="old"))
    store.save(AuthToken(provider="anthropic", access_token="new"))
    loaded = store.load("anthropic")
    assert loaded.access_token == "new"


def test_token_store_corrupt_file_returns_empty(tmp_path: Path):
    store = _make_store(tmp_path)
    store.tokens_file.write_text("NAO E JSON VALIDO", encoding="utf-8")
    assert store.load_all() == {}


def test_token_store_save_with_refresh_token(tmp_path: Path):
    store = _make_store(tmp_path)
    token = AuthToken(
        provider="openai",
        access_token="acc-xxx",
        refresh_token="ref-xxx",
    )
    store.save(token)
    loaded = store.load("openai")
    assert loaded.refresh_token == "ref-xxx"


# ─── PROVIDERS ────────────────────────────────────────────────────────────────


def test_providers_have_required_keys():
    for name, cfg in PROVIDERS.items():
        assert "name" in cfg, f"Provider '{name}' sem 'name'"
        assert "auth_type" in cfg, f"Provider '{name}' sem 'auth_type'"


def test_providers_auth_type_valid():
    valid_types = {"oauth", "api_key", "device_flow"}
    for name, cfg in PROVIDERS.items():
        assert cfg["auth_type"] in valid_types, f"Provider '{name}' tem auth_type inválido"


# ─── AuthManager ──────────────────────────────────────────────────────────────


def _make_manager(tmp_path: Path) -> AuthManager:
    manager = AuthManager(base_dir=tmp_path / "auth")
    return manager


def test_auth_manager_login_api_key(tmp_path: Path):
    manager = _make_manager(tmp_path)
    token = manager.login_api_key("anthropic", "sk-ant-xxx")
    assert token.provider == "anthropic"
    assert token.api_key == "sk-ant-xxx"


def test_auth_manager_login_api_key_custom_base(tmp_path: Path):
    manager = _make_manager(tmp_path)
    token = manager.login_api_key("custom", "mykey", api_base="http://localhost:8000/v1")
    assert token.api_base == "http://localhost:8000/v1"


def test_auth_manager_login_api_key_saves_token(tmp_path: Path):
    manager = _make_manager(tmp_path)
    manager.login_api_key("groq", "gsk-xxx")
    loaded = manager.store.load("groq")
    assert loaded is not None
    assert loaded.api_key == "gsk-xxx"


def test_auth_manager_status_empty(tmp_path: Path):
    manager = _make_manager(tmp_path)
    assert manager.status() == {}


def test_auth_manager_status_with_providers(tmp_path: Path):
    manager = _make_manager(tmp_path)
    manager.login_api_key("anthropic", "sk-ant-xxx")
    manager.login_api_key("groq", "gsk-xxx")
    status = manager.status()
    assert "anthropic" in status
    assert "groq" in status
    assert status["anthropic"]["type"] == "api_key"


def test_auth_manager_logout_specific_provider(tmp_path: Path):
    manager = _make_manager(tmp_path)
    manager.login_api_key("anthropic", "sk-xxx")
    result = manager.logout("anthropic")
    assert result is True
    assert manager.store.load("anthropic") is None


def test_auth_manager_logout_all(tmp_path: Path):
    manager = _make_manager(tmp_path)
    manager.login_api_key("anthropic", "sk-xxx")
    manager.login_api_key("groq", "gsk-xxx")
    result = manager.logout()
    assert result is True
    assert manager.status() == {}


def test_auth_manager_get_client_not_authenticated(tmp_path: Path):
    manager = _make_manager(tmp_path)
    with pytest.raises(ValueError, match="Nao autenticado"):
        manager.get_client("anthropic")


def test_auth_manager_get_client_with_api_key(tmp_path: Path):
    manager = _make_manager(tmp_path)
    manager.login_api_key("groq", "gsk-test", api_base="https://api.groq.com/openai/v1")
    client = manager.get_client("groq")
    assert client is not None


def test_auth_manager_get_client_with_access_token_no_api_key(tmp_path: Path):
    """AuthToken sem api_key usa access_token."""
    manager = _make_manager(tmp_path)
    token = AuthToken(
        provider="openai",
        access_token="acc-tok-xxx",
        api_base="https://api.openai.com/v1",
    )
    manager.store.save(token)
    client = manager.get_client("openai")
    assert client is not None


def test_auth_manager_refresh_no_token(tmp_path: Path):
    manager = _make_manager(tmp_path)
    result = manager.refresh("anthropic")
    assert result is None


def test_auth_manager_refresh_no_refresh_token(tmp_path: Path):
    manager = _make_manager(tmp_path)
    manager.login_api_key("groq", "gsk-xxx")  # api key, sem refresh_token
    result = manager.refresh("groq")
    assert result is None


def test_auth_manager_refresh_no_token_url(tmp_path: Path):
    """Provider sem token_url (api_key providers) retorna None."""
    manager = _make_manager(tmp_path)
    token = AuthToken(provider="anthropic", access_token="acc", refresh_token="ref")
    manager.store.save(token)
    # anthropic não tem token_url no PROVIDERS (é api_key)
    result = manager.refresh("anthropic")
    assert result is None


def test_auth_manager_refresh_oauth_provider(tmp_path: Path):
    """Para provider com token_url, tenta refresh."""
    manager = _make_manager(tmp_path)
    # openai tem token_url
    token = AuthToken(
        provider="openai",
        access_token="old-acc",
        refresh_token="old-ref",
        api_base="https://api.openai.com/v1",
    )
    manager.store.save(token)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "access_token": "new-acc",
        "refresh_token": "new-ref",
        "expires_in": 3600,
    }
    manager._http.post = MagicMock(return_value=mock_resp)

    new_token = manager.refresh("openai")
    assert new_token is not None
    assert new_token.access_token == "new-acc"


def test_import_codex_token_json(tmp_path: Path):
    """_import_codex_token lê JSON do Codex CLI."""
    import json
    manager = _make_manager(tmp_path)

    codex_path = tmp_path / "auth.json"
    codex_path.write_text(json.dumps({
        "tokens": {"access_token": "codex-jwt-xxx", "refresh_token": "ref-xxx"}
    }), encoding="utf-8")

    token = manager._import_codex_token(codex_path)
    assert token.access_token == "codex-jwt-xxx"
    assert token.provider == "openai"


def test_import_codex_token_toml(tmp_path: Path):
    """_import_codex_token lê TOML básico."""
    manager = _make_manager(tmp_path)
    codex_path = tmp_path / "config.toml"
    codex_path.write_text('access_token = "toml-jwt-xxx"\nrefresh_token = "toml-ref"\n', encoding="utf-8")

    token = manager._import_codex_token(codex_path)
    assert token.access_token == "toml-jwt-xxx"


def test_import_codex_token_no_access_token_raises(tmp_path: Path):
    """Se não encontrar access_token, levanta ValueError."""
    import json
    manager = _make_manager(tmp_path)
    codex_path = tmp_path / "auth.json"
    codex_path.write_text(json.dumps({"tokens": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="Codex"):
        manager._import_codex_token(codex_path)


# ─── Segurança: sem fallback XOR silencioso ───────────────────────────────────


def test_try_get_fernet_raises_on_import_error(monkeypatch):
    """_try_get_fernet deve propagar ImportError com mensagem clara."""
    from unittest.mock import patch
    import bauer.auth as auth_module

    with patch("bauer.auth._try_get_fernet", side_effect=ImportError("cryptography ausente")):
        with pytest.raises(ImportError, match="cryptography"):
            auth_module._encrypt_token("sk-test", "key")


def test_encrypt_token_uses_fernet_prefix():
    """_encrypt_token sempre usa Fernet (prefixo 'fernet:') quando cryptography disponível."""
    from bauer.auth import _FERNET_PREFIX
    encrypted = _encrypt_token("sk-test", "some-key")
    assert encrypted.startswith(_FERNET_PREFIX), (
        f"Token encriptado deve ter prefixo '{_FERNET_PREFIX}', mas obteve: {encrypted[:20]}"
    )
