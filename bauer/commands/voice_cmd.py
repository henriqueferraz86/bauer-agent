"""Voice commands: microphone capture, transcription and agent prompt."""

from __future__ import annotations

from pathlib import Path

import typer

from ._common import console

voice_app = typer.Typer(
    help="Captura de voz, transcricao e envio para o Bauer Agent.",
)


@voice_app.command(name="listen")
def cmd_voice_listen(
    max_duration: int = typer.Option(
        120, "--duration", "-d", help="Tempo maximo de gravacao (segundos)"
    ),
    silence_threshold: float = typer.Option(
        -40.0, "--threshold", "-t", help="Nivel de silencio em dB"
    ),
) -> None:
    """Grava audio do microfone, transcreve e imprime o resultado."""
    text = _listen_once(max_duration=max_duration, silence_threshold=silence_threshold)
    if text:
        console.print("\n[bold cyan]Resultado:[/bold cyan]")
        console.print(f"  {text}\n")
    else:
        console.print("[yellow]Nenhum audio capturado.[/yellow]")


@voice_app.command(name="ask")
def cmd_voice_ask(
    max_duration: int = typer.Option(
        120, "--duration", "-d", help="Tempo maximo de gravacao (segundos)"
    ),
    silence_threshold: float = typer.Option(
        -40.0, "--threshold", "-t", help="Nivel de silencio em dB"
    ),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    agent: str = typer.Option("", "--agent", help="Agent especialista opcional"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
) -> None:
    """Fala com o Bauer: grava, transcreve, envia ao agent e imprime resposta."""
    text = _listen_once(max_duration=max_duration, silence_threshold=silence_threshold)
    if not text:
        console.print("[yellow]Nenhum audio capturado.[/yellow]")
        return

    console.print("\n[bold cyan]Voce disse:[/bold cyan]")
    console.print(f"  {text}\n")
    console.print("[bold cyan]Bauer:[/bold cyan]")

    from .agent_cmd import agent_run_one

    agent_run_one(
        task=text,
        config=config,
        models=models,
        agent=agent,
        agents_file=agents_file,
    )


@voice_app.command(name="transcribe")
def cmd_voice_transcribe(
    file_path: str = typer.Argument(
        ..., help="Caminho do arquivo de audio (.wav, .mp3, .ogg, etc.)"
    ),
) -> None:
    """Transcreve um arquivo de audio existente."""
    from bauer.transcription import transcribe_audio

    path = Path(file_path)
    if not path.exists():
        console.print(f"[red]Arquivo nao encontrado: {path}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Transcrevendo {path.name}...[/cyan]")
    result = transcribe_audio(path)

    if result.get("success"):
        text = result.get("transcript", "")
        provider = result.get("provider", "?")
        console.print(f"\n[green]Transcrito via {provider}:[/green]")
        console.print(f"  {text}\n")
    else:
        error = result.get("error", "Erro desconhecido")
        console.print(f"[red]Transcricao falhou: {error}[/red]")
        raise typer.Exit(1)


def _listen_once(*, max_duration: int, silence_threshold: float) -> str | None:
    try:
        from bauer.audio_capture import capture_voice_input

        return capture_voice_input(
            duration_max_s=max_duration,
            silence_threshold_db=silence_threshold,
            console=console,
        )
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[red]Erro: {exc}[/red]")
        raise typer.Exit(1) from exc
