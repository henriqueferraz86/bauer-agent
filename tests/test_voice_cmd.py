from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from bauer.cli import app


def test_voice_ask_sends_transcript_to_agent(tmp_path: Path):
    runner = CliRunner()

    with patch("bauer.voice_stt_stream.capture_voice_input_streaming", return_value="resuma o projeto") as capture:
        with patch("bauer.commands.agent_cmd.agent_run_one") as run_one:
            result = runner.invoke(
                app,
                [
                    "voice",
                    "ask",
                    "--config",
                    str(tmp_path / "config.yaml"),
                    "--models",
                    str(tmp_path / "models.yaml"),
                    "--agent",
                    "worker-remoto",
                    "--agents",
                    str(tmp_path / "agents.yaml"),
                ],
            )

    assert result.exit_code == 0, result.output
    capture.assert_called_once()
    run_one.assert_called_once_with(
        task="resuma o projeto",
        config=tmp_path / "config.yaml",
        models=tmp_path / "models.yaml",
        agent="worker-remoto",
        agents_file=tmp_path / "agents.yaml",
    )
    assert "Voce disse" in result.output
    assert "Bauer" in result.output


def test_voice_listen_keeps_transcription_only():
    runner = CliRunner()

    with patch("bauer.voice_stt_stream.capture_voice_input_streaming", return_value="texto capturado"):
        with patch("bauer.commands.agent_cmd.agent_run_one") as run_one:
            result = runner.invoke(app, ["voice", "listen"])

    assert result.exit_code == 0, result.output
    assert "texto capturado" in result.output
    run_one.assert_not_called()


def test_voice_ask_without_speak_never_touches_tts(tmp_path: Path):
    """Default (sem --speak): comportamento antigo, sem custo de sintese."""
    runner = CliRunner()

    with patch("bauer.voice_stt_stream.capture_voice_input_streaming", return_value="oi"):
        with patch("bauer.commands.agent_cmd.agent_run_one", return_value="resposta") as run_one:
            with patch("bauer.tts.synthesize_speech") as synth:
                result = runner.invoke(
                    app,
                    [
                        "voice", "ask",
                        "--config", str(tmp_path / "config.yaml"),
                        "--models", str(tmp_path / "models.yaml"),
                        "--agents", str(tmp_path / "agents.yaml"),
                    ],
                )

    assert result.exit_code == 0, result.output
    run_one.assert_called_once()
    synth.assert_not_called()


def test_voice_ask_with_speak_synthesizes_response(tmp_path: Path):
    runner = CliRunner()

    with patch("bauer.voice_stt_stream.capture_voice_input_streaming", return_value="oi"):
        with patch("bauer.commands.agent_cmd.agent_run_one", return_value="ola, tudo bem?"):
            with patch(
                "bauer.tts.synthesize_speech",
                return_value={"success": True, "path": "/tmp/bauer-tts-x.wav", "provider": "local"},
            ) as synth:
                with patch("bauer.audio_playback.play_audio_file", return_value=True) as play:
                    result = runner.invoke(
                        app,
                        [
                            "voice", "ask", "--speak",
                            "--config", str(tmp_path / "config.yaml"),
                            "--models", str(tmp_path / "models.yaml"),
                            "--agents", str(tmp_path / "agents.yaml"),
                        ],
                    )

    assert result.exit_code == 0, result.output
    synth.assert_called_once_with("ola, tudo bem?")
    play.assert_called_once_with("/tmp/bauer-tts-x.wav")


def test_voice_ask_speak_failure_does_not_break_command(tmp_path: Path):
    """TTS indisponivel com --speak: aviso, mas exit_code continua 0."""
    runner = CliRunner()

    with patch("bauer.voice_stt_stream.capture_voice_input_streaming", return_value="oi"):
        with patch("bauer.commands.agent_cmd.agent_run_one", return_value="resposta"):
            with patch(
                "bauer.tts.synthesize_speech",
                return_value={"success": False, "path": "", "error": "sem provider"},
            ):
                result = runner.invoke(
                    app,
                    [
                        "voice", "ask", "--speak",
                        "--config", str(tmp_path / "config.yaml"),
                        "--models", str(tmp_path / "models.yaml"),
                        "--agents", str(tmp_path / "agents.yaml"),
                    ],
                )

    assert result.exit_code == 0, result.output
    assert "voz indisponivel" in result.output or "sem provider" in result.output


def test_voice_speak_plays_and_reports_provider():
    runner = CliRunner()

    with patch(
        "bauer.tts.synthesize_speech",
        return_value={"success": True, "path": "/tmp/bauer-tts-y.wav", "provider": "openai"},
    ):
        with patch("bauer.audio_playback.play_audio_file", return_value=True) as play:
            result = runner.invoke(app, ["voice", "speak", "ola mundo"])

    assert result.exit_code == 0, result.output
    assert "openai" in result.output
    play.assert_called_once_with("/tmp/bauer-tts-y.wav")


