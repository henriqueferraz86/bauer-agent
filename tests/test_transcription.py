"""Testes do bauer/transcription.py (STT Whisper cloud)."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from bauer import transcription
from bauer.transcription import available_stt_provider, transcribe_audio


@pytest.fixture()
def audio_file(tmp_path):
    p = tmp_path / "voice.ogg"
    p.write_bytes(b"OggS" + b"\x00" * 100)
    return p


def _mock_post(monkeypatch, handler):
    """Substitui httpx.post por um handler(url, **kwargs) -> httpx.Response."""
    monkeypatch.setattr(httpx, "post", handler)


class TestValidacao:
    def test_arquivo_inexistente(self, tmp_path):
        result = transcribe_audio(tmp_path / "nada.ogg")
        assert not result["success"]
        assert "não encontrado" in result["error"]

    def test_extensao_invalida(self, tmp_path):
        p = tmp_path / "doc.txt"
        p.write_text("oi")
        result = transcribe_audio(p)
        assert not result["success"]
        assert "não suportada" in result["error"]

    def test_arquivo_vazio(self, tmp_path):
        p = tmp_path / "voice.ogg"
        p.write_bytes(b"")
        result = transcribe_audio(p)
        assert not result["success"]
        assert "vazio" in result["error"]

    def test_arquivo_grande_demais(self, tmp_path, monkeypatch):
        monkeypatch.setattr(transcription, "MAX_AUDIO_BYTES", 10)
        p = tmp_path / "voice.ogg"
        p.write_bytes(b"x" * 100)
        result = transcribe_audio(p)
        assert not result["success"]
        assert "excede" in result["error"]


class TestProviders:
    def test_sem_keys_da_erro_claro(self, audio_file, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("STT_PROVIDER", raising=False)
        monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: False)
        result = transcribe_audio(audio_file)
        assert not result["success"]
        assert "OPENROUTER_API_KEY" in result["error"]

    def test_groq_sucesso(self, audio_file, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        seen = {}

        def fake_post(url, **kwargs):
            seen["url"] = url
            seen["auth"] = kwargs["headers"]["Authorization"]
            return httpx.Response(200, json={"text": "olá mundo"})

        _mock_post(monkeypatch, fake_post)
        result = transcribe_audio(audio_file)
        assert result["success"]
        assert result["transcript"] == "olá mundo"
        assert result["provider"] == "groq"
        assert "groq.com" in seen["url"]
        assert seen["auth"] == "Bearer gsk_test"

    def test_fallback_groq_para_openai(self, audio_file, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "auto")
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        calls = []

        def fake_post(url, **kwargs):
            calls.append(url)
            if "groq" in url:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"text": "fallback ok"})

        _mock_post(monkeypatch, fake_post)
        result = transcribe_audio(audio_file)
        assert result["success"]
        assert result["provider"] == "openai"
        assert len(calls) == 2

    def test_todos_falham(self, audio_file, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "auto")
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        def fake_post(url, **kwargs):
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        _mock_post(monkeypatch, fake_post)
        result = transcribe_audio(audio_file)
        assert not result["success"]
        assert "bad key" in result["error"]

    def test_transcricao_vazia_e_erro(self, audio_file, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "groq")
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        def fake_post(url, **kwargs):
            return httpx.Response(200, json={"text": "   "})

        _mock_post(monkeypatch, fake_post)
        result = transcribe_audio(audio_file)
        assert not result["success"]

    def test_openrouter_sucesso_usa_json_com_audio_base64(self, audio_file, monkeypatch):
        monkeypatch.delenv("STT_PROVIDER", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        seen = {}

        def fake_post(url, **kwargs):
            seen["url"] = url
            seen["headers"] = kwargs["headers"]
            seen["json"] = kwargs["json"]
            return httpx.Response(200, json={"text": "olá pelo OpenRouter"})

        _mock_post(monkeypatch, fake_post)
        result = transcribe_audio(audio_file)

        assert result["success"]
        assert result["provider"] == "openrouter"
        assert result["transcript"] == "olá pelo OpenRouter"
        assert seen["url"] == transcription.OPENROUTER_STT_URL
        assert seen["headers"]["Authorization"] == "Bearer sk-or-test"
        assert seen["json"]["model"] == "openai/whisper-large-v3-turbo"
        assert seen["json"]["input_audio"]["format"] == "ogg"
        assert seen["json"]["input_audio"]["data"]
        assert seen["json"]["language"] == "pt"


class TestAvailableProvider:
    def test_groq_prioridade(self, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "auto")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "g")
        monkeypatch.setenv("OPENAI_API_KEY", "o")
        assert available_stt_provider() == "groq"

    def test_openai_fallback(self, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "auto")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "o")
        assert available_stt_provider() == "openai"

    def test_openrouter_e_padrao(self, monkeypatch):
        monkeypatch.delenv("STT_PROVIDER", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.setenv("GROQ_API_KEY", "g")
        assert available_stt_provider() == "openrouter"

    def test_nenhum(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("STT_PROVIDER", raising=False)
        monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: False)
        assert available_stt_provider() is None


class TestLocalProvider:
    """STT local offline (faster-whisper) — modelo open-source na máquina."""

    def test_available_local_quando_instalado(self, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "local")
        monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: True)
        assert available_stt_provider() == "local"

    def test_available_local_sem_pacote_e_none(self, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "local")
        monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: False)
        assert available_stt_provider() is None

    def test_auto_cai_no_local_sem_keys(self, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "auto")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: True)
        assert available_stt_provider() == "local"

    def test_transcribe_usa_local(self, audio_file, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "local")

        called = {}

        def fake_local(path, model=None, language=""):
            called["model"] = model
            called["language"] = language
            return {"success": True, "transcript": "olá mundo"}

        monkeypatch.setattr(transcription, "_transcribe_local", fake_local)
        result = transcribe_audio(audio_file)
        assert result["success"]
        assert result["provider"] == "local"
        assert result["transcript"] == "olá mundo"
        assert called["model"] == transcription.LOCAL_STT_MODEL
        assert called["language"] == "pt"

    def test_transcribe_local_sem_pacote_erro_amigavel(self, audio_file, monkeypatch):
        """Sem faster-whisper instalado, STT_PROVIDER=local dá erro com dica de install."""
        monkeypatch.setenv("STT_PROVIDER", "local")
        import builtins
        real_import = builtins.__import__

        def no_faster_whisper(name, *args, **kwargs):
            if name == "faster_whisper":
                raise ImportError("No module named 'faster_whisper'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_faster_whisper)
        result = transcribe_audio(audio_file)
        assert not result["success"]
        assert "faster-whisper" in result["error"]


class TestPreload:
    """preload_local_model — warm-up do boot do gateway (só se provider=local)."""

    def test_preload_nao_dispara_p_cloud(self, monkeypatch):
        monkeypatch.setattr(transcription, "available_stt_provider", lambda: "groq")
        chamou = {"n": 0}
        monkeypatch.setattr(transcription, "_load_local_model", lambda m=None: chamou.__setitem__("n", chamou["n"] + 1))
        assert transcription.preload_local_model() is False
        time.sleep(0.05)
        assert chamou["n"] == 0  # não carregou nada

    def test_preload_dispara_p_local(self, monkeypatch):
        monkeypatch.setattr(transcription, "available_stt_provider", lambda: "local")
        carregou = threading.Event()
        monkeypatch.setattr(
            transcription, "_load_local_model",
            lambda m=None: carregou.set(),
        )
        assert transcription.preload_local_model() is True
        assert carregou.wait(timeout=2.0), "preload deveria ter chamado _load_local_model em background"
