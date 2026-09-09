"""Primitivas de interação Rich para a sessão CLI do agente."""

from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def busy_spinner(console, text: str):
    """Exibe atividade sem permitir que a camada visual derrube o turno."""
    from .ui_frame import current_frame, register

    frame = current_frame()
    if frame is not None:
        from rich.text import Text
        frame.set_atividade(Text.from_markup(text).plain.strip())
        try:
            yield
        finally:
            frame.set_atividade("")
        return
    status = None
    try:
        status = console.status(text, spinner="dots")
        status.__enter__()
    except Exception:  # noqa: BLE001 -- indicador nunca bloqueia execução
        status = None
    if status is None:
        yield
        return
    with register(status):
        try:
            yield
        finally:
            try:
                status.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 -- cleanup de UI é best-effort
                pass


def thinking_status(console, model_name: str):
    return busy_spinner(console, f"[dim]{model_name} pensando… (Ctrl+C interrompe)[/dim]")


def tool_exec_status(console, name: str):
    return busy_spinner(console, f"[dim]executando {name}… (Ctrl+C interrompe)[/dim]")


def prompt_cmd_decision(console, title: str, body: str) -> str:
    """Solicita decisão para os dois gates de shell e retorna a política escolhida."""
    from .ui import approval_card, approval_options
    from .ui_frame import suspend

    console.print()
    console.print(approval_card(title, body))
    console.print(approval_options())
    try:
        with suspend():
            raw = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "deny"
    decision = {"e": "once", "o": "once", "1": "once", "s": "session", "a": "always",
                "n": "deny", "d": "deny", "": "deny"}.get(raw[:1], "deny")
    label = {"once": "executando uma vez", "session": "liberado nesta sessão",
             "always": "aprendido (liberado sempre)", "deny": "negado"}[decision]
    console.print(f"  [{'green' if decision != 'deny' else 'red'}]{label}[/]")
    return decision
