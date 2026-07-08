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


def test_capture_voice_default_message_uses_5s_silence_and_120s_max():
    """O prompt de voz deve refletir os novos defaults de conversa longa."""
    from bauer.audio_capture import capture_voice_input

    console = MagicMock()
    with patch("bauer.audio_capture._has_sounddevice", return_value=True), \
         patch("bauer.audio_capture._has_numpy", return_value=True), \
         patch("bauer.audio_capture.sd.InputStream", side_effect=KeyboardInterrupt):
        assert capture_voice_input(console=console) is None

    first_message = console.print.call_args_list[0][0][0]
    assert "silêncio de 5s" in first_message
    assert "max 120s" in first_message


class TestAudioCaptureSkipped:
    """Testa fallback when deps are unavailable — não tenta importar de verdade."""

    @pytest.mark.skipif(
        not (
            _try_import("sounddevice") and _try_import("numpy") and _try_import("soundfile")
        ),
        reason="sounddevice, numpy ou soundfile não instalados",
    )
    def test_capture_returns_text_or_none(self):
        """Se deps estão, capture retorna texto ou None."""
        from bauer.audio_capture import capture_voice_input

        # Moca sounddevice.InputStream e numpy
        with patch("bauer.audio_capture.sd.InputStream"):
            with patch("bauer.audio_capture.sd.rec") as mock_rec:
                with patch("bauer.audio_capture.np.concatenate") as mock_concat:
                    with patch("bauer.audio_capture.transcribe_audio") as mock_transcribe:
                        # Simula: nenhum áudio (frames vazio)
                        mock_rec.return_value = None
                        result = capture_voice_input(console=None)
                        # Sem frames, retorna None
                        assert result is None
