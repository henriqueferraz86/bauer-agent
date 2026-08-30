"""Testes do bauer/tts.py (TTS — Coqui XTTS-v2 local / OpenAI cloud)."""

from __future__ import annotations

import httpx
import pytest

from bauer import tts
from bauer.tts import available_tts_provider, synthesize_speech


def _mock_post(monkeypatch, handler):
    monkeypatch.setattr(httpx, "post", handler)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Nenhum teste deve herdar TTS_PROVIDER/OPENAI_API_KEY do ambiente real."""
    monkeypatch.delenv("TTS_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


class TestValidacao:
    def test_texto_vazio(self):
        result = synthesize_speech("")
        assert not result["success"]
        assert "vazio" in result["error"]

    def test_texto_grande_demais(self, monkeypatch):
        monkeypatch.setattr(tts, "MAX_TEXT_CHARS", 10)
        result = synthesize_speech("x" * 100)
        assert not result["success"]
        assert "excede" in result["error"]


class TestAvailableProvider:
    def test_sem_nada_configurado(self, monkeypatch):
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: False)
        assert available_tts_provider() is None

    def test_openai_key_presente(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: False)
        assert available_tts_provider() == "openai"

    def test_local_instalado_prioriza_sobre_openai(self, monkeypatch):
        """auto: local vem primeiro (offline, sem key) — símetro ao TTS_PROVIDER=auto."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: True)
        assert available_tts_provider() == "local"

    def test_pref_local_sem_pacote_instalado(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "local")
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: False)
        assert available_tts_provider() is None

    def test_pref_openai_sem_key(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        assert available_tts_provider() is None


class TestSynthesizeSemProvider:
    def test_sem_providers_da_erro_claro(self, monkeypatch):
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: False)
        result = synthesize_speech("ola mundo")
        assert not result["success"]
        assert "coqui-tts" in result["error"]
        assert "OPENAI_API_KEY" in result["error"]


class TestSynthesizeOpenAI:
    def test_sucesso(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TTS_PROVIDER", "openai")
        seen = {}

        def fake_post(url, **kwargs):
            seen["url"] = url
            seen["json"] = kwargs["json"]
            seen["auth"] = kwargs["headers"]["Authorization"]
            return httpx.Response(200, content=b"RIFF....WAVEfake")

        _mock_post(monkeypatch, fake_post)
        out = tmp_path / "resp.wav"
        result = synthesize_speech("ola mundo", output_path=out)

        assert result["success"]
        assert result["provider"] == "openai"
        assert result["path"] == str(out)
        assert out.read_bytes() == b"RIFF....WAVEfake"
        assert seen["url"] == tts.OPENAI_TTS_URL
        assert seen["auth"] == "Bearer sk-test"
        assert seen["json"]["input"] == "ola mundo"

    def test_http_error_reportado(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TTS_PROVIDER", "openai")

        def fake_post(url, **kwargs):
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})

        _mock_post(monkeypatch, fake_post)
        result = synthesize_speech("ola", output_path=tmp_path / "x.wav")
        assert not result["success"]
        assert "invalid api key" in result["error"]

    def test_resposta_vazia_e_erro(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("TTS_PROVIDER", "openai")

        def fake_post(url, **kwargs):
            return httpx.Response(200, content=b"")

        _mock_post(monkeypatch, fake_post)
        result = synthesize_speech("ola", output_path=tmp_path / "x.wav")
        assert not result["success"]
        assert not (tmp_path / "x.wav").exists()


class TestSynthesizeLocal:
    def test_sucesso_usa_primeiro_speaker_embutido(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TTS_PROVIDER", "local")
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: True)
        monkeypatch.setenv("TTS_VOICE", "")

        calls = {}

        def fake_synth(text, dest, model=None):
            calls["text"] = text
            calls["dest"] = dest
            dest.write_bytes(b"fake-wav")

        monkeypatch.setattr(tts, "_synthesize_local", fake_synth)
        out = tmp_path / "local.wav"
        result = synthesize_speech("oi", output_path=out)

        assert result["success"]
        assert result["provider"] == "local"
        assert calls["text"] == "oi"

    def test_pacote_ausente_da_mensagem_de_instalacao(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TTS_PROVIDER", "local")
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: True)

        def fake_synth(text, dest, model=None):
            raise RuntimeError("coqui-tts não instalado. Rode `pip install coqui-tts`")

        monkeypatch.setattr(tts, "_synthesize_local", fake_synth)
        result = synthesize_speech("oi", output_path=tmp_path / "x.wav")
        assert not result["success"]
        assert "coqui-tts" in result["error"]


class TestFallbackAutoCascade:
    def test_local_falha_cai_para_openai(self, monkeypatch, tmp_path):
        """auto: local tenta primeiro; se falhar, cai pra openai — não desiste."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(tts, "_coqui_tts_available", lambda: True)

        def failing_local(text, dest, model=None):
            raise RuntimeError("modelo indisponivel")

        monkeypatch.setattr(tts, "_synthesize_local", failing_local)

        def fake_post(url, **kwargs):
            return httpx.Response(200, content=b"cloud-audio")

        _mock_post(monkeypatch, fake_post)
        out = tmp_path / "x.wav"
        result = synthesize_speech("oi", output_path=out)

        assert result["success"]
        assert result["provider"] == "openai"
        assert out.read_bytes() == b"cloud-audio"


class TestResolveLocalDevice:
    def test_env_explicita_vence(self, monkeypatch):
        monkeypatch.setattr(tts, "LOCAL_TTS_DEVICE", "cpu")
        assert tts._resolve_local_device() == "cpu"

    def test_auto_sem_torch_cai_para_cpu(self, monkeypatch):
        monkeypatch.setattr(tts, "LOCAL_TTS_DEVICE", "auto")
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "torch":
                raise ImportError("no torch")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert tts._resolve_local_device() == "cpu"
