"""Testes para ModelRouter — classificação e roteamento de mensagens."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bauer.model_router import ModelRouter, Route, RouterConfig, RouteKind


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_router(reply: str = "direct") -> tuple[ModelRouter, MagicMock]:
    client = MagicMock()
    client.chat_stream.return_value = iter([reply])
    router = ModelRouter(client=client)
    return router, client


# ─── RouterConfig ─────────────────────────────────────────────────────────────


def test_router_config_defaults():
    cfg = RouterConfig()
    assert cfg.enabled is True
    assert cfg.router_model == "qwen3:0.6b"
    assert cfg.default_model == "phi4-mini"
    assert len(cfg.routes) == 5


def test_route_for_known_kind():
    cfg = RouterConfig()
    route = cfg.route_for("code")
    assert route.kind == "code"


def test_route_for_unknown_kind_fallback():
    cfg = RouterConfig()
    route = cfg.route_for("unknown")  # type: ignore
    assert route.kind == "direct"


# ─── ModelRouter.classify ────────────────────────────────────────────────────


@pytest.mark.parametrize("reply,expected", [
    ("direct",      "direct"),
    ("code",        "code"),
    ("codigo",      "code"),
    ("reasoning",   "reasoning"),
    ("raciocinio",  "reasoning"),
    ("tool",        "tool"),
    ("ferramenta",  "tool"),
    ("orchestrate", "orchestrate"),
    ("orquestra",   "orchestrate"),
    ("qualquer coisa desconhecida", "direct"),
])
def test_classify_maps_reply_to_kind(reply, expected):
    router, _ = _make_router(reply)
    assert router.classify("qualquer mensagem") == expected


def test_classify_handles_mixed_case():
    router, _ = _make_router("CODE")
    assert router.classify("escreva um script") == "code"


def test_classify_whitespace_stripped():
    router, _ = _make_router("  reasoning  ")
    assert router.classify("explique algo") == "reasoning"


def test_classify_falls_back_on_client_error():
    client = MagicMock()
    client.chat_stream.side_effect = ConnectionError("offline")
    router = ModelRouter(client=client)
    # Deve retornar "reasoning" (fallback seguro) sem levantar exceção
    result = router.classify("qualquer coisa")
    assert result == "reasoning"


def test_classify_uses_router_model():
    router, client = _make_router("direct")
    router.classify("oi")
    call_args = client.chat_stream.call_args
    model_used = call_args[0][0]
    assert model_used == router.config.router_model


# ─── ModelRouter.select_model ────────────────────────────────────────────────


def test_select_model_returns_model_and_route():
    router, _ = _make_router("code")
    model, route = router.select_model("escreva um script python")
    assert isinstance(model, str)
    assert len(model) > 0
    assert isinstance(route, Route)
    assert route.kind == "code"


def test_select_model_direct_uses_light_model():
    router, _ = _make_router("direct")
    model, route = router.select_model("oi")
    assert route.kind == "direct"
    # modelo direto deve ser mais leve (qwen3)
    assert "qwen" in model.lower() or "direct" in route.label


def test_select_model_orchestrate():
    router, _ = _make_router("orchestrate")
    model, route = router.select_model("pesquise e analise e gere relatorio")
    assert route.kind == "orchestrate"


def test_select_model_fallback_on_error():
    client = MagicMock()
    client.chat_stream.side_effect = RuntimeError("sem conexão")
    router = ModelRouter(client=client)
    model, route = router.select_model("oi")
    # Não deve explodir — retorna reasoning como fallback
    assert model
    assert route.kind == "reasoning"


# ─── Route dataclass ─────────────────────────────────────────────────────────


def test_route_fields():
    r = Route(kind="code", label="codigo", model="smollm3")
    assert r.kind == "code"
    assert r.label == "codigo"
    assert r.model == "smollm3"
