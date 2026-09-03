from __future__ import annotations

from unittest.mock import patch

from bauer.voice_status import collect_voice_status


def test_collect_voice_status_is_read_only_and_reports_components():
    available = {"sounddevice", "numpy", "soundfile", "faster_whisper", "openwakeword"}
    with patch("bauer.voice_status._available", side_effect=lambda module: module in available):
        with patch.dict("os.environ", {"BAUER_WAKE_MODEL": "hey_jarvis"}):
            with patch("bauer.voice_status.shutil.which", return_value="powershell"):
                status = collect_voice_status()

    assert {item["name"] for item in status} >= {
        "microfone / VAD",
        "STT local",
        "wake word acústica",
        "barge-in VAD + AEC",
    }
    assert all(item["ok"] for item in status)


def test_collect_voice_status_marks_missing_optional_stack():
    with patch("bauer.voice_status._available", return_value=False):
        with patch("bauer.voice_status.shutil.which", return_value=None):
            status = collect_voice_status()

    assert any(item["ok"] is False for item in status)
