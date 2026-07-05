"""Testes de captura de áudio (bauer/audio_capture.py).

Parada é manual (ENTER), não detecção automática de silêncio — mais
confiável que calibrar threshold de dB por microfone/ambiente (o que já
falhou: gravação cortada antes da fala aparecer, ou nunca cortada).
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


def _try_import(name: str) -> bool:
    """Helper: True se módulo está disponível."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _delayed_enter(*_args, **_kwargs) -> str:
    """Mock de input(): simula um pequeno atraso real antes do ENTER, dando
    tempo da thread de gravação (mockada, instantânea) rodar algumas
    iterações — sem isso o teste seria não-determinístico (corrida entre
    threads sem nenhum tempo real decorrido)."""
    time.sleep(0.05)
    return ""


class TestAudioCaptureDeps:
    """Verifica disponibilidade de dependências."""

    def test_has_sounddevice_check(self):
        from bauer.audio_capture import _has_sounddevice

        assert isinstance(_has_sounddevice(), bool)

    def test_has_numpy_check(self):
        from bauer.audio_capture import _has_numpy

        assert isinstance(_has_numpy(), bool)


class TestAudioCaptureImportError:
    """Testa comportamento quando deps estão faltando."""

    def test_missing_sounddevice_raises_import_error(self):
        from bauer.audio_capture import capture_voice_input

        with patch("bauer.audio_capture._has_sounddevice", return_value=False):
            with pytest.raises(ImportError, match="sounddevice não instalado"):
                capture_voice_input(console=None)

    def test_missing_numpy_raises_import_error(self):
        from bauer.audio_capture import capture_voice_input

        with patch("bauer.audio_capture._has_sounddevice", return_value=True), patch(
            "bauer.audio_capture._has_numpy", return_value=False
        ):
            with pytest.raises(ImportError, match="numpy não instalado"):
                capture_voice_input(console=None)


@pytest.mark.skipif(not _try_import("numpy"), reason="numpy não instalado")
class TestTrimSilence:
    """Corta silêncio das bordas — reduz alucinação do Whisper (ex.: 'Thank
    you.' em trechos de silêncio puro, comum no início da gravação)."""

    def test_corta_silencio_das_bordas(self):
        import numpy as np

        from bauer.audio_capture import _trim_silence

        chunk_size = 1600
        silence = np.zeros((chunk_size, 1), dtype="float32")
        speech = np.full((chunk_size, 1), 0.5, dtype="float32")
        # 3 chunks de silêncio + 2 de fala + 3 de silêncio
        audio = np.concatenate([silence] * 3 + [speech] * 2 + [silence] * 3, axis=0)

        trimmed = _trim_silence(audio, chunk_size)

        # Bem mais curto que o original (bordas de silêncio removidas)
        assert len(trimmed) < len(audio)
        # A fala real continua presente no meio do resultado
        assert np.any(np.abs(trimmed) > 0.1)

    def test_silencio_total_devolve_original(self):
        import numpy as np

        from bauer.audio_capture import _trim_silence

        chunk_size = 1600
        silence = np.zeros((chunk_size, 1), dtype="float32")
        audio = np.concatenate([silence] * 5, axis=0)

        trimmed = _trim_silence(audio, chunk_size)

        assert len(trimmed) == len(audio)

    def test_tudo_fala_nao_corta_quase_nada(self):
        import numpy as np

        from bauer.audio_capture import _trim_silence

        chunk_size = 1600
        speech = np.full((chunk_size, 1), 0.5, dtype="float32")
        audio = np.concatenate([speech] * 5, axis=0)

        trimmed = _trim_silence(audio, chunk_size)

        assert len(trimmed) == len(audio)


