from __future__ import annotations

from unittest.mock import patch

import pytest

from bauer.voice_acoustic_wake import (
    AcousticWakeWordUnavailable,
    MicrophoneWakeWordMonitor,
    acoustic_backend_configured,
    build_acoustic_backend,
    capture_acoustic_command,
)


def test_acoustic_backend_is_opt_in():
    with patch.dict("os.environ", {}, clear=True):
        assert acoustic_backend_configured() is False
    with patch.dict("os.environ", {"BAUER_WAKE_BACKEND": "acoustic"}):
        assert acoustic_backend_configured() is True


def test_build_acoustic_backend_requires_model_name():
    with patch.dict("os.environ", {"BAUER_WAKE_BACKEND": "acoustic"}, clear=True):
        with pytest.raises(AcousticWakeWordUnavailable, match="BAUER_WAKE_MODEL"):
            build_acoustic_backend()


def test_openwakeword_backend_reports_missing_optional_dependency():
    with patch.dict("os.environ", {"BAUER_WAKE_MODEL": "hey_jarvis"}):
        with patch.dict("sys.modules", {"openwakeword": None, "openwakeword.model": None}):
            with pytest.raises(AcousticWakeWordUnavailable, match="openwakeword"):
                build_acoustic_backend()


def test_microphone_monitor_sets_event_when_backend_crosses_threshold():
    np = pytest.importorskip("numpy")

    detected: list[bool] = []

    class Backend:
        def score(self, _samples):
            return 0.8

    monitor = MicrophoneWakeWordMonitor(
        Backend(),
        threshold=0.5,
        on_detected=lambda: detected.append(True),
    )
    monitor._numpy = np
    monitor._on_audio(np.zeros((128, 1), dtype=np.int16), 128, None, None)

    assert monitor.wait(0) is True
    assert detected == [True]


def test_capture_command_reports_missing_audio_stack():
    with patch.object(MicrophoneWakeWordMonitor, "available", return_value=False):
        with pytest.raises(AcousticWakeWordUnavailable, match="sounddevice"):
            capture_acoustic_command(object())
