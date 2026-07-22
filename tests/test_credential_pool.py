"""Tests for G11 — CredentialPool."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.credential_pool import (
    CredentialPool,
    _cpool,
    _fernet_decrypt,
    _fernet_encrypt,
    _xor_decrypt,
    _xor_encrypt,
)


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    """Nenhum teste toca o keychain REAL do SO.

    No Linux CI o keyring não tem backend → set() cai no arquivo e os testes
    passavam. No Windows o Credential Manager EXISTE → set() ia pro keyring, o
    arquivo (credential_pool.json) nunca era escrito, e os segredos vazavam pro
    keychain real do dev; valores stale entre runs quebravam os asserts. Ou
    seja: os testes do file-layer falhavam SÓ no Windows e poluíam o SO.

    Desligar o keyring real por padrão torna a suíte determinística e hermética
    em qualquer SO. Os testes do keyring-layer re-ligam explicitamente com mocks
    nos seus próprios `with patch(...)`, que sobrepõem este autouse.
    """
    monkeypatch.setattr("bauer.credential_pool._keyring", None)
    monkeypatch.setattr("bauer.credential_pool._KEYRING_AVAILABLE", False)


# ── XOR crypto helpers ────────────────────────────────────────────────────────

class TestXorCrypto:
    def test_encrypt_decrypt_roundtrip(self):
        key = "test-key-123"
        plain = "my_secret_api_key"
        cipher = _xor_encrypt(plain, key)
        assert plain == _xor_decrypt(cipher[4:], key)  # strip "xor:" prefix

    def test_different_keys_produce_different_output(self):
        c1 = _xor_encrypt("secret", "key1")
        c2 = _xor_encrypt("secret", "key2")
        assert c1 != c2


# ── Fernet / XOR fallback ─────────────────────────────────────────────────────

class TestFernetEncrypt:
    def test_roundtrip_with_fallback(self):
        key = "pool-key-abc"
        plain = "sk-test1234567890"
        # May use Fernet or XOR depending on cryptography install
        cipher = _fernet_encrypt(plain, key)
        recovered = _fernet_decrypt(cipher, key)
        assert recovered == plain

    def test_produces_non_empty_cipher(self):
        cipher = _fernet_encrypt("secret", "key")
        assert cipher

    def test_cipher_differs_from_plain(self):
        plain = "my_api_key"
        cipher = _fernet_encrypt(plain, "enc-key")
        assert cipher != plain


# ── CredentialPool — file layer ───────────────────────────────────────────────

class TestCredentialPoolFileLayer:
    def _pool(self, tmp_path: Path) -> CredentialPool:
        return CredentialPool(base_dir=tmp_path)

    def test_get_missing_returns_fallback(self, tmp_path):
        pool = self._pool(tmp_path)
        assert pool.get("groq", fallback="default-key") == "default-key"

    def test_set_then_get_roundtrip(self, tmp_path):
        pool = self._pool(tmp_path)
        pool.set("groq", "sk-groq-secret")
        assert pool.get("groq") == "sk-groq-secret"

    def test_set_multiple_providers(self, tmp_path):
        pool = self._pool(tmp_path)
        pool.set("openai", "sk-openai")
        pool.set("anthropic", "sk-anthropic")
        assert pool.get("openai") == "sk-openai"
        assert pool.get("anthropic") == "sk-anthropic"

    def test_delete_removes_entry(self, tmp_path):
        pool = self._pool(tmp_path)
        pool.set("groq", "sk-groq")
        assert pool.delete("groq") is True
        assert pool.get("groq") == ""

    def test_delete_missing_returns_false(self, tmp_path):
        pool = self._pool(tmp_path)
        assert pool.delete("nonexistent") is False

    def test_list_providers(self, tmp_path):
        pool = self._pool(tmp_path)
        pool.set("openai", "k1")
        pool.set("groq", "k2")
        providers = pool.list_providers()
        assert "openai" in providers
        assert "groq" in providers

    def test_list_empty(self, tmp_path):
        pool = self._pool(tmp_path)
        assert pool.list_providers() == []

    def test_file_is_encrypted(self, tmp_path):
        pool = self._pool(tmp_path)
        pool.set("groq", "super-secret")
        pool_file = tmp_path / "credential_pool.json"
        raw = pool_file.read_text()
        assert "super-secret" not in raw

    def test_overwrite_existing_key(self, tmp_path):
        pool = self._pool(tmp_path)
        pool.set("groq", "old-key")
        pool.set("groq", "new-key")
        assert pool.get("groq") == "new-key"

    def test_empty_secret_not_stored(self, tmp_path):
        pool = self._pool(tmp_path)
        pool.set("groq", "")
        # Empty secret should be skipped
        assert pool.get("groq") == ""


# ── CredentialPool — keyring layer ────────────────────────────────────────────

class TestCredentialPoolKeyringLayer:
    def _pool(self, tmp_path: Path) -> CredentialPool:
        return CredentialPool(base_dir=tmp_path)

    def test_get_uses_keyring_when_available(self, tmp_path):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "keychain-secret"
        with patch("bauer.credential_pool._keyring", mock_kr), \
             patch("bauer.credential_pool._KEYRING_AVAILABLE", True):
            pool = self._pool(tmp_path)
            result = pool.get("openai")
        assert result == "keychain-secret"
        mock_kr.get_password.assert_called_once_with("bauer-agent", "openai")

    def test_get_falls_back_to_file_when_keyring_returns_none(self, tmp_path):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch("bauer.credential_pool._keyring", mock_kr), \
             patch("bauer.credential_pool._KEYRING_AVAILABLE", True):
            pool = self._pool(tmp_path)
            pool._file_set("groq", "file-secret")
            result = pool.get("groq")
        assert result == "file-secret"

    def test_set_calls_keyring_set_password(self, tmp_path):
        mock_kr = MagicMock()
        with patch("bauer.credential_pool._keyring", mock_kr), \
             patch("bauer.credential_pool._KEYRING_AVAILABLE", True):
            pool = self._pool(tmp_path)
            pool.set("groq", "my-secret")
        mock_kr.set_password.assert_called_once_with("bauer-agent", "groq", "my-secret")

    def test_set_falls_back_to_file_when_keyring_unavailable(self, tmp_path):
        with patch("bauer.credential_pool._KEYRING_AVAILABLE", False):
            pool = self._pool(tmp_path)
            pool.set("groq", "my-secret")
        # Should have written to file instead
        pool2 = CredentialPool(base_dir=tmp_path)
        assert pool2.get("groq") == "my-secret"

    def test_delete_calls_keyring_delete(self, tmp_path):
        mock_kr = MagicMock()
        with patch("bauer.credential_pool._keyring", mock_kr), \
             patch("bauer.credential_pool._KEYRING_AVAILABLE", True):
            pool = self._pool(tmp_path)
            pool._keyring_delete("groq")
        mock_kr.delete_password.assert_called_once_with("bauer-agent", "groq")

    def test_keyring_import_error_graceful(self, tmp_path):
        with patch("bauer.credential_pool._KEYRING_AVAILABLE", False):
            pool = self._pool(tmp_path)
            pool.set("openai", "sk-key")
            assert pool.get("openai") == "sk-key"


# ── _cpool singleton ──────────────────────────────────────────────────────────

class TestCpoolSingleton:
    def test_returns_credential_pool_instance(self):
        import bauer.credential_pool as cp_module
        original = cp_module._credential_pool
        try:
            cp_module._credential_pool = None
            pool = _cpool()
            assert isinstance(pool, CredentialPool)
        finally:
            cp_module._credential_pool = original

    def test_same_instance_returned_twice(self):
        import bauer.credential_pool as cp_module
        original = cp_module._credential_pool
        try:
            cp_module._credential_pool = None
            p1 = _cpool()
            p2 = _cpool()
            assert p1 is p2
        finally:
            cp_module._credential_pool = original

    def test_existing_instance_not_replaced(self):
        import bauer.credential_pool as cp_module
        original = cp_module._credential_pool
        try:
            sentinel = CredentialPool.__new__(CredentialPool)
            cp_module._credential_pool = sentinel  # type: ignore
            assert _cpool() is sentinel
        finally:
            cp_module._credential_pool = original


# ── get() fallback chain ──────────────────────────────────────────────────────

class TestGetFallbackChain:
    def test_fallback_returned_when_nothing_stored(self, tmp_path):
        with patch("bauer.credential_pool._KEYRING_AVAILABLE", False):
            pool = CredentialPool(base_dir=tmp_path)
            result = pool.get("groq", fallback="cfg-key")
        assert result == "cfg-key"

    def test_empty_string_fallback(self, tmp_path):
        with patch("bauer.credential_pool._KEYRING_AVAILABLE", False):
            pool = CredentialPool(base_dir=tmp_path)
            assert pool.get("groq") == ""

    def test_stored_key_takes_priority_over_fallback(self, tmp_path):
        with patch("bauer.credential_pool._KEYRING_AVAILABLE", False):
            pool = CredentialPool(base_dir=tmp_path)
            pool.set("groq", "stored-key")
            result = pool.get("groq", fallback="cfg-fallback")
        assert result == "stored-key"
