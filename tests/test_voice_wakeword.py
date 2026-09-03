from __future__ import annotations

from unittest.mock import patch

from bauer.voice_wakeword import (
    configured_wake_word,
    extract_command,
    is_wake_word_only,
)


def test_extracts_command_after_default_wake_word():
    assert extract_command("Bauer, leia o README") == "leia o README"


def test_wake_word_matching_ignores_accents_and_case():
    assert extract_command("BÁUER, abra o navegador") == "abra o navegador"


def test_does_not_match_word_inside_another_word():
    assert extract_command("o bauerei está pronto") is None


def test_wake_word_only_returns_empty_command():
    assert is_wake_word_only("bauer")
    assert extract_command("bauer") == ""


def test_configured_wake_word_uses_environment_with_fallback():
    with patch.dict("os.environ", {"BAUER_WAKE_WORD": "jarvis"}):
        assert configured_wake_word() == "jarvis"
    with patch.dict("os.environ", {"BAUER_WAKE_WORD": ""}):
        assert configured_wake_word() == "bauer"
