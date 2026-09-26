from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from bauer.web_auth import (
    AuthValidationError,
    GoogleIdentityError,
    InvalidBootstrapError,
    InvalidCredentialsError,
    SetupAlreadyCompleteError,
    WebAuthService,
    WebAuthStore,
)


BOOTSTRAP = "bootstrap-secret"
PASSWORD = "correct horse battery staple"


def _service(tmp_path, **kwargs) -> WebAuthService:
    return WebAuthService(
        WebAuthStore(tmp_path / "runtime"),
        bootstrap_key=BOOTSTRAP,
        session_hours=1,
        **kwargs,
    )


def _google_claims(sub="google-123", email="admin@example.com"):
    return {
        "aud": "client-id",
        "iss": "https://accounts.google.com",
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": "Admin Bauer",
    }


def test_local_setup_is_singleton_and_never_persists_plain_secrets(tmp_path):
    service = _service(tmp_path)

    with pytest.raises(InvalidBootstrapError):
        service.setup_local(email="admin@example.com", password=PASSWORD, bootstrap_key="wrong")

    issued = service.setup_local(
        email="Admin@Example.com", password=PASSWORD, bootstrap_key=BOOTSTRAP
    )
    assert issued.admin.email == "admin@example.com"
    assert issued.admin.password_hash.startswith("$argon2id$")
    assert service.authenticate(issued.token).admin.email == "admin@example.com"

    with pytest.raises(SetupAlreadyCompleteError):
        service.setup_local(
            email="other@example.com", password=PASSWORD, bootstrap_key=BOOTSTRAP
        )

    raw = (tmp_path / "runtime" / "web_auth.sqlite3").read_bytes()
    assert PASSWORD.encode() not in raw
    assert BOOTSTRAP.encode() not in raw
    assert issued.token.encode() not in raw
    assert issued.csrf_token.encode() not in raw


def test_login_session_csrf_logout_and_expiry(tmp_path):
    service = _service(tmp_path)
    service.setup_local(email="admin@example.com", password=PASSWORD, bootstrap_key=BOOTSTRAP)

    with pytest.raises(InvalidCredentialsError):
        service.login(email="admin@example.com", password="wrong")
    with pytest.raises(InvalidCredentialsError):
        service.login(email="missing@example.com", password="wrong")

    issued = service.login(email="ADMIN@example.com", password=PASSWORD)
    session = service.authenticate(issued.token)
    assert session is not None
    service.require_csrf(session, issued.csrf_token)
    with pytest.raises(InvalidCredentialsError):
        service.require_csrf(session, "wrong")

    assert service.logout(issued.token) is True
    assert service.authenticate(issued.token) is None

    expired = service.store.create_session(issued.admin, ttl_seconds=60, now=100)
    assert service.store.get_session(expired.token, now=159) is not None
    assert service.store.get_session(expired.token, now=160) is None


def test_session_store_keeps_only_the_twenty_most_recent_sessions(tmp_path):
    service = _service(tmp_path)
    first = service.setup_local(
        email="admin@example.com", password=PASSWORD, bootstrap_key=BOOTSTRAP
    )
    sessions = [first]
    sessions.extend(
        service.store.create_session(first.admin, ttl_seconds=3600, now=100 + index)
        for index in range(25)
    )

    with sqlite3.connect(service.store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0] == 20
    assert service.store.get_session(sessions[-1].token, now=200) is not None


def test_recovery_requires_bootstrap_and_revokes_previous_sessions(tmp_path):
    service = _service(tmp_path)
    old = service.setup_local(
        email="admin@example.com", password=PASSWORD, bootstrap_key=BOOTSTRAP
    )

    with pytest.raises(InvalidBootstrapError):
        service.recover(
            email="admin@example.com", new_password="a brand new secure password", bootstrap_key="bad"
        )

    recovered = service.recover(
        email="admin@example.com",
        new_password="a brand new secure password",
        bootstrap_key=BOOTSTRAP,
    )
    assert service.authenticate(old.token) is None
    assert service.authenticate(recovered.token) is not None
    assert service.login(
        email="admin@example.com", password="a brand new secure password"
    ).admin.id == 1


@pytest.mark.parametrize("email", ["", "not-an-email", "a@", "@example.com"])
def test_email_validation(email, tmp_path):
    service = _service(tmp_path)
    with pytest.raises(AuthValidationError):
        service.setup_local(email=email, password=PASSWORD, bootstrap_key=BOOTSTRAP)


def test_short_password_is_rejected(tmp_path):
    with pytest.raises(AuthValidationError):
        _service(tmp_path).setup_local(
            email="admin@example.com", password="short", bootstrap_key=BOOTSTRAP
        )


def test_concurrent_setup_creates_exactly_one_admin(tmp_path):
    service = _service(tmp_path)

    def attempt(index):
        try:
            service.setup_local(
                email=f"admin{index}@example.com",
                password=PASSWORD,
                bootstrap_key=BOOTSTRAP,
            )
            return "ok"
        except SetupAlreadyCompleteError:
            return "closed"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    assert results.count("ok") == 1
    assert results.count("closed") == 7
    with sqlite3.connect(service.store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin").fetchone()[0] == 1


def test_google_can_bootstrap_and_only_same_subject_can_login(tmp_path):
    claims = _google_claims()

    def verifier(_token, _client_id):
        return claims

    service = _service(
        tmp_path, google_client_id="client-id", google_verifier=verifier
    )

    with pytest.raises(InvalidBootstrapError):
        service.login_google(credential="id-token", bootstrap_key="bad")

    issued = service.login_google(credential="id-token", bootstrap_key=BOOTSTRAP)
    assert issued.admin.google_sub == "google-123"
    assert issued.admin.password_hash is None
    assert service.login_google(credential="id-token").admin.email == "admin@example.com"

    claims["sub"] = "attacker"
    with pytest.raises(InvalidCredentialsError):
        service.login_google(credential="id-token")


def test_google_claims_are_validated_even_with_injected_verifier(tmp_path):
    claims = _google_claims()
    claims["email_verified"] = False
    service = _service(
        tmp_path,
        google_client_id="client-id",
        google_verifier=lambda _token, _client_id: claims,
    )
    with pytest.raises(GoogleIdentityError):
        service.login_google(credential="id-token", bootstrap_key=BOOTSTRAP)


def test_local_admin_can_link_matching_google_account(tmp_path):
    service = _service(
        tmp_path,
        google_client_id="client-id",
        google_verifier=lambda _token, _client_id: _google_claims(),
    )
    local = service.setup_local(
        email="admin@example.com", password=PASSWORD, bootstrap_key=BOOTSTRAP
    )
    linked = service.link_google(session_token=local.token, credential="id-token")

    assert linked.admin.google_sub == "google-123"
    assert service.authenticate(local.token) is None
    assert service.login_google(credential="id-token").admin.google_sub == "google-123"


def test_google_link_rejects_different_email(tmp_path):
    service = _service(
        tmp_path,
        google_client_id="client-id",
        google_verifier=lambda _token, _client_id: _google_claims(email="other@example.com"),
    )
    local = service.setup_local(
        email="admin@example.com", password=PASSWORD, bootstrap_key=BOOTSTRAP
    )
    with pytest.raises(GoogleIdentityError):
        service.link_google(session_token=local.token, credential="id-token")
