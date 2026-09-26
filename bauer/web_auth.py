"""Autenticação do frontend do ``bauer serve``.

O browser recebe uma sessão opaca em cookie HttpOnly. Somente hashes de
sessão/CSRF e hashes Argon2id de senha são persistidos. A API key existente é
usada apenas como credencial de bootstrap/recuperação e continua disponível
para clientes não-browser.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .logging_config import log_suppressed


_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,253}$")
_GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
_MAX_GOOGLE_TOKEN_CHARS = 16_384


class WebAuthError(Exception):
    """Erro esperado e seguro para apresentar pela camada HTTP."""

    code = "auth_error"


class AuthValidationError(WebAuthError):
    code = "validation_error"


class InvalidBootstrapError(WebAuthError):
    code = "invalid_bootstrap"


class InvalidCredentialsError(WebAuthError):
    code = "invalid_credentials"


class SetupAlreadyCompleteError(WebAuthError):
    code = "setup_already_complete"


class SetupRequiredError(WebAuthError):
    code = "setup_required"


class GoogleNotConfiguredError(WebAuthError):
    code = "google_not_configured"


class GoogleIdentityError(WebAuthError):
    code = "invalid_google_identity"


@dataclass(frozen=True)
class AdminRecord:
    id: int
    email: str
    password_hash: str | None
    google_sub: str | None
    display_name: str
    created_at: int
    updated_at: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "display_name": self.display_name,
            "has_password": bool(self.password_hash),
            "google_linked": bool(self.google_sub),
        }


@dataclass(frozen=True)
class SessionCredentials:
    token: str
    csrf_token: str
    expires_at: int
    admin: AdminRecord


@dataclass(frozen=True)
class AuthenticatedSession:
    token_hash: str
    csrf_hash: str
    expires_at: int
    admin: AdminRecord


def normalize_email(value: str) -> str:
    email = (value or "").strip().casefold()
    if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
        raise AuthValidationError("E-mail inválido.")
    return email


def validate_password(value: str) -> str:
    password = value or ""
    if len(password) < 12:
        raise AuthValidationError("A senha precisa ter pelo menos 12 caracteres.")
    if len(password) > 1024:
        raise AuthValidationError("A senha excede o limite permitido.")
    return password


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class PasswordService:
    """Hash/verify Argon2id com o piso recomendado pelo OWASP."""

    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=2,
            memory_cost=19_456,
            parallelism=1,
            hash_len=32,
            salt_len=16,
        )
        # Equaliza aproximadamente o caminho de e-mail inexistente no login.
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        return self._hasher.hash(validate_password(password))

    def verify(self, encoded: str | None, password: str) -> bool:
        candidate = encoded or self._dummy_hash
        try:
            return bool(self._hasher.verify(candidate, password or "")) and encoded is not None
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    def needs_rehash(self, encoded: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(encoded)
        except (InvalidHashError, VerificationError):
            return True


_DDL = """
CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT,
    google_sub TEXT UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CHECK (password_hash IS NOT NULL OR google_sub IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash TEXT PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES admin(id) ON DELETE CASCADE,
    csrf_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_expires
    ON web_sessions(expires_at);
"""


class WebAuthStore:
    """Store SQLite single-admin com writers serializados."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "web_auth.sqlite3"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path), isolation_level=None, timeout=10.0, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)
        try:
            os.chmod(self.path, 0o600)
        except OSError as exc:
            log_suppressed("web_auth.chmod_store", exc)

    @staticmethod
    def _admin(row: sqlite3.Row | None) -> AdminRecord | None:
        if row is None:
            return None
        return AdminRecord(
            id=int(row["id"]),
            email=str(row["email"]),
            password_hash=row["password_hash"],
            google_sub=row["google_sub"],
            display_name=str(row["display_name"] or ""),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def get_admin(self) -> AdminRecord | None:
        with self._connect() as conn:
            return self._admin(conn.execute("SELECT * FROM admin WHERE id=1").fetchone())

    def get_admin_by_email(self, email: str) -> AdminRecord | None:
        normalized = normalize_email(email)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM admin WHERE email=?", (normalized,)).fetchone()
            return self._admin(row)

    def create_admin(
        self,
        *,
        email: str,
        password_hash: str | None,
        google_sub: str | None = None,
        display_name: str = "",
        now: int | None = None,
    ) -> AdminRecord:
        normalized = normalize_email(email)
        if not password_hash and not google_sub:
            raise AuthValidationError("Administrador precisa de senha ou identidade Google.")
        timestamp = int(time.time() if now is None else now)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM admin WHERE id=1").fetchone():
                raise SetupAlreadyCompleteError("O administrador já foi cadastrado.")
            conn.execute(
                """INSERT INTO admin
                   (id, email, password_hash, google_sub, display_name, created_at, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?)""",
                (normalized, password_hash, google_sub, display_name.strip(), timestamp, timestamp),
            )
            conn.commit()
        except SetupAlreadyCompleteError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise SetupAlreadyCompleteError("O administrador já foi cadastrado.") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        admin = self.get_admin()
        if admin is None:  # pragma: no cover - defesa contra corrupção externa
            raise WebAuthError("Cadastro não foi persistido.")
        return admin

    def update_password(self, password_hash: str, *, now: int | None = None) -> AdminRecord:
        timestamp = int(time.time() if now is None else now)
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE admin SET password_hash=?, updated_at=? WHERE id=1",
                (password_hash, timestamp),
            )
            if cursor.rowcount != 1:
                raise SetupRequiredError("Cadastre o administrador primeiro.")
        admin = self.get_admin()
        assert admin is not None
        return admin

    def link_google(
        self, google_sub: str, display_name: str, *, now: int | None = None
    ) -> AdminRecord:
        subject = (google_sub or "").strip()
        if not subject:
            raise GoogleIdentityError("Identidade Google inválida.")
        timestamp = int(time.time() if now is None else now)
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE admin SET google_sub=?, display_name=?, updated_at=?
                   WHERE id=1 AND (google_sub IS NULL OR google_sub=?)""",
                (subject, display_name.strip(), timestamp, subject),
            )
            if cursor.rowcount != 1:
                raise GoogleIdentityError("Conta Google diferente da vinculada.")
        admin = self.get_admin()
        assert admin is not None
        return admin

    def create_session(
        self, admin: AdminRecord, *, ttl_seconds: int, now: int | None = None
    ) -> SessionCredentials:
        timestamp = int(time.time() if now is None else now)
        expires_at = timestamp + max(60, int(ttl_seconds))
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            conn.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (timestamp,))
            conn.execute(
                """INSERT INTO web_sessions
                   (token_hash, admin_id, csrf_hash, created_at, expires_at, last_seen_at)
                   VALUES (?, 1, ?, ?, ?, ?)""",
                (_digest(token), _digest(csrf_token), timestamp, expires_at, timestamp),
            )
            conn.execute(
                """DELETE FROM web_sessions
                   WHERE admin_id=1 AND token_hash NOT IN (
                       SELECT token_hash FROM web_sessions
                       WHERE admin_id=1
                       ORDER BY created_at DESC, rowid DESC
                       LIMIT 20
                   )"""
            )
        return SessionCredentials(token, csrf_token, expires_at, admin)

    def get_session(
        self, token: str, *, now: int | None = None, touch: bool = True
    ) -> AuthenticatedSession | None:
        if not token:
            return None
        timestamp = int(time.time() if now is None else now)
        token_hash = _digest(token)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT s.token_hash, s.csrf_hash, s.expires_at,
                          a.id, a.email, a.password_hash, a.google_sub,
                          a.display_name, a.created_at, a.updated_at
                   FROM web_sessions s JOIN admin a ON a.id=s.admin_id
                   WHERE s.token_hash=?""",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if int(row["expires_at"]) <= timestamp:
                conn.execute("DELETE FROM web_sessions WHERE token_hash=?", (token_hash,))
                return None
            if touch:
                conn.execute(
                    "UPDATE web_sessions SET last_seen_at=? WHERE token_hash=?",
                    (timestamp, token_hash),
                )
            admin = self._admin(row)
            assert admin is not None
            return AuthenticatedSession(
                token_hash=token_hash,
                csrf_hash=str(row["csrf_hash"]),
                expires_at=int(row["expires_at"]),
                admin=admin,
            )

    def verify_csrf(self, session: AuthenticatedSession, csrf_token: str) -> bool:
        return bool(csrf_token) and hmac.compare_digest(
            session.csrf_hash, _digest(csrf_token)
        )

    def revoke_session(self, token: str) -> bool:
        if not token:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM web_sessions WHERE token_hash=?", (_digest(token),)
            )
            return cursor.rowcount > 0

    def revoke_all_sessions(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM web_sessions")
            return cursor.rowcount


GoogleVerifier = Callable[[str, str], dict[str, Any]]


def verify_google_id_token(token: str, client_id: str) -> dict[str, Any]:
    """Valida Google ID token usando a biblioteca oficial, carregada sob demanda."""
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    return dict(id_token.verify_oauth2_token(token, Request(), client_id))


class WebAuthService:
    def __init__(
        self,
        store: WebAuthStore,
        *,
        bootstrap_key: str,
        session_hours: int = 168,
        google_client_id: str = "",
        google_verifier: GoogleVerifier | None = None,
        passwords: PasswordService | None = None,
    ) -> None:
        self.store = store
        self._bootstrap_key = bootstrap_key or ""
        self._session_seconds = max(1, int(session_hours)) * 3600
        self.google_client_id = (google_client_id or "").strip()
        self._google_verifier = google_verifier or verify_google_id_token
        self.passwords = passwords or PasswordService()

    @property
    def setup_required(self) -> bool:
        return self.store.get_admin() is None

    def _verify_bootstrap(self, value: str) -> None:
        if not self._bootstrap_key or not hmac.compare_digest(
            (value or "").strip(), self._bootstrap_key
        ):
            raise InvalidBootstrapError("Credencial de bootstrap inválida.")

    def _issue(self, admin: AdminRecord) -> SessionCredentials:
        return self.store.create_session(admin, ttl_seconds=self._session_seconds)

    def setup_local(
        self, *, email: str, password: str, bootstrap_key: str
    ) -> SessionCredentials:
        self._verify_bootstrap(bootstrap_key)
        admin = self.store.create_admin(
            email=email,
            password_hash=self.passwords.hash(password),
        )
        return self._issue(admin)

    def login(self, *, email: str, password: str) -> SessionCredentials:
        try:
            normalized = normalize_email(email)
        except AuthValidationError:
            normalized = "invalid@example.invalid"
        admin = self.store.get_admin()
        encoded = admin.password_hash if admin and admin.email == normalized else None
        if not self.passwords.verify(encoded, password):
            raise InvalidCredentialsError("E-mail ou senha inválidos.")
        assert admin is not None and encoded is not None
        if self.passwords.needs_rehash(encoded):
            admin = self.store.update_password(self.passwords.hash(password))
        return self._issue(admin)

    def recover(
        self, *, email: str, new_password: str, bootstrap_key: str
    ) -> SessionCredentials:
        self._verify_bootstrap(bootstrap_key)
        admin = self.store.get_admin()
        try:
            matches = admin is not None and hmac.compare_digest(
                admin.email, normalize_email(email)
            )
        except AuthValidationError:
            matches = False
        if not matches:
            raise InvalidCredentialsError("Não foi possível recuperar a conta.")
        admin = self.store.update_password(self.passwords.hash(new_password))
        self.store.revoke_all_sessions()
        return self._issue(admin)

    def authenticate(self, token: str) -> AuthenticatedSession | None:
        return self.store.get_session(token)

    def require_csrf(self, session: AuthenticatedSession, csrf_token: str) -> None:
        if not self.store.verify_csrf(session, csrf_token):
            raise InvalidCredentialsError("Token CSRF inválido ou ausente.")

    def logout(self, token: str) -> bool:
        return self.store.revoke_session(token)

    def _google_claims(self, credential: str) -> tuple[str, str, str]:
        if not self.google_client_id:
            raise GoogleNotConfiguredError("Login com Google não está configurado.")
        token = (credential or "").strip()
        if not token or len(token) > _MAX_GOOGLE_TOKEN_CHARS:
            raise GoogleIdentityError("Credencial Google inválida.")
        try:
            claims = self._google_verifier(token, self.google_client_id)
        except Exception as exc:
            raise GoogleIdentityError("Credencial Google inválida.") from exc
        if claims.get("aud") != self.google_client_id:
            raise GoogleIdentityError("Credencial Google destinada a outro cliente.")
        if claims.get("iss") not in _GOOGLE_ISSUERS:
            raise GoogleIdentityError("Emissor Google inválido.")
        if claims.get("email_verified") is not True:
            raise GoogleIdentityError("O Google não confirmou este e-mail.")
        subject = str(claims.get("sub") or "").strip()
        if not subject:
            raise GoogleIdentityError("Identidade Google sem subject.")
        email = normalize_email(str(claims.get("email") or ""))
        name = str(claims.get("name") or "").strip()[:200]
        return subject, email, name

    def login_google(
        self, *, credential: str, bootstrap_key: str = ""
    ) -> SessionCredentials:
        subject, email, name = self._google_claims(credential)
        admin = self.store.get_admin()
        if admin is None:
            self._verify_bootstrap(bootstrap_key)
            admin = self.store.create_admin(
                email=email,
                password_hash=None,
                google_sub=subject,
                display_name=name,
            )
        elif not admin.google_sub or not hmac.compare_digest(admin.google_sub, subject):
            raise InvalidCredentialsError("Conta Google não vinculada.")
        return self._issue(admin)

    def link_google(
        self, *, session_token: str, credential: str
    ) -> SessionCredentials:
        session = self.authenticate(session_token)
        if session is None:
            raise InvalidCredentialsError("Sessão inválida.")
        subject, email, name = self._google_claims(credential)
        if not hmac.compare_digest(session.admin.email, email):
            raise GoogleIdentityError("Use a conta Google com o mesmo e-mail do administrador.")
        admin = self.store.link_google(subject, name)
        self.store.revoke_all_sessions()
        return self._issue(admin)
