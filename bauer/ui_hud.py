"""ui_hud.py — o painel de instrumentos do turno (plano 028 F2).

Hoje o estado da sessão (modelo, contexto, custo) só existe na `bottom_toolbar`
do prompt_toolkit — e ela é renderizada pelo MECANISMO DE PROMPT, não por um
layout persistente. Existe enquanto o `input()` espera; some no Enter, que é
justamente quando a máquina começa a trabalhar (D3 do plano 028).

Este módulo não resolve isso sozinho — só define o ESTADO e o RENDER, puros e
testáveis (`HudState` + `render_hud`), do mesmo jeito que `ui.py` faz para as
outras peças do kit. Quem mantém o HUD vivo *durante* o turno é `ui_frame.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from . import theme, ui

#: Estados do Kernel, na ordem em que a esteira acende (ver
#: bauer/core/kernel/states.py:KERNEL_TRANSITIONS). "completed"/"failed"/
#: "cancelled" são terminais e não aparecem na esteira — o turno já colapsou.
_ESTEIRA = ("planning", "policy_check", "queued", "running", "evaluating")

#: Status que o Kernel publica e que NÃO são estado de run. `policy.evaluated`
#: manda `status=decision.action` — "allow"/"deny"/"ask" — no mesmo campo que
#: os demais eventos usam para o estado. Ler o campo cru colocaria "allow" na
#: esteira como se fosse uma etapa do turno.
_STATUS_NAO_E_ESTADO = frozenset({"allow", "deny", "ask", "warn"})


def estado_do_evento(event_type: str, status: "str | None") -> "str | None":
    """Traduz um evento do Kernel no estado que a esteira deve mostrar.

    Devolve `None` quando o evento não carrega estado de run — o chamador
    mantém o que já tinha em vez de apagar a esteira.

    Existe porque o Kernel publica estado por CINCO tópicos diferentes
    (`run.planning.started`, `run.state.changed`, `run.replanning`,
    `run.validation.started`, `run.progress.warning`), não só por
    `run.state.changed`. Assinar apenas esse tópico — como o plano 028 dizia
    originalmente — perderia planning e evaluating, ou seja, metade da esteira.
    """
    if not status:
        return None
    s = str(status).strip().lower()
    if not s or s in _STATUS_NAO_E_ESTADO:
        return None
    return s


@dataclass(frozen=True)
class HudState:
    """Retrato do turno num instante. Imutável — cada atualização cria um novo.

    `kernel_estado=None` significa Kernel desligado: a esteira some do render
    em vez de inventar um estado que não existe (ver plano 028 F2, "dado real,
    não enfeite").
    """

    local: bool = True
    modelo: str = ""
    ctx_pct: float = 0.0
    custo_usd: float = 0.0
    tokens_por_segundo: float = 0.0
    rodada: int = 0
    kernel_estado: "str | None" = None
    comandos: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _selo(local: bool) -> Text:
    g = ui.active_glyphs()
    if local:
        return Text(f"{g.seal_local} local", style=f"bold {theme.ACCENT_TEXT}")
    return Text(f"{g.seal_cloud} nuvem", style=f"bold {theme.CLOUD}")


def _custo(usd: float) -> str:
    if usd <= 0:
        return "$0,00"
    if usd < 0.01:
        return f"${usd:.4f}"
    return f"${usd:.2f}"


def _esteira(estado: "str | None") -> "Text | None":
    """`⟐⟐⟐◦` — acende até o estado atual, apaga o resto. None = Kernel
    desligado, a esteira inteira some (não aparece cinza, some de verdade)."""
    if estado is None:
        return None
    g = ui.active_glyphs()
    t = Text()
    if estado not in _ESTEIRA:
        # terminal (completed/failed/cancelled) ou estado não mapeado: esteira
        # cheia — o turno chegou ao fim, não há "próximo passo" a apagar.
        t.append(g.step_on * len(_ESTEIRA), style=theme.ACCENT)
        t.append(f" {estado}", style=theme.DIM)
        return t
    pos = _ESTEIRA.index(estado)
    t.append(g.step_on * (pos + 1), style=theme.ACCENT)
    t.append(g.step_off * (len(_ESTEIRA) - pos - 1), style=theme.FAINT)
    t.append(f" {estado}", style=theme.DIM)
    return t


def render_hud(state: HudState) -> RenderableType:
    """A linha de rodapé: selo · modelo · contexto · custo · tok/s · esteira.

    Cada campo só aparece se tiver conteúdo — turno sem custo não imprime
    "$0,00" cinza ocupando espaço à toa, e tok/s só aparece quando > 0 (turno
    ainda não gerou nada, ou terminou e o valor foi zerado pelo chamador).
    """
    partes: list[RenderableType] = [_selo(state.local)]

    if state.modelo:
        partes.append(Text(state.modelo, style=theme.DIM))

    if state.ctx_pct > 0:
        rotulo = Text("ctx ", style=theme.DIM)
        rotulo.append_text(ui.context_gauge(state.ctx_pct, width=8))
        partes.append(rotulo)

    partes.append(Text(_custo(state.custo_usd), style=theme.DIM))

    if state.tokens_por_segundo > 0:
        partes.append(Text(f"{state.tokens_por_segundo:.1f} tok/s", style=theme.ACCENT_TEXT))

    esteira = _esteira(state.kernel_estado)
    if esteira is not None:
        partes.append(esteira)

    linha = Text("  ").join(p if isinstance(p, Text) else Text.from_markup(str(p)) for p in partes)

    corpo: list[RenderableType] = [linha]
    if state.comandos:
        cmd_line = Text()
        for idx, (cmd, desc) in enumerate(state.comandos):
            if idx:
                cmd_line.append("   ")
            cmd_line.append(cmd, style=f"bold {theme.ACCENT_TEXT}")
            cmd_line.append(f" {desc}", style=theme.FAINT)
        corpo.append(cmd_line)

    grid = Table.grid(padding=0)
    grid.add_column()
    for r in corpo:
        grid.add_row(r)
    return grid


def render_hud_html(state: HudState) -> str:
    """O MESMO estado, no transporte do prompt_toolkit (`HTML`).

    Existem dois renders porque existem dois mecanismos de desenho, não duas
    fontes de verdade: enquanto o prompt espera input, quem desenha o rodapé é
    o prompt_toolkit (`bottom_toolbar`); durante o turno, é o `Live` do Rich
    (`ui_frame.TurnFrame`). O estado que alimenta os dois é o mesmo `HudState`
    — antes do F2 a toolbar montava o HTML na mão, com a paleta antiga
    hardcoded, e por isso escapou inteira do F0.

    Devolve string (não `HTML`) para este módulo não depender de
    prompt_toolkit; quem chama envolve em `HTML(...)`.
    """
    import html as _html

    g = ui.active_glyphs()
    partes: list[str] = []

    if state.local:
        partes.append(
            f"<b><style fg='{theme.ACCENT_TEXT}'>{g.seal_local} local</style></b>"
        )
    else:
        partes.append(
            f"<b><style fg='{theme.CLOUD}'>{g.seal_cloud} nuvem</style></b>"
        )

    if state.modelo:
        partes.append(f"<style fg='{theme.DIM}'>{_html.escape(state.modelo)}</style>")

    if state.ctx_pct > 0:
        pct = max(0.0, min(1.0, state.ctx_pct))
        largura = 8
        cheio = round(pct * largura)
        perigo = pct > 0.85
        cor_barra = theme.BAD if perigo else theme.ACCENT
        cor_pct = theme.BAD if perigo else theme.DIM
        partes.append(
            f"ctx <style fg='{cor_barra}'>{g.gauge_full * cheio}</style>"
            f"<style fg='{theme.FAINT}'>{g.gauge_empty * (largura - cheio)}</style>"
            f" <style fg='{cor_pct}'>{int(pct * 100)}%</style>"
        )

    partes.append(f"<style fg='{theme.DIM}'>{_html.escape(_custo(state.custo_usd))}</style>")

    if state.tokens_por_segundo > 0:
        partes.append(
            f"<style fg='{theme.ACCENT_TEXT}'>{state.tokens_por_segundo:.1f} tok/s</style>"
        )

    if state.comandos:
        atalhos = " · ".join(cmd for cmd, _ in state.comandos)
        partes.append(f"<style fg='{theme.FAINT}'>{_html.escape(atalhos)}</style>")

    return " " + "  ·  ".join(partes) + " "
