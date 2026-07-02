"""Testes das correções de UX/performance do terminal (bauer agent).

Cobre:
  - AuthManager: httpx.Client lazy (criava SSL context ~260ms × 62 fallbacks
    no startup — 17s medidos antes do fix)
  - OllamaClient.is_alive: probe com timeout curto (não o timeout de chat)
  - _print_assistant_response: render Markdown + fallback texto puro
  - _thinking_status: nunca quebra o turno (best-effort)
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from rich.console import Console


# ─── AuthManager lazy http ────────────────────────────────────────────────────


def test_auth_manager_init_does_not_create_http_client(tmp_path):
    from bauer.auth import AuthManager

    auth = AuthManager(base_dir=tmp_path)
    assert auth._http_client is None  # nada de httpx.Client no __init__


def test_auth_manager_http_created_on_first_use_and_close_safe(tmp_path):
    from bauer.auth import AuthManager

    auth = AuthManager(base_dir=tmp_path)
    auth.close()  # fechar sem nunca ter usado não cria nem quebra
    assert auth._http_client is None

    client = auth._http  # primeiro acesso cria
    assert auth._http_client is client
    assert auth._http is client  # acessos seguintes reusam
    auth.close()


# ─── OllamaClient.is_alive: probe curto ──────────────────────────────────────


def test_is_alive_probe_uses_short_timeout():
    """Liveness não pode esperar o timeout de chat (30-300s): Ollama saudável
    responde /api/tags em ms; caído/via firewall segurava o startup."""
    from bauer.ollama_client import OllamaClient

    c = OllamaClient("http://localhost:11434", timeout_seconds=300)
    with patch("bauer.ollama_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        alive, reason = c.is_alive()

    assert alive is True
    assert mock_get.call_args.kwargs["timeout"] <= 2.0


def test_is_alive_probe_respects_smaller_configured_timeout():
    from bauer.ollama_client import OllamaClient

    c = OllamaClient("http://localhost:11434", timeout_seconds=1)
    with patch("bauer.ollama_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        c.is_alive()

    assert mock_get.call_args.kwargs["timeout"] == 1.0


# ─── _print_assistant_response ────────────────────────────────────────────────


def _capture_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def test_print_assistant_response_renders_markdown():
    from bauer.agent import _print_assistant_response

    console = _capture_console()
    _print_assistant_response(console, "resposta com **negrito** e `codigo`")
    out = console.file.getvalue()

    assert "bauer" in out
    assert "negrito" in out
    assert "**" not in out  # markdown foi renderizado, não impresso cru


def test_print_assistant_response_includes_cost_line():
    from bauer.agent import _print_assistant_response

    console = _capture_console()
    _print_assistant_response(console, "ok", cost_line="[dim]custo x[/dim]")
    assert "custo x" in console.file.getvalue()


def test_print_assistant_response_falls_back_to_plain_text():
    from bauer.agent import _print_assistant_response

    console = _capture_console()
    with patch("rich.markdown.Markdown", side_effect=RuntimeError("boom")):
        _print_assistant_response(console, "texto simples")
    assert "texto simples" in console.file.getvalue()


# ─── _thinking_status ─────────────────────────────────────────────────────────


def test_thinking_status_yields_even_if_console_status_fails():
    from bauer.agent import _thinking_status

    console = MagicMock()
    console.status.side_effect = RuntimeError("live display já ativo")
    ran = False
    with _thinking_status(console, "modelo-x"):
        ran = True
    assert ran


def test_thinking_status_enters_and_exits_console_status():
    from bauer.agent import _thinking_status

    console = _capture_console()
    with _thinking_status(console, "modelo-x"):
        pass  # não deve levantar nem deixar live display pendurado
    # segundo uso confirma que o primeiro liberou o live display
    with _thinking_status(console, "modelo-x"):
        pass
