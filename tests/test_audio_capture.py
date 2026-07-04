"""Testes de captura de áudio (bauer/audio_capture.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _try_import(name: str) -> bool:
    """Helper: True se módulo está disponível."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class TestAudioCaptureDeps:
    """Verifica disponibilidade de dependências."""

    def test_has_sounddevice_check(self):
        from bauer.audio_capture import _has_sounddevice

        # Não levanta, retorna bool
        result = _has_sounddevice()
        assert isinstance(result, bool)

    def test_has_numpy_check(self):
        from bauer.audio_capture import _has_numpy

        # Não levanta, retorna bool
        result = _has_numpy()
        assert isinstance(result, bool)


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


class TestAudioCaptureSkipped:
    """Testa fallback when deps are unavailable — não tenta importar de verdade."""

    @pytest.mark.skipif(
        not (
            _try_import("sounddevice") and _try_import("numpy") and _try_import("soundfile")
        ),
        reason="sounddevice, numpy ou soundfile não instalados",
    )
    def test_capture_sem_frames_retorna_none(self):
        """duration_max_s=0 → max_frames=0 → loop nunca roda → sem áudio → None."""
        from bauer.audio_capture import capture_voice_input

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
            "bauer.transcription.transcribe_audio",
            return_value={"success": False, "transcript": "", "error": "silêncio"},
        ):
            capture_voice_input(duration_max_s=1, silence_threshold_db=-100, console=None)

        mock_stream.assert_not_called()
        assert mock_rec.called
