"""Sprint 62 — fronteiras de rede e web (auditoria 2026-10-12, T02/T03/WEB-02/WEB-03).

Toda prova aqui é hermética: transporte HTTP falso (``httpx.request``
patchado), orquestrador de time falso e construtor de client cloud falso.
Nenhum request sai da máquina e nenhum provider real é construído.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from bauer.tool_router import ToolRouter, _policy_operation_for_tool
from bauer.tools.base import ToolError


# ─── Transporte falso ─────────────────────────────────────────────────────────


class _FakeTransport:
    """Responde em sequência; grava (method, url, kwargs) de cada request."""

    def __init__(self, *responses: tuple[int, dict]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        status, headers = self.responses.pop(0) if self.responses else (200, {})
        headers = {"content-type": "text/plain", **headers}
        return httpx.Response(status, headers=headers, text="ok",
                              request=httpx.Request(method, url))


def _run_http(router: ToolRouter, transport: _FakeTransport, args: dict) -> str:
    with patch("bauer.tools.web._is_safe_url"), patch("httpx.request", transport):
        return router.execute({"action": "http_request", "args": args})


def _policy_router(tmp_path: Path, rules: str) -> ToolRouter:
    ws = tmp_path / "ws"
    (ws / ".bauer").mkdir(parents=True)
    (ws / ".bauer" / "policy.yaml").write_text(rules, encoding="utf-8")
    return ToolRouter(workspace=ws, web_enabled=True, audit_enabled=False,
                      policy_enabled=True, policy_root=tmp_path / "runtime")


# ─── T02: política network.http ──────────────────────────────────────────────


def test_http_tools_map_to_network_http_policy_operation():
    assert _policy_operation_for_tool("http_request") == "network.http"
    assert _policy_operation_for_tool("web_fetch") == "network.http"


def test_network_http_deny_rule_stops_http_request_before_transport(tmp_path):
    router = _policy_router(
        tmp_path, "rules:\n  - id: deny-all-http\n    operation: network.http\n    action: deny\n"
    )
    transport = _FakeTransport((200, {}))
    with pytest.raises(ToolError, match="policy denied"):
        _run_http(router, transport, {"url": "https://api.example/x"})
    assert transport.calls == []


def test_network_http_deny_rule_stops_web_fetch_before_extract(tmp_path):
    router = _policy_router(
        tmp_path, "rules:\n  - id: deny-all-http\n    operation: network.http\n    action: deny\n"
    )
    router._web = MagicMock()
    with patch("bauer.tools.web._is_safe_url"), pytest.raises(ToolError, match="policy denied"):
        router.execute({"action": "web_fetch", "args": {"url": "https://example.com/"}})
    router._web.extract.assert_not_called()


def test_network_http_allow_rule_lets_request_through(tmp_path):
    router = _policy_router(
        tmp_path, "rules:\n  - id: allow-http\n    operation: network.http\n    action: allow\n"
    )
    transport = _FakeTransport((200, {}))
    out = _run_http(router, transport, {"url": "https://api.example/x"})
    assert out.startswith("HTTP 200")
    assert len(transport.calls) == 1


def test_network_http_ask_rule_creates_approval_before_transport(tmp_path):
    router = _policy_router(
        tmp_path, "rules:\n  - id: ask-http\n    operation: network.http\n    action: ask\n"
    )
    transport = _FakeTransport((200, {}))
    with pytest.raises(ToolError):
        _run_http(router, transport, {"url": "https://api.example/x"})
    assert transport.calls == []
    from bauer.core.policy import ApprovalManager

    pending = [r for r in ApprovalManager(root=tmp_path / "runtime").list()
               if r.tool_name == "http_request"]
    assert pending and pending[0].status == "pending"


def test_default_rule_denies_secret_in_body_without_model_flag(tmp_path):
    """O modelo não precisa declarar `contains_secret`: o runtime detecta."""
    router = ToolRouter(workspace=tmp_path / "ws", web_enabled=True, audit_enabled=False,
                        policy_enabled=True, policy_root=tmp_path / "runtime")
    transport = _FakeTransport((200, {}))
    synthetic = "sk-" + "A" * 32
    with pytest.raises(ToolError, match="secret"):
        _run_http(router, transport, {
            "url": "https://collector.example/in", "method": "POST",
            "body": {"leak": synthetic}, "contains_secret": False,
        })
    assert transport.calls == []


def test_default_rule_allows_declared_credential_header(tmp_path):
    """Authorization é o canal legítimo de uma API autenticada: não é exfiltração."""
    router = ToolRouter(workspace=tmp_path / "ws", web_enabled=True, audit_enabled=False,
                        policy_enabled=True, policy_root=tmp_path / "runtime")
    transport = _FakeTransport((200, {}))
    out = _run_http(router, transport, {
        "url": "https://api.example/me",
        "headers": {"Authorization": "Bearer " + "sk-" + "B" * 32},
    })
    assert out.startswith("HTTP 200")
    assert len(transport.calls) == 1


def test_network_policy_payload_flags_credentials_and_origin():
    from bauer.tools.web import network_policy_payload

    payload = network_policy_payload({
        "url": "HTTPS://API.Example:8443/x?q=1", "method": "post",
        "headers": {"X-Api-Key": "k"},
    })
    assert payload["method"] == "POST"
    assert (payload["scheme"], payload["host"], payload["port"]) == ("https", "api.example", 8443)
    assert payload["has_credentials"] is True
    assert payload["contains_secret"] is False
    assert network_policy_payload({"url": "https://x.example/?token=" + "ghp_" + "c" * 36})[
        "contains_secret"] is True


# ─── T03: redirect entre origens ─────────────────────────────────────────────


def _plain_router(tmp_path: Path) -> ToolRouter:
    return ToolRouter(workspace=tmp_path / "ws", web_enabled=True, audit_enabled=False)


_CREDS = {"Authorization": "Bearer audit-token", "Cookie": "sid=1", "X-Api-Key": "k",
          "X-Custom-Token": "t", "Accept": "text/plain"}


@pytest.mark.parametrize("target", [
    "https://other.example/target",      # host
    "http://source.example/target",      # scheme (downgrade)
    "https://source.example:8443/target",  # porta
])
def test_cross_origin_redirect_strips_sensitive_headers(tmp_path, target):
    transport = _FakeTransport((302, {"location": target}), (200, {}))
    out = _run_http(_plain_router(tmp_path), transport,
                    {"url": "https://source.example/start", "headers": dict(_CREDS)})
    assert out.startswith("HTTP 200")
    assert len(transport.calls) == 2
    second_headers = transport.calls[1][2]["headers"]
    lowered = {k.lower() for k in second_headers}
    assert not lowered & {"authorization", "cookie", "x-api-key", "x-custom-token"}
    assert second_headers.get("Accept") == "text/plain"


def test_same_origin_redirect_keeps_authorization(tmp_path):
    # Porta default explícita (:443) é a mesma origem que a implícita.
    transport = _FakeTransport((302, {"location": "https://SOURCE.example:443/next"}), (200, {}))
    _run_http(_plain_router(tmp_path), transport,
              {"url": "https://source.example/start", "headers": dict(_CREDS)})
    assert len(transport.calls) == 2
    assert transport.calls[1][2]["headers"]["Authorization"] == "Bearer audit-token"


def test_303_turns_post_into_get_without_body(tmp_path):
    transport = _FakeTransport((303, {"location": "/done"}), (200, {}))
    _run_http(_plain_router(tmp_path), transport, {
        "url": "https://source.example/form", "method": "POST",
        "headers": {"Content-Type": "application/json"}, "body": {"a": 1},
    })
    method, _url, kwargs = transport.calls[1]
    assert method == "GET"
    assert kwargs["json"] is None and kwargs["content"] is None
    assert "Content-Type" not in kwargs["headers"]


def test_307_preserves_method_and_body_on_same_origin(tmp_path):
    transport = _FakeTransport((307, {"location": "/again"}), (200, {}))
    _run_http(_plain_router(tmp_path), transport, {
        "url": "https://source.example/form", "method": "POST", "body": {"a": 1},
    })
    method, _url, kwargs = transport.calls[1]
    assert method == "POST" and kwargs["json"] == {"a": 1}


def test_307_with_body_to_foreign_origin_is_not_followed(tmp_path):
    transport = _FakeTransport((307, {"location": "https://other.example/x"}), (200, {}))
    out = _run_http(_plain_router(tmp_path), transport, {
        "url": "https://source.example/form", "method": "POST", "body": {"a": 1},
        "headers": {"Authorization": "Bearer audit-token"},
    })
    assert len(transport.calls) == 1
    assert out.startswith("HTTP 307")


def test_redirect_to_internal_host_is_blocked_before_request(tmp_path):
    from bauer.url_safety import UrlSafetyError

    def _safe(url):
        if "169.254" in url:
            raise UrlSafetyError("internal")

    transport = _FakeTransport((302, {"location": "http://169.254.169.254/meta"}), (200, {}))
    with patch("bauer.tools.web._is_safe_url", _safe), patch("httpx.request", transport):
        with pytest.raises(ToolError, match="SSRF"):
            _plain_router(tmp_path).execute(
                {"action": "http_request", "args": {"url": "https://source.example/"}})
    assert len(transport.calls) == 1


# ─── WEB-02 / WEB-03: servidor ───────────────────────────────────────────────

pytest.importorskip("fastapi", reason="requer bauer-agent[server]")

from fastapi.testclient import TestClient  # noqa: E402

from bauer.server import create_app  # noqa: E402

API_KEY = "sprint62-bootstrap-key"


def _llm() -> MagicMock:
    llm = MagicMock()
    llm.host = "http://localhost:11434"
    llm._provider = "ollama"
    llm.list_models.return_value = ["fake"]
    llm.has_model.return_value = True
    return llm


def _app(tmp_path: Path, *, profile_set: str = "default", api_key: str = "",
         config: str | None = None, fallback_clients=None):
    ws = tmp_path / "workspace"
    ws.mkdir(exist_ok=True)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(config or (
        "model:\n  provider: ollama\n  name: fake\n  requested_context: 4096\n"
        "kernel:\n  enabled: false\n"
    ), encoding="utf-8")
    return create_app("fake", 4096, ToolRouter(workspace=ws), _llm(), "test",
                      tmp_path / "sessions", api_key=api_key, rate_limit_requests=0,
                      workspace=ws, config_path=cfg, profile_set=profile_set,
                      fallback_clients=fallback_clients)


def _cloud() -> MagicMock:
    cloud = MagicMock()
    cloud._provider = "openai"
    return cloud


def _team_orchestrator():
    team = MagicMock()
    team.to_dict.return_value = {}
    orch = MagicMock()
    orch.team_registry.get.return_value = team
    orch.stream.return_value = iter([{"event": "executed"}])
    return orch


def _logged_in(tmp_path: Path) -> TestClient:
    client = TestClient(_app(tmp_path, api_key=API_KEY))
    setup = client.post("/auth/setup", json={
        "email": "admin@example.com", "password": "correct horse battery staple",
        "bootstrap_key": API_KEY,
    })
    assert setup.status_code == 201
    return client


def test_team_stream_get_does_not_start_orchestration(tmp_path):
    client = _logged_in(tmp_path)
    orch = _team_orchestrator()
    with patch("bauer.core.runtime.AgnoTeamOrchestrator", return_value=orch):
        res = client.get("/api/teams/fake/stream", params={"task": "execute tools"})
    assert res.status_code == 405
    orch.stream.assert_not_called()


def test_team_stream_post_without_csrf_is_refused(tmp_path):
    client = _logged_in(tmp_path)
    orch = _team_orchestrator()
    with patch("bauer.core.runtime.AgnoTeamOrchestrator", return_value=orch):
        res = client.post("/api/teams/fake/stream", json={"task": "execute tools"})
    assert res.status_code == 403
    orch.stream.assert_not_called()


def test_team_stream_post_with_csrf_streams_events(tmp_path):
    client = _logged_in(tmp_path)
    orch = _team_orchestrator()
    csrf = client.cookies.get("bauer_csrf")
    with patch("bauer.core.runtime.AgnoTeamOrchestrator", return_value=orch):
        res = client.post("/api/teams/fake/stream", json={"task": "execute tools"},
                          headers={"X-CSRF-Token": csrf})
    assert res.status_code == 200
    assert orch.stream.call_count == 1
    assert orch.stream.call_args.args[:2] == ("fake", "execute tools")
    assert '"executed"' in res.text


def test_local_switch_to_cloud_is_refused_before_building_client(tmp_path):
    client = TestClient(_app(tmp_path, profile_set="local"))
    with patch("bauer.auxiliary_client._build_client_for_provider",
               return_value=_cloud()) as build:
        res = client.post("/models/switch", json={"provider": "openai", "model": "fake-cloud"})
    assert res.status_code == 409
    assert "--local" in res.json()["detail"]
    build.assert_not_called()
    status = client.get("/status").json()
    assert status["provider"] == "ollama" and status["model"] == "fake"


def test_local_switch_between_local_models_still_works(tmp_path):
    client = TestClient(_app(tmp_path, profile_set="local"))
    with patch("bauer.server._warmup_ollama_model"):
        res = client.post("/models/switch", json={"provider": "ollama", "model": "other"})
    assert res.status_code == 200
    assert res.json()["provider"] == "ollama"


def test_default_mode_switch_to_cloud_is_unchanged(tmp_path):
    client = TestClient(_app(tmp_path))
    with patch("bauer.auxiliary_client._build_client_for_provider", return_value=_cloud()):
        res = client.post("/models/switch", json={"provider": "openai", "model": "fake-cloud"})
    assert res.status_code == 200 and res.json()["provider"] == "openai"


def test_local_mode_ignores_saved_cloud_selection(tmp_path):
    with patch("bauer.auxiliary_client._build_client_for_provider", return_value=_cloud()):
        client = TestClient(_app(tmp_path))
        assert client.post("/models/switch",
                           json={"provider": "openai", "model": "fake-cloud"}).status_code == 200
        saved = tmp_path / "runtime" / "serve-model-state.json"
        assert "openai" in saved.read_text(encoding="utf-8")
        local = TestClient(_app(tmp_path, profile_set="local"))
    assert local.get("/status").json()["provider"] == "ollama"


def test_local_mode_oauth_does_not_select_cloud_model(tmp_path):
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        from fastapi import APIRouter
        return APIRouter()

    with patch("bauer.desktop_api.build_desktop_router", side_effect=_capture):
        client = TestClient(_app(tmp_path, profile_set="local"))
    on_connected = captured["on_openai_auth_connected"]
    with patch("bauer.auxiliary_client._build_client_for_provider",
               return_value=_cloud()) as build:
        warning = on_connected()
    assert warning and "--local" in warning
    build.assert_not_called()
    assert client.get("/status").json()["provider"] == "ollama"


def test_local_mode_drops_cloud_fallback_clients(tmp_path):
    local_fb = _llm()
    seen = {}

    def _turn(ctx, router, client, model, fallbacks, *a, **k):
        seen["fallbacks"] = list(fallbacks)
        ctx.add_assistant("ok")
        return "ok", []

    with patch("bauer.agent.run_one_turn_with_fallback", side_effect=_turn):
        client = TestClient(_app(tmp_path, profile_set="local",
                                 fallback_clients=[(_cloud(), "gpt"), (local_fb, "small")]))
        assert client.post("/chat", json={"message": "oi"}).status_code == 200
    assert [m for _c, m in seen["fallbacks"]] == ["small"]


def test_local_mode_routing_never_builds_cloud_profile_client(tmp_path):
    config = (
        "model:\n  provider: ollama\n  name: fake\n  requested_context: 16384\n"
        "  router_enabled: true\n"
        "  profiles_local:\n"
        "    fast: {provider: openai, model: gpt-cloud}\n"
        "    balanced: {provider: openai, model: gpt-cloud}\n"
        "    coding: {provider: openai, model: gpt-cloud}\n"
        "    heavy: {provider: openai, model: gpt-cloud}\n"
        "kernel:\n  enabled: false\n"
    )
    seen = {}

    def _turn(ctx, router, client, model, fallbacks, *a, **k):
        seen["client"], seen["model"] = client, model
        ctx.add_assistant("ok")
        return "ok", []

    from types import SimpleNamespace

    route = SimpleNamespace(provider="openai", model="gpt-cloud", profile="fast")
    with patch("bauer.agent.run_one_turn_with_fallback", side_effect=_turn), \
            patch("bauer.routing_runtime.decide_route", return_value=route) as decide, \
            patch("bauer.commands._runtime._build_client", return_value=_cloud()) as build:
        client = TestClient(_app(tmp_path, profile_set="local", config=config))
        assert client.post("/chat", json={"message": "oi"}).status_code == 200
    assert decide.called
    build.assert_not_called()
    assert getattr(seen["client"], "_provider", "") == "ollama"
    assert seen["model"] == "fake"


def test_validar_execucao_local_rejects_cloud_tier_in_profiles_local():
    from bauer.config_loader import BauerConfig
    from bauer.model_router import validar_execucao_local

    cfg = BauerConfig(**{"model": {
        "provider": "ollama", "name": "fake",
        "profiles_local": {"fast": {"provider": "ollama", "model": "a"},
                           "heavy": {"provider": "openai", "model": "gpt-cloud"}},
    }})
    problems = validar_execucao_local(cfg, "fake")
    assert any("heavy=openai/gpt-cloud" in p for p in problems)
    ok = BauerConfig(**{"model": {"provider": "ollama", "name": "fake",
                                  "profiles_local": {"fast": {"provider": "ollama", "model": "a"}}}})
    assert validar_execucao_local(ok, "fake") == []