@pytest.mark.skipif(
    not (_try_import("sounddevice") and _try_import("numpy") and _try_import("soundfile")),
    reason="sounddevice, numpy ou soundfile não instalados",
)
class TestCaptureComEnter:
    def test_sem_frames_retorna_none(self):
        """duration_max_s=0 → max_frames=0 → thread de gravação não roda
        nenhuma iteração, independente de quando ENTER é apertado → None."""
        from bauer.audio_capture import capture_voice_input

        with patch("builtins.input", return_value=""):
            result = capture_voice_input(duration_max_s=0, console=None)
        assert result is None

    def test_nao_abre_stream_concorrente_com_rec(self):
        """Regressão: sd.InputStream() aberto em paralelo a sd.rec() disputava
        o microfone e travava a gravação em alguns drivers Windows. sd.rec()
        já abre/fecha sua própria stream — não deve haver mais nenhuma
        chamada a sd.InputStream durante a captura."""
        import numpy as np

        from bauer.audio_capture import capture_voice_input

        fake_chunk = np.zeros((1600, 1), dtype="float32")
        with patch("sounddevice.rec", return_value=fake_chunk) as mock_rec, patch(
            "sounddevice.wait"
        ), patch("sounddevice.InputStream") as mock_stream, patch(
            "builtins.input", side_effect=_delayed_enter
        ), patch(
            "bauer.transcription.transcribe_audio",
            return_value={"success": True, "transcript": "x", "provider": "local"},
        ):
            capture_voice_input(duration_max_s=30, console=None)

        mock_stream.assert_not_called()
        assert mock_rec.called

    def test_enter_para_gravacao_e_transcreve(self):
        """Grava alguns frames (thread) até ENTER (thread principal via
        input()), transcreve e retorna o texto."""
        import numpy as np

        from bauer.audio_capture import capture_voice_input

        fake_chunk = np.full((1600, 1), 0.5, dtype="float32")
        with patch("sounddevice.rec", return_value=fake_chunk), patch(
            "sounddevice.wait"
        ), patch("builtins.input", side_effect=_delayed_enter), patch(
            "bauer.transcription.transcribe_audio",
            return_value={"success": True, "transcript": "ola mundo", "provider": "local"},
        ) as mock_transcribe:
            result = capture_voice_input(duration_max_s=30, console=None)

        assert result == "ola mundo"
        assert mock_transcribe.called

    def test_transcricao_falha_retorna_none(self):
        from bauer.audio_capture import capture_voice_input
        import numpy as np

        fake_chunk = np.full((1600, 1), 0.5, dtype="float32")
        with patch("sounddevice.rec", return_value=fake_chunk), patch(
            "sounddevice.wait"
        ), patch("builtins.input", side_effect=_delayed_enter), patch(
            "bauer.transcription.transcribe_audio",
            return_value={"success": False, "transcript": "", "error": "boom"},
        ):
            result = capture_voice_input(duration_max_s=30, console=None)

        assert result is None

    def test_keep_wav_env_mantem_arquivo(self, monkeypatch):
        """BAUER_VOICE_KEEP_WAV=1 mantém o .wav no disco (debug: ouvir o
        áudio capturado pra separar 'mic errado' de 'transcrição alucinou')."""
        import numpy as np
        from pathlib import Path

        from bauer.audio_capture import capture_voice_input

        monkeypatch.setenv("BAUER_VOICE_KEEP_WAV", "1")
        fake_chunk = np.full((1600, 1), 0.5, dtype="float32")
        captured_path = {}

        def _fake_transcribe(path):
            captured_path["path"] = str(path)
            return {"success": True, "transcript": "ok", "provider": "local"}

        with patch("sounddevice.rec", return_value=fake_chunk), patch(
            "sounddevice.wait"
        ), patch("builtins.input", side_effect=_delayed_enter), patch(
            "bauer.transcription.transcribe_audio", side_effect=_fake_transcribe
        ):
            capture_voice_input(duration_max_s=30, console=None)

        wav_path = Path(captured_path["path"])
        assert wav_path.exists()
        wav_path.unlink()  # limpa o que o teste deixou

    def test_teto_de_seguranca_para_sozinho_sem_enter(self):
        """duration_max_s pequeno funciona como trava de segurança: mesmo se
        ENTER nunca "chegasse" a tempo, a thread de gravação para sozinha
        quando atinge max_frames (aqui simulado com input() bem lento)."""
        from bauer.audio_capture import capture_voice_input
        import numpy as np

        fake_chunk = np.full((1600, 1), 0.5, dtype="float32")

        def _slow_input(*_a, **_k):
            time.sleep(0.3)  # bem mais lento que a gravação (duration_max_s=0.2)
            return ""

        with patch("sounddevice.rec", return_value=fake_chunk), patch(
            "sounddevice.wait"
        ), patch("builtins.input", side_effect=_slow_input), patch(
            "bauer.transcription.transcribe_audio",
            return_value={"success": True, "transcript": "ok", "provider": "local"},
        ):
            result = capture_voice_input(duration_max_s=0.2, console=None)

        assert result == "ok"
