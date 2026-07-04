"""Testes de TTS local via Piper (bauer/tts_local.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestHasPiper:
    def test_has_piper_returns_bool(self):
        from bauer.tts_local import _has_piper

        assert isinstance(_has_piper(), bool)


class TestSynthesizeToFileImportError:
    def test_missing_piper_raises_import_error(self, tmp_path):
        from bauer.tts_local import synthesize_to_file

        with patch("bauer.tts_local._has_piper", return_value=False):
            with pytest.raises(ImportError, match="piper-tts não instalado"):
                synthesize_to_file("olá", tmp_path / "out.wav")


class TestSpeakText:
    def test_texto_vazio_retorna_false_sem_tentar_nada(self):
        from bauer.tts_local import speak_text

        assert speak_text("") is False
        assert speak_text("   ") is False

    def test_falha_ao_carregar_voz_retorna_false_com_aviso(self):
        from bauer.tts_local import speak_text

        console = MagicMock()
        with patch("bauer.tts_local._load_voice", side_effect=ImportError("piper-tts não instalado")):
            result = speak_text("olá mundo", console=console)
        assert result is False
        console.print.assert_called_once()
        assert "TTS local indisponível" in console.print.call_args[0][0]

    def test_erro_generico_na_sintese_retorna_false_sem_levantar(self):
        from bauer.tts_local import speak_text

        console = MagicMock()
        with patch("bauer.tts_local._load_voice", side_effect=RuntimeError("boom")):
            result = speak_text("olá mundo", console=console)
        assert result is False
        console.print.assert_called_once()
        assert "Falha ao falar a resposta" in console.print.call_args[0][0]

    def test_sucesso_sintetiza_e_toca(self):
        from bauer.tts_local import speak_text

        import numpy as np

        fake_chunk = MagicMock()
        fake_chunk.audio_int16_array = np.array([1, 2, 3], dtype="int16")
        fake_voice = MagicMock()
        fake_voice.synthesize.return_value = [fake_chunk]
        fake_voice.config.sample_rate = 22050

        with patch("bauer.tts_local._load_voice", return_value=fake_voice), patch(
            "sounddevice.play"
        ) as mock_play, patch("sounddevice.wait") as mock_wait:
            result = speak_text("olá mundo")

        assert result is True
        mock_play.assert_called_once()
        mock_wait.assert_called_once()

    def test_sem_chunks_retorna_false(self):
        from bauer.tts_local import speak_text

        fake_voice = MagicMock()
        fake_voice.synthesize.return_value = []

        with patch("bauer.tts_local._load_voice", return_value=fake_voice), patch("sounddevice.play"):
            result = speak_text("olá mundo")

        assert result is False


class TestVoicePaths:
    def test_voice_paths_usa_tts_voices_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BAUER_HOME", str(tmp_path))
        from bauer.tts_local import _voice_paths

        model_path, config_path = _voice_paths("pt_BR-faber-medium")
        assert model_path.name == "pt_BR-faber-medium.onnx"
        assert config_path.name == "pt_BR-faber-medium.onnx.json"
        assert model_path.parent == tmp_path / "tts_voices"
