"""Testes de playback de áudio (bauer/audio_playback.py)."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

from bauer.audio_playback import play_audio_file


class TestPlaybackDeps:
    def test_missing_deps_returns_false(self, tmp_path):
        p = tmp_path / "a.wav"
        p.write_bytes(b"fake")
        with patch("bauer.audio_playback._has_sounddevice", return_value=False):
            assert play_audio_file(p) is False

    def test_missing_soundfile_returns_false(self, tmp_path):
        p = tmp_path / "a.wav"
        p.write_bytes(b"fake")
        with patch("bauer.audio_playback._has_sounddevice", return_value=True), patch(
            "bauer.audio_playback._has_soundfile", return_value=False
        ):
            assert play_audio_file(p) is False


class TestPlaybackFile:
    def test_missing_file_returns_false(self, tmp_path):
        with patch("bauer.audio_playback._has_sounddevice", return_value=True), patch(
            "bauer.audio_playback._has_soundfile", return_value=True
        ):
            assert play_audio_file(tmp_path / "nao-existe.wav") is False

    def test_plays_and_waits_by_default(self, tmp_path):
        p = tmp_path / "a.wav"
        p.write_bytes(b"fake")
        fake_sd = MagicMock()
        fake_sf = MagicMock()
        fake_sf.read.return_value = ([0.0, 0.1], 16000)
        with patch("bauer.audio_playback._has_sounddevice", return_value=True), \
             patch("bauer.audio_playback._has_soundfile", return_value=True), \
             patch("bauer.audio_playback.sd", fake_sd), \
             patch("bauer.audio_playback.sf", fake_sf):
            assert play_audio_file(p) is True

        fake_sf.read.assert_called_once_with(str(p), dtype="float32")
        fake_sd.play.assert_called_once_with([0.0, 0.1], 16000)
        fake_sd.wait.assert_called_once()

    def test_non_blocking_skips_wait(self, tmp_path):
        p = tmp_path / "a.wav"
        p.write_bytes(b"fake")
        fake_sd = MagicMock()
        fake_sf = MagicMock()
        fake_sf.read.return_value = ([0.0], 16000)
        with patch("bauer.audio_playback._has_sounddevice", return_value=True), \
             patch("bauer.audio_playback._has_soundfile", return_value=True), \
             patch("bauer.audio_playback.sd", fake_sd), \
             patch("bauer.audio_playback.sf", fake_sf):
            assert play_audio_file(p, blocking=False) is True

        fake_sd.wait.assert_not_called()

    def test_playback_error_returns_false_not_raise(self, tmp_path):
        p = tmp_path / "a.wav"
        p.write_bytes(b"fake")
        fake_sd = MagicMock()
        fake_sd.play.side_effect = RuntimeError("no output device")
        fake_sf = MagicMock()
        fake_sf.read.return_value = ([0.0], 16000)
        with patch("bauer.audio_playback._has_sounddevice", return_value=True), \
             patch("bauer.audio_playback._has_soundfile", return_value=True), \
             patch("bauer.audio_playback.sd", fake_sd), \
             patch("bauer.audio_playback.sf", fake_sf):
            assert play_audio_file(p) is False

    def test_hung_playback_times_out_instead_of_blocking_forever(self, tmp_path):
        """Achado real: numa maquina sem placa de som, sd.play()/sd.wait()
        podem travar em vez de levantar (PortAudio esperando um dispositivo
        que nunca aparece) — reproduzido rodando de verdade num servidor
        headless. play_audio_file precisa devolver False num prazo curto,
        nao ficar preso para sempre."""
        p = tmp_path / "a.wav"
        p.write_bytes(b"fake")
        fake_sd = MagicMock()
        # sd.wait() nunca retorna — simula o hang real do PortAudio.
        fake_sd.wait.side_effect = lambda: threading.Event().wait()
        fake_sf = MagicMock()
        fake_sf.read.return_value = ([0.0], 16000)

        with patch("bauer.audio_playback._has_sounddevice", return_value=True), \
             patch("bauer.audio_playback._has_soundfile", return_value=True), \
             patch("bauer.audio_playback.sd", fake_sd), \
             patch("bauer.audio_playback.sf", fake_sf):
            start = time.monotonic()
            result = play_audio_file(p, timeout=0.2)
            elapsed = time.monotonic() - start

        assert result is False
        # Devolveu perto do timeout pedido, nao ficou preso indefinidamente.
        assert elapsed < 2.0
