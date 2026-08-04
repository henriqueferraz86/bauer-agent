"""ui_frame.py — o quadro vivo do turno e o `suspend()` que mata o D5 na raiz.

## O problema que isto substitui

`Live`/`Status` do Rich e `input()` disputam o terminal: a thread de refresh
do display corrompe a leitura de stdin (o "bug do totodo" — texto digitado
sumindo/truncado, incidente real 2026-07-02). A correção de hoje é uma
ALLOWLIST mantida à mão: `_INTERACTIVE_TOOLS`, `_CONFIRM_CAPABLE_TOOLS` e
`_CONFIRM_EXEC_ACTIVE` em `bauer/agent.py` decidem, tool por tool, se um
spinner pode envolver a chamada — e esquecer uma tool nova na lista
reintroduz o bug.

## A ideia

Em vez de prever de antemão QUAIS tools vão pedir input, qualquer coisa que
segure a tela (um `console.status()`, ou o `TurnFrame` inteiro do HUD) se
ANUNCIA aqui com `register()`. Quem for chamar `input()` — em qualquer
profundidade, mesmo em `bauer/tools/agent_misc.py`, que nem importa
`agent.py` — só precisa envolver a leitura com `suspend()`: ele pausa TUDO
que estiver registrado, devolve o terminal, e retoma ao sair. Sem lista,
sem precisar saber o nome da tool.

O registro é uma pilha (`ContextVar`) porque `Status` (spinner de "pensando…"
ou "executando tool…") pode estar ativo DENTRO da região viva do `TurnFrame`
— os dois precisam pausar juntos, na ordem certa.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal, Protocol, runtime_checkable

from rich.console import Console, Group, RenderableType
from rich.text import Text

from .ui_hud import HudState, render_hud


def ui_glyphs():
    """Glifos ativos. Importado tarde para não criar ciclo com `ui.py`."""
    from .ui import active_glyphs

    return active_glyphs()


@runtime_checkable
class Pausable(Protocol):
    """O que `register()` aceita: `rich.status.Status`, `rich.live.Live` e
    `TurnFrame` cumprem isso sem adaptação — todos têm `start()`/`stop()`."""

    def stop(self) -> None: ...
    def start(self) -> None: ...


_stack: "ContextVar[tuple[Pausable, ...]]" = ContextVar("bauer_live_stack", default=())

#: O `TurnFrame` do turno em andamento, se houver. Existe para o código fundo
#: do loop (spinner de tool, streaming) descobrir que há um quadro ativo sem
#: precisar receber o objeto por parâmetro em toda a cadeia de chamadas —
#: `_run_tool_loop_body` → `_native_turn_interactive` → execução da tool são
#: três assinaturas de distância.
_current: "ContextVar[Any | None]" = ContextVar("bauer_turn_frame", default=None)


def current_frame() -> "Any | None":
    """O `TurnFrame` ativo, ou None fora de um turno (ou sem suporte a Live)."""
    frame = _current.get()
    if frame is not None and not getattr(frame, "is_live", False):
        # Frame existe mas o terminal não suportou Live: para quem chama, é o
        # mesmo que não haver quadro — assim o caminho antigo (spinner) volta a
        # valer sozinho, sem `if` espalhado.
        return None
    return frame


@contextmanager
def register(display: "Pausable"):
    """Anuncia um display ao vivo como suspensível enquanto o bloco roda."""
    token = _stack.set(_stack.get() + (display,))
    try:
        yield
    finally:
        _stack.reset(token)


@contextmanager
def suspend():
    """Pausa TODOS os displays ao vivo registrados, roda o bloco, retoma na
    ordem inversa. Sem nada registrado, é um no-op — barato de chamar sempre,
    mesmo em código que roda fora de qualquer display (ex.: `/loop`, testes).

    Nunca levanta por causa de um display quebrado: pausar é best-effort,
    porque um erro aqui não pode impedir o `input()` de rodar.
    """
    displays = _stack.get()
    if not displays:
        yield
        return
    paused: list[Pausable] = []
    for d in displays:
        try:
            d.stop()
            paused.append(d)
        except Exception as _exc:  # noqa: BLE001
            from .logging_config import log_suppressed as _log_sup
            _log_sup("ui_frame.suspend_stop", _exc)
    try:
        yield
    finally:
        for d in reversed(paused):
            try:
                d.start()
            except Exception as _exc:  # noqa: BLE001
                from .logging_config import log_suppressed as _log_sup
                _log_sup("ui_frame.suspend_start", _exc)


# ── o quadro do turno (HUD + trilho) ─────────────────────────────────────────


class TurnFrame:
    """Dono do ÚNICO `Live` do turno: HUD fixo embaixo, trilho de tools acima
    dele, histórico permanente rolando por cima via `print_above()`.

        ┌ histórico ─────────────┐  ← print_above(); console.print() normal
        │ ...                    │     funciona com um Live ativo — a linha
        ├ região viva (Live) ────┤     impressa sobe, a região viva não sai
        │ trilho de tools        │     do lugar (comportamento nativo do Rich)
        │ HUD                    │
        └────────────────────────┘

    Implementa o protocolo `Pausable` (`start`/`stop`) e se registra sozinho
    em `register()` ao entrar — qualquer `input()` sob ele já suspende certo,
    sem o chamador precisar saber que um `TurnFrame` existe.

    Best-effort por design: se o terminal não suportar Live (output
    capturado, outro Live já ativo fora deste módulo), os métodos viram no-op
    e `print_above` cai para `console.print` direto — o texto sai, só sem o
    HUD fixo.
    """

    def __init__(self, console: Console, *, refresh_per_second: int = 10) -> None:
        self.console = console
        self._refresh_per_second = refresh_per_second
        self._hud: "HudState | None" = None
        self._rail: "list[RenderableType]" = []
        self._live: Any = None
        # Guarda o MESMO objeto de contexto entre enter/exit — `register()` é
        # baseado em gerador (stateful); criar um novo em __exit__ chamaria
        # __exit__ de um contextmanager que nunca foi entrado, e o registro
        # original ficaria pendurado na pilha para sempre.
        self._register_cm: Any = None
        self._current_token: Any = None
        self._atividade: str = ""

    def __enter__(self) -> "TurnFrame":
        try:
            from rich.live import Live

            self._live = Live(
                self._render(),
                console=self.console,
                refresh_per_second=self._refresh_per_second,
                transient=False,
                vertical_overflow="visible",
            )
            self._live.__enter__()
        except Exception:  # noqa: BLE001
            self._live = None
        self._register_cm = register(self)
        self._register_cm.__enter__()
        self._current_token = _current.set(self)
        return self

    # `Literal[False]` e não `bool`: um `__exit__` que PODE devolver True engole
    # exceções, e o mypy avisa justamente porque a diferença é invisível na
    # leitura. Este quadro nunca engole — se o turno estourou, o erro sobe.
    def __exit__(self, *exc_info: Any) -> "Literal[False]":
        if self._current_token is not None:
            try:
                _current.reset(self._current_token)
            except Exception:  # noqa: BLE001
                _current.set(None)
            self._current_token = None
        if self._register_cm is not None:
            try:
                self._register_cm.__exit__(*exc_info)
            except Exception as _exc:  # noqa: BLE001
                from .logging_config import log_suppressed as _log_sup
                _log_sup("ui_frame.exit_register", _exc)
            self._register_cm = None
        if self._live is not None:
            try:
                self._live.__exit__(*exc_info)
            except Exception as _exc:  # noqa: BLE001
                from .logging_config import log_suppressed as _log_sup
                _log_sup("ui_frame.exit_live", _exc)
            self._live = None
        return False

    # ── conteúdo ─────────────────────────────────────────────────────────
    def print_above(self, renderable: "RenderableType | str") -> None:
        """Imprime algo PERMANENTE no histórico, acima da região viva."""
        target = self._live.console if self._live is not None else self.console
        try:
            target.print(renderable)
        except Exception as _exc:  # noqa: BLE001
            from .logging_config import log_suppressed as _log_sup
            _log_sup("ui_frame.print_above", _exc)

    def set_hud(self, state: "HudState | None") -> None:
        self._hud = state
        self._refresh()

    def set_rail(self, lines: "list[RenderableType]") -> None:
        self._rail = list(lines)
        self._refresh()

    def clear_rail(self) -> None:
        self.set_rail([])

    def set_atividade(self, texto: str) -> None:
        """O que está acontecendo agora ("executando run_command…").

        Substitui o `console.status()` durante o turno: o Rich só admite UM
        display ao vivo por vez, então abrir um spinner com o quadro ativo
        falharia (e o `_busy_spinner` cairia no fallback silencioso, deixando
        o usuário sem indicador nenhum — exatamente o vão que o spinner
        existia para cobrir).
        """
        self._atividade = texto or ""
        self._refresh()

    def _render(self) -> RenderableType:
        parts: "list[RenderableType]" = list(self._rail)
        if self._atividade:
            from . import theme

            g = ui_glyphs()
            linha = Text("  ")
            linha.append(f"{g.running} ", style=theme.ACCENT)
            linha.append(self._atividade, style=theme.DIM)
            parts.append(linha)
        if self._hud is not None:
            parts.append(render_hud(self._hud))
        return Group(*parts) if parts else Text("")

    def _refresh(self) -> None:
        if self._live is None:
            return
        try:
            self._live.update(self._render())
        except Exception as _exc:  # noqa: BLE001
            from .logging_config import log_suppressed as _log_sup
            _log_sup("ui_frame.refresh", _exc)

    # ── protocolo Pausable ───────────────────────────────────────────────
    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()

    def start(self) -> None:
        if self._live is not None:
            self._live.start()

    @property
    def is_live(self) -> bool:
        """False quando o terminal não suportou Live — quem chama pode usar
        isto para decidir entre HUD fixo e o comportamento antigo (spinner)."""
        return self._live is not None
