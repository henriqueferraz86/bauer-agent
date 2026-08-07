"""Comando bauer auth."""

from __future__ import annotations

import typer


auth_app = typer.Typer(help="Autenticacao com providers cloud (OAuth/API Key)")


@auth_app.command("login")
def auth_login(
    provider: str = typer.Option(
        "",
        "--provider", "-p",
        help=(
            "Provider a autenticar (omita para menu interativo).\n"
            "API Key:     openai-api | anthropic | groq | deepseek | openrouter |\n"
            "             mistral | xai | together | gemini | custom\n"
            "Device Flow: github | copilot\n"
            "OAuth:       openai"
        ),
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help=(
            "Nao abrir browser (host headless / SSH): imprime a URL e aceita "
            "a URL de callback colada no terminal. Detectado automaticamente "
            "quando nao ha sessao grafica."
        ),
    ),
):
    """Autentica com um provider cloud.

    Sem --provider: exibe menu interativo com todos os 14 providers.

    Exemplos:
      bauer auth login                   # menu interativo
      bauer auth login --provider copilot
      bauer auth login -p groq
      bauer auth login -p openai --no-browser   # servidor sem browser
    """
    from ..auth import cmd_login

    # None = autodetectar; True = forcar fluxo por colagem.
    cmd_login(provider if provider else None, no_browser=True if no_browser else None)


@auth_app.command("status")
def auth_status():
    """Mostra providers autenticados e status dos tokens."""
    from ..auth import cmd_status

    cmd_status()


@auth_app.command("logout")
def auth_logout(
    provider: str = typer.Option("", "--provider", "-p", help="Provider especifico (vazio = todos)"),
):
    """Remove autenticacao de um provider (ou todos)."""
    from ..auth import cmd_logout

    cmd_logout(provider if provider else None)


@auth_app.command("providers")
def auth_providers():
    """Lista providers disponíveis para autenticacao."""
    from ..auth import cmd_list_providers

    cmd_list_providers()