def test_voice_speak_with_output_file_does_not_play(tmp_path: Path):
    dest = tmp_path / "saida.wav"
    runner = CliRunner()

    with patch(
        "bauer.tts.synthesize_speech",
        return_value={"success": True, "path": str(dest), "provider": "local"},
    ):
        with patch("bauer.audio_playback.play_audio_file") as play:
            result = runner.invoke(
                app, ["voice", "speak", "ola", "--output-file", str(dest)]
            )

    assert result.exit_code == 0, result.output
    # Rich pode quebrar linha num caminho longo — checa o nome do arquivo,
    # não o caminho inteiro como uma única substring.
    assert dest.name in result.output.replace("\n", "")
    play.assert_not_called()


def test_voice_speak_failure_exits_nonzero():
    runner = CliRunner()

    with patch(
        "bauer.tts.synthesize_speech",
        return_value={"success": False, "path": "", "error": "sem provider TTS"},
    ):
        result = runner.invoke(app, ["voice", "speak", "ola"])

    assert result.exit_code != 0
    assert "sem provider TTS" in result.output


def test_voice_chat_exits_on_spoken_exit_word(tmp_path: Path):
    """'sair' falado encerra o loop sem passar pelo agent nem pelo TTS."""
    runner = CliRunner()

    with patch("bauer.transcription.preload_local_model", return_value=False):
        with patch("bauer.tts.preload_local_tts_model", return_value=False):
            with patch("bauer.voice_stt_stream.capture_voice_input_streaming", return_value="sair"):
                with patch("bauer.commands.agent_cmd.agent_run_one") as run_one:
                    result = runner.invoke(
                        app,
                        [
                            "voice", "chat",
                            "--config", str(tmp_path / "config.yaml"),
                            "--models", str(tmp_path / "models.yaml"),
                            "--agents", str(tmp_path / "agents.yaml"),
                        ],
                    )

    assert result.exit_code == 0, result.output
    run_one.assert_not_called()


def test_voice_chat_speaks_each_turn_until_exit(tmp_path: Path):
    """Dois turnos falados, depois 'tchau' — agent e TTS chamados 2x cada."""
    runner = CliRunner()
    turns = iter(["primeira pergunta", "segunda pergunta", "tchau"])

    def fake_capture(**kwargs):
        return next(turns)

    with patch("bauer.transcription.preload_local_model", return_value=False):
        with patch("bauer.tts.preload_local_tts_model", return_value=False):
            with patch("bauer.voice_stt_stream.capture_voice_input_streaming", side_effect=fake_capture):
                with patch(
                    "bauer.commands.agent_cmd.agent_run_one", return_value="resposta falada"
                ) as run_one:
                    with patch(
                        "bauer.tts.synthesize_speech",
                        return_value={"success": True, "path": "/tmp/bauer-tts-z.wav", "provider": "local"},
                    ) as synth:
                        with patch("bauer.audio_playback.play_audio_file", return_value=True):
                            result = runner.invoke(
                                app,
                                [
                                    "voice", "chat",
                                    "--config", str(tmp_path / "config.yaml"),
                                    "--models", str(tmp_path / "models.yaml"),
                                    "--agents", str(tmp_path / "agents.yaml"),
                                ],
                            )

    assert result.exit_code == 0, result.output
    assert run_one.call_count == 2
    assert synth.call_count == 2


def test_voice_status_does_not_capture_audio():
    runner = CliRunner()

    with patch(
        "bauer.voice_status.collect_voice_status",
        return_value=[{"name": "microfone / VAD", "ok": True, "detail": "pronto"}],
    ) as status:
        with patch("bauer.audio_capture.capture_voice_input") as capture:
            result = runner.invoke(app, ["voice", "status"])

    assert result.exit_code == 0, result.output
    assert "Status do Bauer Voice" in result.output
    status.assert_called_once_with()
    capture.assert_not_called()


def test_voice_metrics_prints_persisted_summary(tmp_path: Path):
    runner = CliRunner()

    with patch(
        "bauer.voice_metrics_report.collect_voice_metrics",
        return_value={
            "turns": 1,
            "completed": 1,
            "errors": 0,
            "averages_ms": {"total": 123.0, "llm_to_first_delta": None, "tts_synthesis": 20.0, "tts_playback": 80.0},
            "recent": [{"turn_id": "turn-1", "status": "completed", "total_ms": 123.0}],
        },
    ) as metrics:
        result = runner.invoke(app, ["voice", "metrics", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Métricas do Bauer Voice" in result.output
    assert "turn-1" in result.output
    metrics.assert_called_once_with(limit=20, runtime_root=tmp_path)
