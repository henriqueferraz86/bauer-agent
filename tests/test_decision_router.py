"""Decisor Jev opt-in, parsing seguro e fallback hermético."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bauer.decision_router import JevDecisionClient, decide_with_fallback


def _cfg(*, enabled: bool = True, fallback: bool = True, key: str = "test-key"):
    return SimpleNamespace(
        decision=SimpleNamespace(
            jev_enabled=enabled,
            fallback_enabled=fallback,
            api_key=key,
            endpoint="https://api.typesafe.ai/v1/systemone",
            model="jev-latest",
            timeout_seconds=2.0,
            min_confidence=0.65,
        )
    )


class _Response:
    def __init__(self, payload, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _payload(*, confidence: float = 0.91):
    return {
        "answers": {
            "task_type": {"choice": "coding", "confidence": confidence},
            "complexity": {"choice": "high", "confidence": confidence},
            "profile": {"choice": "heavy", "confidence": confidence},
            "orchestrate": {"noul": 1.0},
        }
    }


def test_disabled_jev_uses_heuristic_without_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Jev não deveria ser chamado")

    monkeypatch.setattr("bauer.decision_router.httpx.post", fail)
    decision = decide_with_fallback("crie uma função python", config=_cfg(enabled=False))
    assert decision.source == "heuristic"
    assert decision.profile == "coding"


def test_jev_parses_typed_decision_and_applies_profile(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(url=url, kwargs=kwargs)
        return _Response(_payload())

    monkeypatch.setattr("bauer.decision_router.httpx.post", fake_post)
    profiles = {
        "heavy": SimpleNamespace(provider="ollama", model="qwen3-coder"),
    }
    decision = decide_with_fallback("redesenhe o runtime", profiles, _cfg())
    assert decision.source == "jev"
    assert decision.profile == "heavy"
    assert decision.model == "qwen3-coder"
    assert decision.orchestrate is True
    assert decision.confidence == pytest.approx(0.91)
    assert captured["kwargs"]["headers"]["Authorization"] == "Bearer test-key"
    assert captured["kwargs"]["json"]["model"] == "jev-latest"


def test_jev_error_falls_back_without_exposing_secret(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr("bauer.decision_router.httpx.post", fail)
    decision = decide_with_fallback("liste os arquivos", config=_cfg(key="super-secret"))
    assert decision.source == "heuristic"
    assert decision.profile == "fast"
    assert "super-secret" not in decision.error


def test_low_confidence_uses_fallback(monkeypatch):
    monkeypatch.setattr(
        "bauer.decision_router.httpx.post",
        lambda *args, **kwargs: _Response(_payload(confidence=0.2)),
    )
    decision = decide_with_fallback("explique o problema", config=_cfg())
    assert decision.source == "heuristic"
    assert "fallback" in decision.reason


def test_invalid_jev_answer_is_rejected(monkeypatch):
    payload = _payload()
    payload["answers"]["profile"]["choice"] = "unknown-tier"
    monkeypatch.setattr(
        "bauer.decision_router.httpx.post",
        lambda *args, **kwargs: _Response(payload),
    )
    decision = decide_with_fallback("faça uma análise", config=_cfg())
    assert decision.source == "heuristic"


def test_fallback_disabled_uses_safe_default(monkeypatch):
    monkeypatch.setattr(
        "bauer.decision_router.httpx.post",
        lambda *args, **kwargs: _Response({"answers": {}}),
    )
    decision = decide_with_fallback("faça uma análise", config=_cfg(fallback=False))
    assert decision.source == "safe-default"
    assert decision.profile == "balanced"
