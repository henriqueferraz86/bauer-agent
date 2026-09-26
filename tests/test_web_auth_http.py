from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi", reason="requer bauer-agent[server]")

from fastapi.testclient import TestClient  # noqa: E402

from bauer.server import create_app  # noqa: E402
from bauer.tool_router import ToolRouter  # noqa: E402


API_KEY = "server-bootstrap-key"
PASSWORD = "correct horse battery staple"


def _client(
    tmp_path: Path,
    *,
    api_key: str = API_KEY,
    secure: bool = False,
    web_auth_enabled: bool = True,
) -> TestClient:
    llm = MagicMock()
    llm.host = "http://localhost:11434"
    llm._provider = "ollama"
    llm.list_models.return_value = ["qwen3:0.6b"]
    router = ToolRouter(workspace=tmp_path / "workspace")
    app = create_app(
        model_name="qwen3:0.6b",
        applied_context=4096,
        router=router,
        client=llm,
        system_prompt="test",
        sessions_dir=tmp_path / "sessions",
        api_key=api_key,
        web_auth_enabled=web_auth_enabled,
        auth_cookie_secure=secure,
        rate_limit_requests=0,
        workspace=tmp_path / "workspace",
    )
    return TestClient(app)


def _setup(client: TestClient):
    return client.post(
        "/auth/setup",
        json={
            "email": "admin@example.com",
            "password": PASSWORD,
            "bootstrap_key": API_KEY,
        },
    )


def test_web_auth_is_inert_without_server_api_key(tmp_path):
    client = _client(tmp_path, api_key="")
    assert client.get("/auth/state").json() == {
        "enabled": False,
        "api_key_required": False,
        "setup_required": False,
        "authenticated": False,
        "google_enabled": False,
        "google_client_id": "",
        "user": None,
    }
    assert client.get("/status").status_code == 200


def test_web_auth_can_be_disabled_while_api_key_remains_required(tmp_path):
    client = _client(tmp_path, web_auth_enabled=False)
    state = client.get("/auth/state").json()
    assert state["enabled"] is False
    assert state["api_key_required"] is True
    assert state["setup_required"] is False
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"X-API-Key": API_KEY}).status_code == 200


def test_setup_session_auth_csrf_and_logout(tmp_path):
    client = _client(tmp_path)
    initial = client.get("/auth/state").json()
    assert initial["enabled"] is True
    assert initial["setup_required"] is True

    rejected = client.post(
        "/auth/setup",
        json={"email": "admin@example.com", "password": PASSWORD, "bootstrap_key": "bad"},
    )
    assert rejected.status_code == 401

    response = _setup(client)
    assert response.status_code == 201
    assert response.json()["user"]["email"] == "admin@example.com"
    assert response.cookies.get("bauer_session")
    assert "HttpOnly" in response.headers.get("set-cookie", "")
    assert client.get("/auth/state").json()["authenticated"] is True

    # GET protegido aceita a sessão sem expor/enviar X-API-Key.
    assert client.get("/status").status_code == 200

    # Uma rota mutável existente também passa pelo CSRF quando a autenticação
    # veio do cookie (o header API key continua sem essa exigência).
    assert client.post("/models/switch", json={"model": "qwen3:0.6b"}).status_code == 403

    # Operação mutável por cookie exige o token double-submit.
    assert client.post("/auth/logout").status_code == 403
    csrf = client.cookies.get("bauer_csrf")
    logged_out = client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logged_out.status_code == 200
    assert client.get("/status").status_code == 401


def test_login_recovery_and_api_key_compatibility(tmp_path):
    client = _client(tmp_path)
    assert _setup(client).status_code == 201
    csrf = client.cookies.get("bauer_csrf")
    assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200

    wrong = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "wrong"}
    )
    assert wrong.status_code == 401

    login = client.post(
        "/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200

    # Header legado continua válido independentemente do cookie/CSRF.
    no_cookie = _client(tmp_path)
    assert no_cookie.get("/status", headers={"X-API-Key": API_KEY}).status_code == 200

    recovered = no_cookie.post(
        "/auth/recover",
        json={
            "email": "admin@example.com",
            "new_password": "a different secure password",
            "bootstrap_key": API_KEY,
        },
    )
    assert recovered.status_code == 200
    assert no_cookie.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "a different secure password"},
    ).status_code == 200


def test_secure_mode_marks_auth_cookies_secure(tmp_path):
    client = _client(tmp_path, secure=True)
    response = _setup(client)
    cookies = response.headers.get_list("set-cookie")
    assert cookies
    assert all("Secure" in cookie for cookie in cookies)


def test_auth_rate_limit_is_independent_from_global_limit(tmp_path):
    client = _client(tmp_path)
    payload = {"email": "admin@example.com", "password": "wrong"}
    statuses = [client.post("/auth/login", json=payload).status_code for _ in range(11)]
    assert statuses[-1] == 429
