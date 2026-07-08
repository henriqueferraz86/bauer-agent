"""Comando bauer credential."""

from __future__ import annotations

import typer

from ._common import console

credential_app = typer.Typer(help="Gerenciador seguro de credenciais (keychain → Fernet → config).")


@credential_app.command("set")
def credential_set(
    provider: str = typer.Argument(..., help="Nome do provider (ex: groq, openai)"),
) -> None:
    """Salva uma credencial no keychain ou arquivo encriptado."""
    import getpass
    secret = getpass.getpass(f"API key para '{provider}': ")
    if not secret:
        console.print("[red]Nenhum valor fornecido.[/red]")
        raise typer.Exit(1)
    from ..credential_pool import _cpool
    _cpool().set(provider, secret)
    console.print(f"[green]✓ Credencial de '{provider}' salva.[/green]")


@credential_app.command("get")
def credential_get(
    provider: str = typer.Argument(..., help="Nome do provider"),
) -> None:
    """Mostra os últimos 4 caracteres da credencial armazenada."""
    from ..credential_pool import _cpool
    val = _cpool().get(provider)
    if not val:
        console.print(f"[yellow]Nenhuma credencial armazenada para '{provider}'.[/yellow]")
    else:
        masked = "*" * (len(val) - 4) + val[-4:]
        console.print(f"[dim]{provider}:[/dim] {masked}")


@credential_app.command("list")
def credential_list() -> None:
    """Lista providers com credencial armazenada."""
    from ..credential_pool import _cpool
    providers = _cpool().list_providers()
    if not providers:
        console.print("[dim]Nenhuma credencial armazenada.[/dim]")
    else:
        for p in providers:
            console.print(f"  [cyan]•[/cyan] {p}")


@credential_app.command("delete")
def credential_delete(
    provider: str = typer.Argument(..., help="Nome do provider"),
) -> None:
    """Remove credencial do keychain e/ou arquivo encriptado."""
    from ..credential_pool import _cpool
    removed = _cpool().delete(provider)
    if removed:
        console.print(f"[green]✓ Credencial de '{provider}' removida.[/green]")
    else:
        console.print(f"[yellow]Nenhuma credencial encontrada para '{provider}'.[/yellow]")
