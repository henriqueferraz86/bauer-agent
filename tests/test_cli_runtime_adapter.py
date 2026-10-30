"""Sprint 37 (plano 062): CLI passa a condicionar o `executor` de
`kernel.execute()` ao `runtime.cli_default_adapter` resolvido do config.

Hermético: nenhum teste aqui toca Agno real nem um provider real.
- `_resolve_cli_runtime_adapter()` é testada isolada, com `load_config`
  trocado por um double — cobre "agno", "bauer_native", seção `runtime`
  ausente e falha de leitura (ConfigError ou qualquer outra exceção).
- A decisão de roteamento dentro de `run_agent_session` é testada com um
  kernel FAKE que só grava o valor de `executor` recebido por `.execute()`
  — nunca exercita `AgnoRuntimeAdapter` de verdade (já testado nos sprints
  35-36). Quando o branch resolvido é `bauer_native`, o fake CHAMA a
  closure recebida (o mesmo `_turn_executor` de produção), o que acaba
  rodando o motor nativo real contra um client mockado — prova que esse
  branch continua byte a byte igual ao de antes desta sprint.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import bauer.config_loader as config_loader
from bauer.tool_router import ToolRouter


# ─── _resolve_cli_runtime_adapter: leitura best-effort do config ───────────


def test_resolve_reads_agno_from_config(monkeypatch):
    from bauer.agent import _resolve_cli_runtime_adapter

    monkeypatch.setattr(
        config_loader, "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(cli_default_adapter="agno")),
    )
    assert _resolve_cli_runtime_adapter() == "agno"


def test_resolve_reads_bauer_native_from_config(monkeypatch):
    from bauer.agent import _resolve_cli_runtime_adapter

    monkeypatch.setattr(
        config_loader, "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(cli_default_adapter="bauer_native")),
    )
    assert _resolve_cli_runtime_adapter() == "bauer_native"


def test_resolve_normalizes_case_and_whitespace(monkeypatch):
    from bauer.agent import _resolve_cli_runtime_adapter

    monkeypatch.setattr(
        config_loader, "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(cli_default_adapter=" AGNO \n")),
    )
    assert _resolve_cli_runtime_adapter() == "agno"


def test_resolve_returns_empty_when_runtime_section_missing(monkeypatch):
    """Config double sem seção `runtime` (ex.: mocks legados de outros testes)."""
    from bauer.agent import _resolve_cli_runtime_adapter

    monkeypatch.setattr(config_loader, "load_config", lambda: SimpleNamespace())
    assert _resolve_cli_runtime_adapter() == ""


def test_resolve_returns_empty_on_config_error(monkeypatch, tmp_path: Path):
    """cwd sem config.yaml (e BAUER_HOME vazio, via conftest) -> ConfigError -> ""."""
    from bauer.agent import _resolve_cli_runtime_adapter

    monkeypatch.chdir(tmp_path)
    assert _resolve_cli_runtime_adapter() == ""


def test_resolve_returns_empty_on_unexpected_exception(monkeypatch):
    """Qualquer falha de leitura é best-effort — nunca propaga, nunca quebra o turno."""
    from bauer.agent import _resolve_cli_runtime_adapter

    def _boom():
        raise RuntimeError("disco falhou")

    monkeypatch.setattr(config_loader, "load_config", _boom)
    assert _resolve_cli_runtime_adapter() == ""


# ─── run_agent_session: branch de executor dado o adapter resolvido ────────


def _capture_client(seen: dict) -> MagicMock:
    client = MagicMock()

    def _cap(model, messages, *a, **k):
        seen.setdefault("models", []).append(model)
        return iter(["ok"])

    client.chat_stream.side_effect = _cap
    client._provider = "openrouter"
    return client


class _FakeKernel:
    """Grava o `executor` recebido por `.execute()` — nunca despacha pro
    AgnoRuntimeAdapter real. Ver docstring do módulo."""

    def __init__(self):
        self.executors: list = []

    def execute(self, request, *, executor=None):
        self.executors.append(executor)
        if executor is None:
            return SimpleNamespace(
                ok=True, status="completed", output="oi do agno (fake)",
                error=None, policy_reason=None, approval_id=None,
            )
        result = executor({}) or {}
        status = result.get("status", "completed")
        return SimpleNamespace(
            ok=(status == "completed"), status=status,
            output=result.get("output", ""), error=result.get("error"),
            policy_reason=None, approval_id=None,
        )


def _run_session_with_fake_kernel(tmp_path: Path, resolved_adapter: str):
    from bauer.agent import run_agent_session
    from rich.console import Console

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    seen: dict = {}
    client = _capture_client(seen)
    kernel = _FakeKernel()
    console = Console(record=True, width=120)

    with patch("builtins.input", side_effect=["diga oi", EOFError]), \
         patch("bauer.agent._try_parse_tool", return_value=None), \
         patch("bauer.agent._resolve_cli_runtime_adapter", return_value=resolved_adapter):
        run_agent_session(
            client, "primary-model", 4096, console,
            ToolRouter(workspace=ws), kernel=kernel,
        )
    return kernel, seen, console.export_text()


def test_agno_adapter_calls_execute_with_executor_none(tmp_path: Path):
    kernel, seen, out = _run_session_with_fake_kernel(tmp_path, "agno")
    assert kernel.executors == [None]
    assert "models" not in seen  # motor nativo nunca chamado
    assert "oi do agno (fake)" in out  # resposta do fake veio do kernel.execute


def test_bauer_native_adapter_calls_execute_with_closure(tmp_path: Path):
    kernel, seen, _ = _run_session_with_fake_kernel(tmp_path, "bauer_native")
    assert len(kernel.executors) == 1
    assert kernel.executors[0] is not None
    assert callable(kernel.executors[0])
    assert seen["models"] == ["primary-model"]  # motor nativo real rodou


def test_missing_adapter_config_falls_back_to_bauer_native(tmp_path: Path):
    """ausente/erro de leitura ("") -> continua usando o executor nativo."""
    kernel, seen, _ = _run_session_with_fake_kernel(tmp_path, "")
    assert len(kernel.executors) == 1
    assert kernel.executors[0] is not None
    assert seen["models"] == ["primary-model"]


# ─── Correção 2 (bloqueante): Ctrl-C no branch Agno cancela só o turno ─────


class _FakeKernelKeyboardInterrupt:
    """`.execute()` levanta KeyboardInterrupt quando chamado com
    `executor=None` (caminho Agno) — simula o operador apertando Ctrl-C no
    meio do turno despachado pro AgnoRuntimeAdapter, sem precisar de Agno
    real nem TTY. Ver SPEC.md / achado da revisão independente."""

    def __init__(self):
        self.executors: list = []
        self.calls = 0

    def execute(self, request, *, executor=None):
        self.executors.append(executor)
        self.calls += 1
        if executor is None:
            raise KeyboardInterrupt()
        result = executor({}) or {}
        status = result.get("status", "completed")
        return SimpleNamespace(
            ok=(status == "completed"), status=status,
            output=result.get("output", ""), error=result.get("error"),
            policy_reason=None, approval_id=None,
        )


def test_agno_keyboard_interrupt_cancels_only_the_turn(tmp_path: Path):
    """Ctrl-C durante `kernel.execute(..., executor=None)` não deve escapar
    de `run_agent_session` — a função precisa voltar ao loop/retornar
    normalmente (via EOFError no próximo input), não matar o processo.
    Prova a correção do achado bloqueante nº2 da revisão do sprint 37.

    A asserção sobre a mensagem espiona `console.print` (em vez de inspecionar
    o texto renderizado via `export_text()`): o literal exato reaproveitado
    de `agent.py:2327` — `"\\n[dim][interrompido][/dim]"` — tem um colchete
    interno (`[interrompido]`) que o parser de markup do Rich consome como
    uma tag de estilo desconhecida, então o texto renderizado não contém a
    palavra (mesma característica já presente no literal do branch
    `bauer_native`, preexistente a esta correção — não é uma regressão desta
    sprint). Espionar a chamada prova que o MESMO literal foi reaproveitado,
    sem depender dessa particularidade de renderização do Rich.
    """
    from bauer.agent import run_agent_session
    from rich.console import Console

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    client = _capture_client({})
    kernel = _FakeKernelKeyboardInterrupt()
    console = Console(record=True, width=120)
    print_calls: list = []
    _orig_print = console.print

    def _spy_print(*args, **kwargs):
        print_calls.append(args)
        return _orig_print(*args, **kwargs)

    console.print = _spy_print

    with patch("builtins.input", side_effect=["diga oi", EOFError]), \
         patch("bauer.agent._try_parse_tool", return_value=None), \
         patch("bauer.agent._resolve_cli_runtime_adapter", return_value="agno"):
        # Não deve propagar KeyboardInterrupt (nem nenhuma outra exceção) —
        # se propagasse, este `run_agent_session(...)` levantaria e o teste
        # falharia aqui mesmo, antes de qualquer assert.
        run_agent_session(
            client, "primary-model", 4096, console,
            ToolRouter(workspace=ws), kernel=kernel,
        )

    assert kernel.calls == 1
    assert kernel.executors == [None]
    assert any(
        args and args[0] == "\n[dim][interrompido][/dim]" for args in print_calls
    ), f"mensagem de cancelamento não impressa; chamadas: {print_calls}"
