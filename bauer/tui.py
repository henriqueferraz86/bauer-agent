"""BauerTUI — prompt_toolkit Application with fixed input area.

Provides a proper terminal UI with:
  - Scrollable message history panel (top)
  - Fixed separator line
  - Fixed single-line input area (bottom)
  - Streaming output support: call ``append_token(text)`` from a thread
  - Key bindings: Enter=submit, Ctrl+C=interrupt, Ctrl+L=clear, F1=help
  - 3 themes: default (Catppuccin-inspired), mono, dark

Usage::

    from bauer.tui import BauerTUI

    def my_handler(user_input: str) -> str:
        return f"Echo: {user_input}"

    tui = BauerTUI(handler=my_handler, theme="default")
    tui.run()          # blocks until /exit
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable

_PT_AVAILABLE = False
try:
    from prompt_toolkit import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.formatted_text import HTML, FormattedText
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import (
        Float, FloatContainer, HSplit, Window,
    )
    from prompt_toolkit.layout.controls import (
        BufferControl, FormattedTextControl,
    )
    from prompt_toolkit.layout.dimension import Dimension as D
    from prompt_toolkit.layout.screen import Point
    from prompt_toolkit.styles import Style
    _PT_AVAILABLE = True
except ImportError:
    pass


# Gradiente teal→azul→roxo (mesma paleta do logo BAUER no ascii_intro).
_LOGO_GRADIENT = ["#00d4aa", "#3b82f6", "#a855f7"]


def _grad_logo_fragments(text: str = "BAUER") -> list:
    """Fragmentos prompt_toolkit com 'BAUER' em gradiente horizontal."""
    def _lerp(c1: str, c2: str, t: float) -> str:
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        return f"#{round(r1+(r2-r1)*t):02x}{round(g1+(g2-g1)*t):02x}{round(b1+(b2-b1)*t):02x}"

    def _color(frac: float) -> str:
        stops = _LOGO_GRADIENT
        if frac <= 0:
            return stops[0]
        if frac >= 1:
            return stops[-1]
        seg = frac * (len(stops) - 1)
        i = int(seg)
        return _lerp(stops[i], stops[i + 1], seg - i)

    n = len(text)
    return [(f"{_color(i / max(n - 1, 1))} bold", ch) for i, ch in enumerate(text)]


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

THEMES: dict[str, dict[str, str]] = {
    "default": {
        # Catppuccin Mocha palette
        "header":      "bg:#1e1e2e #cdd6f4 bold",
        "separator":   "bg:#313244 #6c7086",
        "user_line":   "#89b4fa bold",
        "user_prefix": "#89b4fa bold",
        "bot_line":    "#a6e3a1",
        "bot_prefix":  "#a6e3a1 bold",
        "dim":         "#6c7086",
        "error":       "#f38ba8",
        "cost":        "#94e2d5",
        "input_area":  "bg:#181825 #cdd6f4",
        "cursor":      "#f5c2e7",
    },
    "mono": {
        "header":      "bold",
        "separator":   "",
        "user_line":   "bold",
        "user_prefix": "bold",
        "bot_line":    "",
        "bot_prefix":  "bold",
        "dim":         "",
        "error":       "bold",
        "cost":        "",
        "input_area":  "",
        "cursor":      "",
    },
    "dark": {
        "header":      "bg:#0d1117 #e6edf3 bold",
        "separator":   "bg:#21262d #484f58",
        "user_line":   "#58a6ff bold",
        "user_prefix": "#58a6ff bold",
        "bot_line":    "#56d364",
        "bot_prefix":  "#56d364 bold",
        "dim":         "#484f58",
        "error":       "#f85149",
        "cost":        "#39d353",
        "input_area":  "bg:#0d1117 #e6edf3",
        "cursor":      "#d2a8ff",
    },
}

_DEFAULT_THEME = "default"


def _make_style(theme_name: str) -> "Style":
    t = THEMES.get(theme_name, THEMES[_DEFAULT_THEME])
    return Style.from_dict({
        "header":      t["header"],
        "separator":   t["separator"],
        "user-prefix": t["user_prefix"],
        "user-line":   t["user_line"],
        "bot-prefix":  t["bot_prefix"],
        "bot-line":    t["bot_line"],
        "dim":         t["dim"],
        "error-line":  t["error"],
        "cost-line":   t["cost"],
        "input-area":  t["input_area"],
    })


# ---------------------------------------------------------------------------
# Message model
# ---------------------------------------------------------------------------

class _Msg:
    __slots__ = ("role", "text", "tokens_in", "tokens_out", "cost_usd")

    def __init__(
        self,
        role: str,
        text: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        self.role = role
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost_usd = cost_usd


# ---------------------------------------------------------------------------
# BauerTUI
# ---------------------------------------------------------------------------

class BauerTUI:
    """prompt_toolkit-based terminal UI with fixed input area.

    Parameters
    ----------
    handler:
        Synchronous callable ``(user_text: str) -> str`` that returns the
        assistant response. Called in a background thread so the UI stays
        responsive. May call ``tui.append_token(tok)`` for streaming.
    theme:
        One of ``"default"``, ``"mono"``, ``"dark"``.
    history_file:
        Path for persistent input history (``~/.bauer/.cli_history``).
    max_messages:
        Maximum messages kept in the scrollable history (default 500).
    model_name:
        Displayed in the header bar.
    """

    def __init__(
        self,
        handler: Callable[[str], str],
        *,
        theme: str = "default",
        history_file: str | None = None,
        max_messages: int = 500,
        model_name: str = "bauer",
        provider: str = "",
        context_tokens: int | str = "",
    ) -> None:
        if not _PT_AVAILABLE:
            raise ImportError(
                "prompt_toolkit is required for BauerTUI. "
                "Install with: pip install prompt-toolkit"
            )
        self._handler = handler
        self._theme = theme
        self._style = _make_style(theme)
        self._max_msgs = max_messages
        self._model_name = model_name
        self._provider = provider
        self._context_tokens = context_tokens
        # Linha do "cursor" no fim do histórico — usada para ancorar a rolagem
        # no rodapé (mensagens antigas saem por cima, recentes ficam visíveis).
        self._last_line_y = 0

        # Message history (thread-safe via lock)
        self._messages: deque[_Msg] = deque(maxlen=max_messages)
        self._lock = threading.Lock()

        # Streaming token buffer for current bot response
        self._streaming_tokens: list[str] = []
        self._is_streaming = False

        # Flag to exit the main loop
        self._should_exit = False

        # Callback to refresh the display from any thread
        self._app: "Application | None" = None

        # Input history
        if history_file:
            try:
                _hist: "FileHistory | InMemoryHistory" = FileHistory(history_file)
            except Exception:
                _hist = InMemoryHistory()
        else:
            _hist = InMemoryHistory()
        self._history = _hist

        # Build the prompt_toolkit layout
        self._input_buffer = Buffer(history=self._history, multiline=False)
        self._history_control = FormattedTextControl(
            self._render_history,
            focusable=False,
            # Cursor invisível na última linha → a Window rola para mostrar o
            # rodapé. As mensagens antigas saem por cima conforme chegam novas.
            get_cursor_position=lambda: Point(x=0, y=self._last_line_y),
        )
        self._layout = self._build_layout()
        self._kb = self._build_keybindings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_user_message(self, text: str) -> None:
        with self._lock:
            self._messages.append(_Msg("user", text))
        self._refresh()

    def add_bot_message(
        self,
        text: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            self._messages.append(_Msg("bot", text, tokens_in, tokens_out, cost_usd))
            self._is_streaming = False
            self._streaming_tokens = []
        self._refresh()

    def add_error(self, text: str) -> None:
        with self._lock:
            self._messages.append(_Msg("error", text))
        self._refresh()

    def append_token(self, token: str) -> None:
        """Append a streaming token to the current bot response bubble."""
        with self._lock:
            self._is_streaming = True
            self._streaming_tokens.append(token)
        self._refresh()

    def clear_messages(self) -> None:
        with self._lock:
            self._messages.clear()
            self._streaming_tokens = []
            self._is_streaming = False
        self._refresh()

    def run(self) -> None:
        """Run the TUI event loop (blocking)."""
        self._app = Application(
            layout=self._layout,
            style=self._style,
            key_bindings=self._kb,
            full_screen=True,
            mouse_support=False,
            refresh_interval=0.1,  # poll for streaming token updates
        )
        self._app.run()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _header_fragments(self) -> "FormattedText":
        """Header em 2 linhas: logo BAUER em gradiente + info + comandos."""
        frags: list[tuple[str, str]] = []
        # Linha 1: BAUER (gradiente) · modelo (provider) · contexto
        frags.extend(_grad_logo_fragments("BAUER"))
        frags.append(("class:dim", "  ·  "))
        frags.append(("class:bot-prefix", self._model_name))
        if self._provider:
            frags.append(("class:dim", f" ({self._provider})"))
        if self._context_tokens not in ("", None):
            try:
                _ctx = f"{int(self._context_tokens):,}".replace(",", ".")
            except (ValueError, TypeError):
                _ctx = str(self._context_tokens)
            frags.append(("class:dim", f"  ·  {_ctx} tokens"))
        frags.append(("", "\n"))
        # Linha 2: comandos
        for cmd, desc in (
            ("/model", "trocar"), ("/status", "stats"),
            ("/clear", "limpar"), ("/memory", "memoria"), ("/exit", "sair"),
        ):
            frags.append(("class:cost-line", cmd))
            frags.append(("class:dim", f" {desc}   "))
        return FormattedText(frags)

    def _build_layout(self) -> "Layout":
        header = Window(
            FormattedTextControl(self._header_fragments, focusable=False),
            height=2,
        )

        history_window = Window(
            self._history_control,
            wrap_lines=True,
            style="class:history",
        )

        sep = Window(
            FormattedTextControl(
                lambda: FormattedText([("class:separator", "─" * 200)])
            ),
            height=1,
            style="class:separator",
        )

        root = HSplit([
            header,
            history_window,
            sep,
            Window(
                BufferControl(buffer=self._input_buffer),
                height=1,
                style="class:input-area",
                get_line_prefix=lambda line_number, wrap_count: _grad_logo_fragments("›") + [("", " ")],
            ),
        ])

        return Layout(root)

    def _build_keybindings(self) -> "KeyBindings":
        kb = KeyBindings()

        @kb.add("enter")
        def _on_enter(event):
            text = self._input_buffer.text.strip()
            self._input_buffer.reset()
            if not text:
                return
            if text.lower() in ("/exit", "/quit", "exit", "quit"):
                self._should_exit = True
                event.app.exit()
                return
            if text.lower() in ("/clear", "clear"):
                self.clear_messages()
                return
            self._submit(text, event.app)

        @kb.add("c-c")
        def _on_ctrl_c(event):
            """Interrupt current generation or exit if idle."""
            if self._is_streaming:
                # Signal interrupt — the handler loop will notice
                self._is_streaming = False
                self.add_bot_message("[interrompido]")
            else:
                event.app.exit()

        @kb.add("c-l")
        def _on_ctrl_l(event):
            self.clear_messages()

        @kb.add("f1")
        def _on_f1(event):
            help_text = (
                "Comandos: /exit /clear /model /memory /status /agents /spec "
                "| Ctrl+C=interromper | Ctrl+L=limpar | ↑↓=histórico"
            )
            self.add_bot_message(help_text)

        return kb

    # ------------------------------------------------------------------
    # Submit + handler
    # ------------------------------------------------------------------

    def _submit(self, text: str, app: "Application") -> None:
        self.add_user_message(text)

        def _worker():
            try:
                result = self._handler(text)
                if not self._is_streaming:
                    # handler returned a full response (non-streaming)
                    self.add_bot_message(result)
            except Exception as exc:
                self.add_error(f"Erro: {exc}")

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_history(self) -> "FormattedText":
        lines: list[tuple[str, str]] = []
        with self._lock:
            msgs = list(self._messages)
            streaming = list(self._streaming_tokens) if self._is_streaming else []

        for msg in msgs:
            if msg.role == "user":
                lines.append(("class:user-prefix", "you> "))
                lines.append(("class:user-line", msg.text))
                lines.append(("", "\n"))
            elif msg.role == "bot":
                lines.append(("class:bot-prefix", "bauer> "))
                lines.append(("class:bot-line", msg.text))
                lines.append(("", "\n"))
                if msg.tokens_in or msg.cost_usd:
                    _cost = f"${msg.cost_usd:.4f}" if msg.cost_usd else ""
                    _toks = f"↑{msg.tokens_in} ↓{msg.tokens_out}" if msg.tokens_in else ""
                    _meta = " | ".join(filter(None, [_toks, _cost]))
                    lines.append(("class:cost-line", f"  [{_meta}]\n"))
            elif msg.role == "error":
                lines.append(("class:error-line", f"[erro] {msg.text}\n"))

        # Streaming partial response
        if streaming:
            partial = "".join(streaming)
            lines.append(("class:bot-prefix", "bauer> "))
            lines.append(("class:bot-line", partial))
            lines.append(("class:dim", " ▋"))  # blinking cursor simulation
            lines.append(("", "\n"))

        # Conta as linhas para posicionar o "cursor" no fim → rola pro rodapé.
        total_lines = sum(text.count("\n") for _style, text in lines)
        self._last_line_y = max(0, total_lines)

        return FormattedText(lines)

    def _refresh(self) -> None:
        """Request an application repaint from any thread."""
        app = self._app
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Fallback: plain readline-based loop when prompt_toolkit is unavailable
# ---------------------------------------------------------------------------

class _FallbackTUI:
    """Minimal stdin/stdout fallback when prompt_toolkit is not installed."""

    def __init__(self, handler: Callable[[str], str], **kwargs) -> None:
        self._handler = handler

    def run(self) -> None:
        import sys
        print("Bauer Agent (modo simples — instale prompt-toolkit para TUI completa)")
        print("Digite /exit para sair.\n")
        while True:
            try:
                text = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nAté logo.")
                return
            if not text:
                continue
            if text.lower() in ("/exit", "/quit"):
                print("Até logo.")
                return
            try:
                result = self._handler(text)
                print(f"bauer> {result}\n")
            except Exception as exc:
                print(f"[erro] {exc}\n")


def make_tui(
    handler: Callable[[str], str],
    *,
    theme: str = "default",
    history_file: str | None = None,
    model_name: str = "bauer",
    provider: str = "",
    context_tokens: int | str = "",
) -> "BauerTUI | _FallbackTUI":
    """Factory: return BauerTUI if prompt_toolkit is available, else fallback."""
    if _PT_AVAILABLE:
        return BauerTUI(
            handler,
            theme=theme,
            history_file=history_file,
            model_name=model_name,
            provider=provider,
            context_tokens=context_tokens,
        )
    return _FallbackTUI(handler, theme=theme)
