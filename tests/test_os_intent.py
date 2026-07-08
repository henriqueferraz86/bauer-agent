"""Tests for os_intent — roteador de intenções do Bauer OS (Sprint 24)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bauer import os_intent
from bauer.os_intent import (
    IntentDecision,
    _extract_json,
    catalog_for_prompt,
    route_intent,
)


def _manifest(skill_id="windows.browser", platforms=("windows",), inputs=None):
    return SimpleNamespace(
        id=skill_id,
        description=f"Skill {skill_id}",
        platforms=list(platforms),
        inputs=inputs if inputs is not None else {"url": {"type": "string"}, "browser": {"type": "string"}},
    )


class FakeClient:
    """TextLLMClient mínimo: devolve um payload fixo em um chunk."""

    default_model = "fake-model"

    def __init__(self, payload: str):
        self.payload = payload
        self.calls: list[list[dict]] = []

    def chat_stream(self, model, messages):
        self.calls.append(messages)
        yield self.payload


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_object(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_fenced(self):
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_prose_prefix(self):
        out = _extract_json('Claro! Aqui está: {"skill_id": "x", "inputs": {"k": "v"}}')
        assert out == {"skill_id": "x", "inputs": {"k": "v"}}

    def test_nested_braces(self):
        out = _extract_json('{"inputs": {"url": "https://a.b/{c}"}}')
        assert out is None or isinstance(out, dict)  # chaves no valor: não pode explodir

    def test_invalid(self):
        assert _extract_json("nada de json aqui") is None
        assert _extract_json("") is None
        assert _extract_json("[1, 2]") is None  # lista não é decisão


# ---------------------------------------------------------------------------
# catalog_for_prompt
# ---------------------------------------------------------------------------

class TestCatalogFilter:
    def test_filters_by_platform(self):
        manifests = [
            _manifest("windows.browser", platforms=("windows",)),
            _manifest("linux.shell_safe", platforms=("linux",)),
            _manifest("bauer.coding", platforms=("windows", "linux", "macos")),
        ]
        out = catalog_for_prompt(manifests, platform_name="linux")
        assert {m.id for m in out} == {"linux.shell_safe", "bauer.coding"}

    def test_any_and_empty_platforms_always_included(self):
        manifests = [
            _manifest("a", platforms=("any",)),
            _manifest("b", platforms=()),
        ]
        out = catalog_for_prompt(manifests, platform_name="windows")
        assert {m.id for m in out} == {"a", "b"}


# ---------------------------------------------------------------------------
# route_intent
# ---------------------------------------------------------------------------

class TestRouteIntent:
    def _patch_client(self, monkeypatch, payload):
        client = FakeClient(payload)
        monkeypatch.setattr(
            "bauer.auxiliary_client.get_text_auxiliary_client",
            lambda slot, cfg=None: (client, client.default_model),
        )
        return client

    def test_routes_to_skill_with_inputs(self, monkeypatch):
        self._patch_client(
            monkeypatch,
            '{"skill_id": "windows.browser", "inputs": {"url": "https://www.google.com/search?q=agno+docs"},'
            ' "confidence": 0.92, "reason": "pesquisa na web"}',
        )
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        decision = route_intent("abre o navegador e pesquisa docs do agno", manifests)
        assert isinstance(decision, IntentDecision)
        assert decision.skill_id == "windows.browser"
        assert decision.inputs["url"].startswith("https://www.google.com/search")
        assert decision.confidence == pytest.approx(0.92)

    def test_filters_undeclared_inputs(self, monkeypatch):
        self._patch_client(
            monkeypatch,
            '{"skill_id": "windows.browser", "inputs": {"url": "https://x", "evil": "rm -rf"},'
            ' "confidence": 0.9, "reason": ""}',
        )
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        decision = route_intent("abrir x", manifests)
        assert decision is not None
        assert "evil" not in decision.inputs
        assert decision.inputs == {"url": "https://x"}

    def test_low_confidence_rejected(self, monkeypatch):
        self._patch_client(
            monkeypatch,
            '{"skill_id": "windows.browser", "inputs": {}, "confidence": 0.2, "reason": "chute"}',
        )
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        assert route_intent("talvez algo", manifests) is None

    def test_unknown_skill_rejected(self, monkeypatch):
        self._patch_client(
            monkeypatch,
            '{"skill_id": "skill.inexistente", "inputs": {}, "confidence": 0.9, "reason": ""}',
        )
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        assert route_intent("qualquer coisa", manifests) is None

    def test_null_skill_means_no_match(self, monkeypatch):
        self._patch_client(
            monkeypatch,
            '{"skill_id": null, "inputs": {}, "confidence": 0, "reason": "conversa"}',
        )
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        assert route_intent("bom dia, tudo bem?", manifests) is None

    def test_client_unavailable_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "bauer.auxiliary_client.get_text_auxiliary_client",
            lambda slot, cfg=None: (None, None),
        )
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        assert route_intent("abrir navegador", manifests) is None

    def test_garbage_response_returns_none(self, monkeypatch):
        self._patch_client(monkeypatch, "desculpe, não entendi a pergunta")
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        assert route_intent("abrir navegador", manifests) is None

    def test_client_exception_returns_none(self, monkeypatch):
        class BoomClient:
            default_model = "boom"

            def chat_stream(self, model, messages):
                raise RuntimeError("provider down")

        monkeypatch.setattr(
            "bauer.auxiliary_client.get_text_auxiliary_client",
            lambda slot, cfg=None: (BoomClient(), "boom"),
        )
        manifests = [_manifest(platforms=(os_intent.current_platform(),))]
        assert route_intent("abrir navegador", manifests) is None

    def test_empty_text_returns_none(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "bauer.auxiliary_client.get_text_auxiliary_client",
            lambda slot, cfg=None: called.append(slot) or (None, None),
        )
        assert route_intent("", [_manifest()]) is None
        assert called == []  # nem tenta o provider

    def test_slot_is_intent_router(self, monkeypatch):
        seen = {}

        def fake_get(slot, cfg=None):
            seen["slot"] = slot
            return None, None

        monkeypatch.setattr("bauer.auxiliary_client.get_text_auxiliary_client", fake_get)
        route_intent("abrir navegador", [_manifest(platforms=(os_intent.current_platform(),))])
        assert seen["slot"] == "intent_router"


# ---------------------------------------------------------------------------
# Regressão da Sprint 24: LLMs mandam browser="default" — não é um executável.
# ---------------------------------------------------------------------------

class TestBrowserDefaultAlias:
    @pytest.mark.parametrize("alias", ["default", "Default", "padrao", "padrão", "system"])
    def test_default_aliases_use_webbrowser(self, monkeypatch, alias):
        from unittest.mock import MagicMock

        from bauer.core.skills import windows as win

        opened = MagicMock(return_value=True)
        monkeypatch.setattr(win.webbrowser, "open", opened)
        popen = MagicMock()
        monkeypatch.setattr(win.subprocess, "Popen", popen)

        out = win._open_browser({"url": "https://example.com", "browser": alias})
        assert out["opened"] is True
        assert out["browser"] == "default"
        opened.assert_called_once()
        popen.assert_not_called()  # jamais Popen(["default", ...])
