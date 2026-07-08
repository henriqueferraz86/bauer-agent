from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from bauer.cli import app


def test_voice_ask_sends_transcript_to_agent(tmp_path: Path):
    runner = CliRunner()

    with patch("bauer.audio_capture.capture_voice_input", return_value="resuma o projeto") as capture:
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

    with patch("bauer.audio_capture.capture_voice_input", return_value="texto capturado"):
        with patch("bauer.commands.agent_cmd.agent_run_one") as run_one:
            result = runner.invoke(app, ["voice", "listen"])

    assert result.exit_code == 0, result.output
    assert "texto capturado" in result.output
    run_one.assert_not_called()
