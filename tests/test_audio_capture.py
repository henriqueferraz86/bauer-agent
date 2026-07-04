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
        """duration_max_s=0 → max_frames=0 → loop nunca roda → sem áudio → None.

        sd/np são import lazy DENTRO de capture_voice_input (não no nível do
        módulo), então o patch precisa mirar sounddevice.InputStream direto.
        """
        from bauer.audio_capture import capture_voice_input

        with patch("sounddevice.InputStream"):
            result = capture_voice_input(duration_max_s=0, console=None)
            assert result is None
