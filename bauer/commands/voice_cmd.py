"""Comando bauer voice — captura de voz e transcrição via Whisper local."""

from __future__ import annotations

import typer
from rich.console import Console

from ._common import console

voice_app = typer.Typer(
    help="Captura de voz e transcrição (requer sounddevice + faster-whisper local).",
)


@voice_app.command(name="listen")
def cmd_voice_listen(
    max_duration: int = typer.Option(
        30, "--duration", "-d", help="Tempo máximo de gravação (segundos)"
    ),
    silence_threshold: float = typer.Option(
        -40.0, "--threshold", "-t", help="Nível de silêncio em dB"
    ),
) -> None:
    """Grava áudio do microfone, transcreve com Whisper local, imprime o resultado."""
    try:
        from bauer.audio_capture import capture_voice_input

        text = capture_voice_input(
            duration_max_s=max_duration,
            silence_threshold_db=silence_threshold,
            console=console,
        )

        if text:
            console.print("\n[bold cyan]📋 Resultado:[/bold cyan]")
            console.print(f"  {text}\n")
        else:
            console.print("[yellow]Nenhum áudio capturado.[/yellow]")

    except ImportError as e:
        console.print(f"[red]✗ {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]✗ Erro: {e}[/red]")
        raise typer.Exit(1)


@voice_app.command(name="transcribe")
def cmd_voice_transcribe(
    file_path: str = typer.Argument(
        ..., help="Caminho do arquivo de áudio (.wav, .mp3, .ogg, etc.)"
    ),
) -> None:
    """Transcreve um arquivo de áudio existente."""
    from pathlib import Path

    from bauer.transcription import transcribe_audio

    path = Path(file_path)
    if not path.exists():
        console.print(f"[red]✗ Arquivo não encontrado: {path}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]📝 Transcrevendo {path.name}...[/cyan]")

    result = transcribe_audio(path)

    if result.get("success"):
        text = result.get("transcript", "")
        provider = result.get("provider", "?")
        console.print(f"\n[green]✓ Transcrito via {provider}:[/green]")
        console.print(f"  {text}\n")
    else:
        error = result.get("error", "Erro desconhecido")
        console.print(f"[red]✗ Transcrição falhou: {error}[/red]")
        raise typer.Exit(1)
