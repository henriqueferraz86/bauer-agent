from bauer.gateway_process import is_gateway_runtime_cmdline, is_gateway_runtime_process
from bauer.commands.gateway_cmd import _configure_gateway_logging


def test_detects_gateway_runtime_command_even_with_python_path():
    assert is_gateway_runtime_cmdline([
        "/usr/local/bin/python3",
        "-m", "bauer.cli", "gateway", "start", "--config", "/app/config.yaml",
    ])


def test_rejects_reused_pid_belonging_to_another_command():
    assert not is_gateway_runtime_cmdline([
        "/usr/local/bin/ollama", "pull", "qwen3:0.6b",
    ])
    assert not is_gateway_runtime_cmdline([
        "python", "-m", "bauer.cli", "serve",
    ])


def test_missing_pid_is_not_reported_as_running():
    import psutil

    assert not is_gateway_runtime_process(2_147_483_647)


def test_gateway_logs_do_not_emit_httpx_request_urls(monkeypatch):
    import logging

    logger = logging.getLogger("httpx")
    old_level = logger.level
    monkeypatch.setattr(logger, "level", old_level)
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: None)
    _configure_gateway_logging()
    assert logger.level == logging.WARNING
