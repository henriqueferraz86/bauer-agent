"""Voice commands: microphone capture, transcription, TTS and agent prompt."""

from __future__ import annotations

import os
from pathlib import Path

import typer

from ._common import console

voice_app = typer.Typer(
    help="Captura de voz, transcricao, sintese de voz e envio para o Bauer Agent.",
)

# Palavras que encerram o loop de conversa por voz (bauer voice chat). Sem
# barra — quem esta falando nao vai dizer "/sair", vai dizer "sair"/"tchau".
_VOICE_EXIT_WORDS = {"sair", "tchau", "encerrar", "parar", "exit", "quit", "bye"}


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
    speak: bool = typer.Option(
        False, "--speak", "-s",
        help="Sintetiza e toca a resposta em voz (TTS_PROVIDER=auto|local|openai)",
    ),
) -> None:
    """Fala com o Bauer: grava, transcreve, envia ao agent e imprime resposta.

    Com --speak, a resposta tambem e sintetizada em audio e tocada no
    alto-falante — round-trip de voz completo num unico comando.
    """
    text = _listen_once(max_duration=max_duration, silence_threshold=silence_threshold)
    if not text:
        console.print("[yellow]Nenhum audio capturado.[/yellow]")
        return

    console.print("\n[bold cyan]Voce disse:[/bold cyan]")
    console.print(f"  {text}\n")
    console.print("[bold cyan]Bauer:[/bold cyan]")

    from .agent_cmd import agent_run_one

    response = agent_run_one(
        task=text,
        config=config,
        models=models,
        agent=agent,
        agents_file=agents_file,
    )

    if speak and response:
        _speak_text(response)


@voice_app.command(name="speak")
def cmd_voice_speak(
    text: str = typer.Argument(..., help="Texto a sintetizar em voz"),
    output_file: str = typer.Option(
        "", "--output-file", "-o",
        help="Salva o audio aqui em vez de tocar (nao toca se informado)",
    ),
) -> None:
    """Sintetiza um texto em voz e toca no alto-falante (ou salva com -o)."""
    from bauer.tts import synthesize_speech

    console.print("[cyan]Sintetizando...[/cyan]")
    result = synthesize_speech(text, output_path=output_file or None)

    if not result.get("success"):
        console.print(f"[red]Sintese falhou: {result.get('error')}[/red]")
        raise typer.Exit(1)

    provider = result.get("provider", "?")
    path = result["path"]
    if output_file:
        console.print(f"[green]Audio salvo via {provider}:[/green] {path}")
        return

    console.print(f"[green]Sintetizado via {provider}.[/green] Tocando...")
    from bauer.audio_playback import play_audio_file

    if not play_audio_file(path):
        console.print(
            f"[yellow]Nao foi possivel tocar automaticamente. Audio em: {path}[/yellow]"
        )
    _cleanup_temp_audio(path)


