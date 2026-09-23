from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from bauer.auth import AuthManager, AuthToken
from bauer.openai_browser_auth import OpenAIBrowserAuthBroker


def _broker(tmp_path, *, clock=lambda: 1000.0):
    manager = AuthManager(base_dir=tmp_path / "auth")
    broker = OpenAIBrowserAuthBroker(
        manager=manager,
        callback_port=1455,
        ttl_seconds=60,
        clock=clock,
    )
    broker._ensure_server = lambda: None
    return broker, manager


def test_start_returns_only_authorization_url_and_safe_expiry(tmp_path):
    broker, _ = _broker(tmp_path)
    started = broker.start()
    parsed = urlparse(started["authorization_url"])
    query = parse_qs(parsed.query)

    assert parsed.netloc == "auth.openai.com"
    assert query["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    assert query["code_challenge_method"] == ["S256"]
    assert "code_verifier" not in query
    assert "token" not in started
    assert broker.status()["status"] == "pending"


def test_repeated_start_reuses_the_same_pending_transaction(tmp_path):
    broker, _ = _broker(tmp_path)
    first = broker.start()
    second = broker.start()

    assert second == first


def test_callback_validates_state_completes_and_never_exposes_tokens(tmp_path, monkeypatch):
    broker, manager = _broker(tmp_path)
    started = broker.start()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]

    def complete(authorization, **kwargs):
        assert kwargs["code"] == "oauth-code"
        assert kwargs["returned_state"] == state
        assert kwargs["require_state"] is True
        token = AuthToken(
            provider="openai",
            access_token="secret-access-token",
            refresh_token="secret-refresh-token",
        )
        manager.store.save(token)
        return token

    monkeypatch.setattr(manager, "complete_oauth", complete)
    code, title, _ = broker.handle_callback(
        f"/auth/callback?code=oauth-code&state={state}"
    )

    assert code == 200
    assert title == "OpenAI conectada"
    status = broker.status()
    assert status["connected"] is True
    assert status["auth_type"] == "chatgpt_oauth"
    assert "secret" not in repr(status)


def test_callback_runs_runtime_selection_without_leaking_warning(tmp_path, monkeypatch):
    manager = AuthManager(base_dir=tmp_path / "auth")
    selected = []
    broker = OpenAIBrowserAuthBroker(
        manager=manager,
        callback_port=1455,
        on_connected=lambda: selected.append("openai/gpt-5.6-luna") or None,
    )
    broker._ensure_server = lambda: None
    started = broker.start()
    state = parse_qs(urlparse(started["authorization_url"]).query)["state"][0]

    def complete(authorization, **kwargs):
        manager.store.save(AuthToken(provider="openai", access_token="secret"))
        return manager.store.load("openai")

    monkeypatch.setattr(manager, "complete_oauth", complete)
    assert broker.handle_callback(f"/auth/callback?code=code&state={state}")[0] == 200
    assert selected == ["openai/gpt-5.6-luna"]
    assert broker.status()["warning"] == ""


def test_callback_rejects_wrong_state_without_exchanging_code(tmp_path, monkeypatch):
    broker, manager = _broker(tmp_path)
    broker.start()
    monkeypatch.setattr(
        manager,
        "complete_oauth",
        lambda *args, **kwargs: pytest.fail("não deve trocar o código"),
    )

    code, title, _ = broker.handle_callback(
        "/auth/callback?code=attacker&state=wrong"
    )

    assert code == 400
    assert title == "Login rejeitado"
    assert manager.store.load("openai") is None


def test_pending_transaction_expires(tmp_path):
    now = [1000.0]
    broker, _ = _broker(tmp_path, clock=lambda: now[0])
    broker.start()
    now[0] = 1061.0

    status = broker.status()

    assert status["status"] == "error"
    assert "expirou" in status["error"]


def test_logout_removes_openai_token(tmp_path):
    broker, manager = _broker(tmp_path)
    manager.store.save(AuthToken(provider="openai", access_token="secret"))
    assert broker.status()["connected"] is True

    assert broker.logout() is True
    assert broker.status()["connected"] is False


def test_token_store_default_respects_bauer_home(tmp_path, monkeypatch):
    target = tmp_path / "persistent-home"
    monkeypatch.setenv("BAUER_HOME", str(target))

    manager = AuthManager()
    manager.store.save(AuthToken(provider="openai", access_token="secret"))

    assert manager.store.tokens_file == target / "auth.json"
    assert manager.store.load("openai").access_token == "secret"
