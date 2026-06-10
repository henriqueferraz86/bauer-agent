"""Tests for bauer/tui.py — BauerTUI + fallback."""

from __future__ import annotations

import pytest

from bauer.tui import (
    THEMES,
    _Msg,
    _make_style,
    _FallbackTUI,
    make_tui,
)


# ---------------------------------------------------------------------------
# THEMES
# ---------------------------------------------------------------------------


def test_themes_defined():
    assert "default" in THEMES
    assert "mono" in THEMES
    assert "dark" in THEMES


def test_all_themes_have_required_keys():
    required = {"header", "separator", "user_line", "user_prefix", "bot_line",
                "bot_prefix", "dim", "error", "cost", "input_area"}
    for theme_name, theme in THEMES.items():
        missing = required - set(theme.keys())
        assert not missing, f"Theme '{theme_name}' missing keys: {missing}"


def test_make_style_default():
    style = _make_style("default")
    # Should not raise
    assert style is not None


def test_make_style_unknown_falls_back_to_default():
    style = _make_style("nonexistent_theme")
    assert style is not None  # should fall back to default


# ---------------------------------------------------------------------------
# _Msg
# ---------------------------------------------------------------------------


def test_msg_creation():
    msg = _Msg("user", "hello world")
    assert msg.role == "user"
    assert msg.text == "hello world"
    assert msg.tokens_in == 0
    assert msg.tokens_out == 0
    assert msg.cost_usd == 0.0


def test_msg_with_cost():
    msg = _Msg("bot", "response", tokens_in=100, tokens_out=50, cost_usd=0.0025)
    assert msg.tokens_in == 100
    assert msg.tokens_out == 50
    assert msg.cost_usd == pytest.approx(0.0025)


# ---------------------------------------------------------------------------
# _FallbackTUI
# ---------------------------------------------------------------------------


def test_fallback_tui_init():
    def handler(text: str) -> str:
        return f"echo: {text}"

    tui = _FallbackTUI(handler)
    assert tui._handler is handler


def test_fallback_tui_handler():
    responses = []

    def handler(text: str) -> str:
        responses.append(text)
        return f"got: {text}"

    tui = _FallbackTUI(handler)
    result = tui._handler("test input")
    assert result == "got: test input"


# ---------------------------------------------------------------------------
# make_tui factory
# ---------------------------------------------------------------------------


def test_make_tui_returns_something():
    """make_tui should always return a TUI object (either BauerTUI or _FallbackTUI)."""
    def handler(text: str) -> str:
        return "ok"

    tui = make_tui(handler)
    assert tui is not None
    assert hasattr(tui, "_handler") or hasattr(tui, "run")


def test_make_tui_fallback_when_no_prompt_toolkit(monkeypatch):
    """When prompt_toolkit is unavailable, make_tui returns _FallbackTUI."""
    import bauer.tui as tui_module
    original = tui_module._PT_AVAILABLE
    try:
        monkeypatch.setattr(tui_module, "_PT_AVAILABLE", False)

        def handler(text: str) -> str:
            return "response"

        result = make_tui(handler)
        assert isinstance(result, _FallbackTUI)
    finally:
        monkeypatch.setattr(tui_module, "_PT_AVAILABLE", original)


# ---------------------------------------------------------------------------
# BauerTUI (prompt_toolkit-dependent tests) — skip if not available
# ---------------------------------------------------------------------------


try:
    from bauer.tui import BauerTUI, _PT_AVAILABLE
    PT_AVAILABLE = _PT_AVAILABLE
except ImportError:
    PT_AVAILABLE = False

pytestmark_pt = pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_init():
    def handler(text: str) -> str:
        return f"response to {text}"

    from bauer.tui import BauerTUI
    tui = BauerTUI(handler, theme="default", model_name="test-model")
    assert tui is not None
    assert tui._model_name == "test-model"


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_add_user_message():
    from bauer.tui import BauerTUI
    messages_seen = []

    def handler(text: str) -> str:
        messages_seen.append(text)
        return "ok"

    tui = BauerTUI(handler, theme="mono")
    tui.add_user_message("hello")
    assert len(tui._messages) == 1
    assert tui._messages[0].role == "user"
    assert tui._messages[0].text == "hello"


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_add_bot_message():
    from bauer.tui import BauerTUI

    def handler(text: str) -> str:
        return "response"

    tui = BauerTUI(handler)
    tui.add_bot_message("response text", tokens_in=50, tokens_out=100, cost_usd=0.001)
    assert len(tui._messages) == 1
    msg = tui._messages[0]
    assert msg.role == "bot"
    assert msg.text == "response text"
    assert msg.tokens_in == 50
    assert msg.tokens_out == 100


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_clear_messages():
    from bauer.tui import BauerTUI

    def handler(text: str) -> str:
        return "ok"

    tui = BauerTUI(handler)
    tui.add_user_message("msg 1")
    tui.add_bot_message("response 1")
    assert len(tui._messages) == 2
    tui.clear_messages()
    assert len(tui._messages) == 0


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_append_token():
    from bauer.tui import BauerTUI

    def handler(text: str) -> str:
        return "ok"

    tui = BauerTUI(handler)
    tui.append_token("Hello")
    tui.append_token(" World")
    assert tui._is_streaming is True
    assert "".join(tui._streaming_tokens) == "Hello World"


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_add_error():
    from bauer.tui import BauerTUI

    def handler(text: str) -> str:
        return "ok"

    tui = BauerTUI(handler)
    tui.add_error("something went wrong")
    assert len(tui._messages) == 1
    assert tui._messages[0].role == "error"


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_render_history_no_app():
    """_render_history should work even without _app set (no event loop)."""
    from bauer.tui import BauerTUI

    def handler(text: str) -> str:
        return "ok"

    tui = BauerTUI(handler)
    tui.add_user_message("hello")
    tui.add_bot_message("world", cost_usd=0.001)
    result = tui._render_history()
    # Just check it doesn't raise — result is a FormattedText list
    assert result is not None


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_all_themes():
    """BauerTUI initialises without error for all themes."""
    from bauer.tui import BauerTUI

    def handler(text: str) -> str:
        return "ok"

    for theme in THEMES:
        tui = BauerTUI(handler, theme=theme)
        assert tui is not None


@pytest.mark.skipif(not PT_AVAILABLE, reason="prompt_toolkit not installed")
def test_bauer_tui_max_messages_limit():
    """Messages deque respects maxlen."""
    from bauer.tui import BauerTUI

    def handler(text: str) -> str:
        return "ok"

    tui = BauerTUI(handler, max_messages=5)
    for i in range(10):
        tui.add_user_message(f"message {i}")
    assert len(tui._messages) == 5