@voice_app.command(name="chat")
def cmd_voice_chat(
    max_duration: int = typer.Option(
        120, "--duration", "-d", help="Tempo maximo de gravacao por turno (segundos)"
    ),
    silence_threshold: float = typer.Option(
        -40.0, "--threshold", "-t", help="Nivel de silencio em dB"
    ),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    agent: str = typer.Option("", "--agent", help="Agent especialista opcional"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
) -> None:
    """Conversa continua por voz: ouve, responde falado, ouve de novo.

    Loop completo mic -> Whisper -> Bauer -> TTS -> alto-falante, sem digitar
    nada. Diga "sair", "tchau" ou "parar" para encerrar, ou Ctrl+C a qualquer
    momento. Com STT_PROVIDER=local + TTS_PROVIDER=local (`uv sync --extra
    voice --extra voice-tts`), roda 100% offline, sem nenhuma API key.
    """
    from bauer.tts import preload_local_tts_model
    from bauer.transcription import preload_local_model

    # Aquece os dois modelos locais em paralelo (se forem os providers ativos)
    # antes do 1o turno — do contrario o usuario fala, e o primeiro "Bauer:"
    # demora dezenas de segundos so pra carregar peso de modelo.
    preload_local_model()
    preload_local_tts_model()

    console.print(
        "[bold cyan]Modo de conversa por voz.[/bold cyan] "
        "Diga 'sair' para encerrar ou Ctrl+C a qualquer momento.\n"
    )

    from .agent_cmd import agent_run_one

    try:
        while True:
            text = _listen_once(
                max_duration=max_duration, silence_threshold=silence_threshold
            )
            if not text:
                console.print("[yellow]Nenhum audio capturado, tentando de novo.[/yellow]\n")
                continue

            console.print(f"\n[bold cyan]Voce disse:[/bold cyan] {text}")

            normalized = text.strip().lower().rstrip(".!?")
            if normalized in _VOICE_EXIT_WORDS:
                console.print("[dim]Ate logo.[/dim]")
                return

            console.print("[bold cyan]Bauer:[/bold cyan]")
            response = agent_run_one(
                task=text,
                config=config,
                models=models,
                agent=agent,
                agents_file=agents_file,
            )
            if response:
                _speak_text(response)
            console.print()
    except KeyboardInterrupt:
        console.print("\n[dim]Ate logo.[/dim]")


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


@voice_app.command(name="status")
def cmd_voice_status() -> None:
    """Mostra a prontidão local do pipeline de voz sem capturar áudio."""
    from bauer.voice_status import collect_voice_status

    console.print("[bold cyan]Status do Bauer Voice[/bold cyan]")
    for component in collect_voice_status():
        marker = "[green]OK[/green]" if component["ok"] else "[yellow]--[/yellow]"
        console.print(f"{marker} {component['name']}: {component['detail']}")


@voice_app.command(name="metrics")
def cmd_voice_metrics(
    limit: int = typer.Option(20, "--limit", min=1, max=200, help="Número de turnos recentes"),
    runtime_root: Path = typer.Option(Path("memory/runtime"), "--root", help="Raiz do runtime"),
) -> None:
    """Exibe médias de latência dos turnos de voz persistidos."""
    from bauer.voice_metrics_report import collect_voice_metrics

    report = collect_voice_metrics(limit=limit, runtime_root=runtime_root)
    console.print("[bold cyan]Métricas do Bauer Voice[/bold cyan]")
    console.print(
        f"Turnos: {report['turns']} | completos: {report['completed']} | erros: {report['errors']}"
    )
    averages = report["averages_ms"]
    for name, value in averages.items():
        console.print(f"  {name}: {value if value is not None else 'n/d'} ms")
    if report["recent"]:
        console.print("[dim]Turnos recentes:[/dim]")
        for item in report["recent"]:
            console.print(
                f"  {item['turn_id']} | {item['status']} | {item['total_ms'] or 'n/d'} ms"
            )


def _listen_once(*, max_duration: int, silence_threshold: float) -> str | None:
    try:
        if os.environ.get("BAUER_STT_STREAMING", "").strip().lower() in {
            "1", "true", "yes"
        }:
            from bauer.voice_stt_stream import capture_voice_input_streaming

            return capture_voice_input_streaming(
                duration_max_s=max_duration,
                silence_threshold_db=silence_threshold,
                console=console,
            )

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


def _speak_text(text: str) -> None:
    """Sintetiza e toca `text`. Best-effort: nunca interrompe o chamador.

    Falha de TTS/playback vira um aviso amarelo, nao uma excecao — a resposta
    de texto ja foi impressa antes desta chamada, entao o turno nao se perde.
    """
    from bauer.tts import synthesize_speech

    result = synthesize_speech(text)
    if not result.get("success"):
        console.print(f"[dim yellow](voz indisponivel: {result.get('error')})[/dim yellow]")
        return

    from bauer.audio_playback import play_audio_file

    path = result["path"]
    if not play_audio_file(path):
        console.print(f"[dim yellow](nao foi possivel tocar o audio: {path})[/dim yellow]")
    _cleanup_temp_audio(path)


def _cleanup_temp_audio(path: str) -> None:
    """Remove o wav temporario gerado por synthesize_speech(output_path=None).

    So os arquivos que a propria sintese criou em tempfile — nunca um
    --output-file passado explicitamente pelo usuario (esse e para o
    usuario manter).
    """
    from contextlib import suppress

    p = Path(path)
    if "bauer-tts-" in p.name:
        with suppress(OSError):
            p.unlink()
