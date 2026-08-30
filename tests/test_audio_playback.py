"""Testes de playback de áudio (bauer/audio_playback.py)."""

from __future__ import annotations

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
