"""G11 — Credential Pool.

Secure credential manager with three fallback layers:
  1. System keychain (Windows Credential Manager / macOS Keychain) via `keyring`
  2. Fernet-encrypted file at ~/.bauer/credential_pool.json
  3. Caller-supplied fallback (config/env value)

Usage:
    pool = CredentialPool()
    key  = pool.get("groq", fallback=cfg.groq.api_key)
    pool.set("groq", "sk-...")
    pool.delete("groq")
    providers = pool.list_providers()
"""
from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Optional keyring ──────────────────────────────────────────────────────────

try:
    import keyring as _keyring
    _KEYRING_AVAILABLE = True
except ImportError:
    _keyring = None  # type: ignore[assignment]
    _KEYRING_AVAILABLE = False


# ── Crypto helpers (reused from auth.py without importing the whole module) ───

def _get_or_create_pool_key(base_dir: Path) -> str:
    """Derive or load a stable encryption key for the credential pool file."""
    key_file = base_dir / ".pool_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(24)
    key_file.write_text(key, encoding="utf-8")
    try:
        import stat
        key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return key


def _fernet_encrypt(plain: str, key: str) -> str:
    try:
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        from cryptography.fernet import Fernet
        import base64
        salt = b"bauer-pool-v1"
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
        fkey = base64.urlsafe_b64encode(kdf.derive(key.encode()))
        return "fernet:" + Fernet(fkey).encrypt(plain.encode()).decode()
    except ImportError:
        return _xor_encrypt(plain, key)


def _fernet_decrypt(cipher: str, key: str) -> str:
    if cipher.startswith("fernet:"):
        try:
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            from cryptography.hazmat.primitives import hashes
            from cryptography.fernet import Fernet
            import base64
            salt = b"bauer-pool-v1"
            kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=100_000)
            fkey = base64.urlsafe_b64encode(kdf.derive(key.encode()))
            return Fernet(fkey).decrypt(cipher[7:].encode()).decode()
        except Exception as exc:
            raise ValueError(f"Credential pool: decryption failed: {exc}") from exc
    return _xor_decrypt(cipher, key)


def _xor_encrypt(plain: str, key: str) -> str:
    import base64
    encrypted = bytes(b ^ ord(key[i % len(key)]) for i, b in enumerate(plain.encode()))
    return "xor:" + base64.b64encode(encrypted).decode()


def _xor_decrypt(cipher: str, key: str) -> str:
    import base64
    if cipher.startswith("xor:"):
        cipher = cipher[4:]
    decoded = base64.b64decode(cipher)
    return bytes(b ^ ord(key[i % len(key)]) for i, b in enumerate(decoded)).decode()


# ── CredentialPool ────────────────────────────────────────────────────────────

class CredentialPool:
    """Three-layer secure credential manager.

    Layer order for get():  keychain → encrypted file → fallback
    Layer order for set():  keychain (preferred) → encrypted file (fallback)
    """

    _NAMESPACE = "bauer-agent"

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or (Path.home() / ".bauer")
        self._base.mkdir(parents=True, exist_ok=True)
        self._pool_file = self._base / "credential_pool.json"
        self._enc_key = _get_or_create_pool_key(self._base)

    # ── Public interface ──────────────────────────────────────────────────────

    def get(self, provider: str, *, fallback: str = "") -> str:
        """Return credential for *provider*. Never raises."""
        try:
            val = self._keyring_get(provider)
            if val:
                return val
            val = self._file_get(provider)
            if val:
                return val
        except Exception as exc:
            logger.debug("CredentialPool.get(%r): %s", provider, exc)
        return fallback

    def set(self, provider: str, secret: str) -> None:
        """Persist credential. Prefers keychain; falls back to encrypted file."""
        if not provider or not secret:
            return
        try:
            if self._keyring_set(provider, secret):
                return
        except Exception as exc:
            logger.debug("CredentialPool.set keyring failed (%r): %s", provider, exc)
        try:
            self._file_set(provider, secret)
        except Exception as exc:
            logger.debug("CredentialPool.set file failed (%r): %s", provider, exc)

    def delete(self, provider: str) -> bool:
        """Remove from all layers. Returns True if anything was deleted."""
        removed = False
        try:
            removed |= self._keyring_delete(provider)
        except Exception:
            pass
        try:
            removed |= self._file_delete(provider)
        except Exception:
            pass
        return removed

    def list_providers(self) -> list[str]:
        """List all providers with a stored credential (union of all layers)."""
        providers: set[str] = set()
        try:
            providers.update(self._keyring_list())
        except Exception:
            pass
        try:
            providers.update(self._file_list())
        except Exception:
            pass
        return sorted(providers)

    # ── Layer 1: system keychain ──────────────────────────────────────────────

    def _keyring_get(self, provider: str) -> str | None:
        if not _KEYRING_AVAILABLE or _keyring is None:
            return None
        val = _keyring.get_password(self._NAMESPACE, provider)
        return val or None

    def _keyring_set(self, provider: str, secret: str) -> bool:
        if not _KEYRING_AVAILABLE or _keyring is None:
            return False
        _keyring.set_password(self._NAMESPACE, provider, secret)
        return True

    def _keyring_delete(self, provider: str) -> bool:
        if not _KEYRING_AVAILABLE or _keyring is None:
            return False
        try:
            _keyring.delete_password(self._NAMESPACE, provider)
            return True
        except Exception:
            return False

    def _keyring_list(self) -> list[str]:
        # keyring has no generic list API — not supported without OS-specific code
        return []

    # ── Layer 2: Fernet-encrypted JSON file ───────────────────────────────────

    def _load_pool(self) -> dict[str, Any]:
        if not self._pool_file.exists():
            return {}
        try:
            return json.loads(self._pool_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_pool(self, data: dict[str, Any]) -> None:
        self._pool_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        try:
            import stat
            self._pool_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass

    def _file_get(self, provider: str) -> str | None:
        data = self._load_pool()
        cipher = data.get(provider)
        if not cipher:
            return None
        return _fernet_decrypt(cipher, self._enc_key)

    def _file_set(self, provider: str, secret: str) -> None:
        data = self._load_pool()
        data[provider] = _fernet_encrypt(secret, self._enc_key)
        self._save_pool(data)

    def _file_delete(self, provider: str) -> bool:
        data = self._load_pool()
        if provider not in data:
            return False
        del data[provider]
        self._save_pool(data)
        return True

    def _file_list(self) -> list[str]:
        return list(self._load_pool().keys())


# ── Module-level singleton ────────────────────────────────────────────────────

_credential_pool: CredentialPool | None = None


def _cpool() -> CredentialPool:
    """Return the process-wide CredentialPool singleton."""
    global _credential_pool
    if _credential_pool is None:
        _credential_pool = CredentialPool()
    return _credential_pool
