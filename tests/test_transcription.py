"""Testes do bauer/transcription.py (STT Whisper cloud)."""

from __future__ import annotations

import json

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
        monkeypatch.delenv("STT_PROVIDER", raising=False)
        monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: False)
        result = transcribe_audio(audio_file)
        assert not result["success"]
        assert "GROQ_API_KEY" in result["error"]

    def test_groq_sucesso(self, audio_file, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
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
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
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
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def fake_post(url, **kwargs):
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        _mock_post(monkeypatch, fake_post)
        result = transcribe_audio(audio_file)
        assert not result["success"]
        assert "bad key" in result["error"]

    def test_transcricao_vazia_e_erro(self, audio_file, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def fake_post(url, **kwargs):
            return httpx.Response(200, json={"text": "   "})

        _mock_post(monkeypatch, fake_post)
        result = transcribe_audio(audio_file)
        assert not result["success"]


class TestAvailableProvider:
    def test_groq_prioridade(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "g")
        monkeypatch.setenv("OPENAI_API_KEY", "o")
        assert available_stt_provider() == "groq"

    def test_openai_fallback(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "o")
        assert available_stt_provider() == "openai"

    def test_nenhum(self, monkeypatch):
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
        monkeypatch.delenv("STT_PROVIDER", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setattr(transcription, "_faster_whisper_available", lambda: True)
        assert available_stt_provider() == "local"

    def test_transcribe_usa_local(self, audio_file, monkeypatch):
        monkeypatch.setenv("STT_PROVIDER", "local")

        called = {}

        def fake_local(path, model=None):
            called["model"] = model
            return {"success": True, "transcript": "olá mundo"}

        monkeypatch.setattr(transcription, "_transcribe_local", fake_local)
        result = transcribe_audio(audio_file)
        assert result["success"]
        assert result["provider"] == "local"
        assert result["transcript"] == "olá mundo"
        assert called["model"] == transcription.LOCAL_STT_MODEL

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
