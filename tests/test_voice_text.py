from bauer.voice_text import strip_emoji_for_speech


def test_strip_emoji_for_speech_preserves_text_and_punctuation():
    assert strip_emoji_for_speech("🔊 Olá, tudo bem? ✅") == "Olá, tudo bem?"


def test_strip_emoji_for_speech_removes_emoji_only_response():
    assert strip_emoji_for_speech("🎤✨") == ""
