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
        ), patch("sounddevice.InputStream") as mock_stream:
            capture_voice_input(duration_max_s=1, silence_threshold_db=-100, console=None)

        mock_stream.assert_not_called()
        assert mock_rec.called

    def test_nao_corta_gravacao_antes_de_comecar_a_falar(self):
        """Regressão: silêncio ANTES da pessoa começar a falar (tempo de
        reação normal) não deve contar como "fim de fala" — só silêncio
        DEPOIS de já ter detectado voz é que encerra a gravação. Sem isso,
        1s de silêncio inicial já cortava a gravação antes da fala real
        (bug relatado: transcrição vinha só "." — quase nada capturado)."""
        import numpy as np

        from bauer.audio_capture import capture_voice_input

        silence_chunk = np.zeros((1600, 1), dtype="float32")
        speech_chunk = np.full((1600, 1), 0.5, dtype="float32")  # amplitude alta

        # 3 chunks de "silêncio de reação" (300ms) -> 1 chunk de fala real ->
        # silêncio suficiente (>= silence_duration_s=1.0s => 10 chunks) p/ parar.
        sequence = [silence_chunk] * 3 + [speech_chunk] + [silence_chunk] * 12

        with patch("sounddevice.rec", side_effect=sequence), patch("sounddevice.wait"), patch(
            "bauer.transcription.transcribe_audio",
            return_value={"success": True, "transcript": "ola mundo", "provider": "local"},
        ) as mock_transcribe:
            result = capture_voice_input(
                duration_max_s=30, silence_threshold_db=-40, console=None
            )

        assert result == "ola mundo"
        assert mock_transcribe.called

    def test_desiste_se_nunca_comecar_a_falar(self):
        """Só silêncio do início ao fim (nunca fala) -> desiste no timeout de
        espera, sem chamar transcribe_audio (nada de útil pra transcrever)."""
        import numpy as np

        from bauer.audio_capture import capture_voice_input

        silence_chunk = np.zeros((1600, 1), dtype="float32")

        with patch("sounddevice.rec", return_value=silence_chunk), patch("sounddevice.wait"), patch(
            "bauer.transcription.transcribe_audio"
        ) as mock_transcribe:
            result = capture_voice_input(
                duration_max_s=30,
                silence_threshold_db=-40,
                max_wait_to_start_s=0.5,
                console=None,
            )

        assert result is None
        assert not mock_transcribe.called
