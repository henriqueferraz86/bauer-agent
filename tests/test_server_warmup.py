"""Testes do warmup de modelo Ollama no /models/switch.

Ao trocar para um modelo local, o servidor dispara o carregamento na GPU em
background (fire-and-forget) para a primeira mensagem não travar. O warmup
nunca deve bloquear nem levantar.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from bauer import server


def test_warmup_calls_ollama_generate_with_zero_predict():
    """Dispara POST /api/generate com prompt vazio e num_predict=0 (carrega
    sem gerar), no host e modelo dados."""
    called: dict = {}
    done = threading.Event()

    def fake_post(url, **kwargs):
        called["url"] = url
        called["json"] = kwargs.get("json")
        done.set()
        return MagicMock()

    with patch("httpx.post", side_effect=fake_post):
        server._warmup_ollama_model("http://localhost:11434/", "qwen2.5:7b")
        assert done.wait(timeout=5), "a thread de warmup não rodou"

    assert called["url"] == "http://localhost:11434/api/generate"  # barra final normalizada
    assert called["json"]["model"] == "qwen2.5:7b"
    assert called["json"]["prompt"] == ""
    assert called["json"]["options"]["num_predict"] == 0
    assert called["json"]["keep_alive"] == "30m"


def test_warmup_never_raises_on_failure():
    """A chamada dispara a thread e retorna na hora; erros ficam na thread e
    são engolidos — o switch nunca falha por causa do warmup."""
    with patch("httpx.post", side_effect=RuntimeError("boom")):
        server._warmup_ollama_model("http://localhost:1", "qwen2.5:7b")  # não levanta


def test_warmup_returns_immediately():
    """O warmup não bloqueia: mesmo com um post lento, a chamada retorna sem
    esperar a request terminar (é background)."""
    slow_started = threading.Event()

    def slow_post(url, **kwargs):
        slow_started.set()
        threading.Event().wait(10)  # simula request lenta
        return MagicMock()

    with patch("httpx.post", side_effect=slow_post):
        server._warmup_ollama_model("http://localhost:11434", "x")
        # se bloqueasse, isto só passaria após 10s; com background é imediato
        assert slow_started.wait(timeout=5)
