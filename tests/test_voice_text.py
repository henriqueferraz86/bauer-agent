from bauer.voice_text import strip_emoji_for_speech


def test_strip_emoji_for_speech_preserves_text_and_punctuation():
    assert strip_emoji_for_speech("🔊 Olá, tudo bem? ✅") == "Olá, tudo bem?"


def test_strip_emoji_for_speech_removes_emoji_only_response():
    assert strip_emoji_for_speech("🎤✨") == ""


def test_strip_emoji_for_speech_removes_markdown_and_ui_symbols():
    text = "* Status: pronto • containers: 3"
    assert strip_emoji_for_speech(text) == "Status pronto containers 3"


def test_strip_emoji_for_speech_preserves_sentence_punctuation():
    assert strip_emoji_for_speech("Olá, Bauer. Tudo bem?") == "Olá, Bauer. Tudo bem?"
