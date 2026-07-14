"""`bauer run` — entrada autônoma de ponta a ponta (plano 022 / Fatia A).

Fachada fina sobre serve_loop.run_loop_rounds, governada pelo Kernel. Estes
testes provam o contrato: CWD=workspace, config canônico vence config do
projeto, limites/banner, exit codes 0/2/130, pasta sensível recusada, não lê
stdin. Nenhum toca rede/LLM real (run_one_turn_with_fallback é mockado).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from bauer.cli import app
from bauer.config_loader import BauerConfig, LoopSection, ModelSection

runner = CliRunner()


def _cfg(**loop_kw) -> BauerConfig:
    return BauerConfig(
        model=ModelSection(provider="openrouter", name="deepseek/deepseek-v4-flash"),
        loop=LoopSection(**loop_kw),
    )


def _patches(cfg: BauerConfig, turn_responses):
    """Contexto que mocka o pesado: load config, client/router builders e o
    motor de turno. Retorna (patchers, seen) — seen coleta o que foi montado."""
    seen: dict = {}
    responses = iter(turn_responses)

    def _turn(ctx, router, client, model, fallbacks):
        r = next(responses)
        seen.setdefault("turns", []).append(r)
        return r  # (text, tool_log)

    def _mk_router(cfg_, ws, llm_client=None, session_id=""):
        seen["workspace"] = Path(ws)
        r = MagicMock()
        r.available_tools.return_value = []
        r._approval_callback = None
        r.workspace = ws
        return r

    return [
        patch("bauer.commands._runtime._load_or_die", return_value=(cfg, MagicMock())),
        patch("bauer.commands._runtime._build_client", return_value=MagicMock()),
        patch("bauer.commands._runtime._build_router", side_effect=_mk_router),
        patch("bauer.commands.agent_cmd._build_fallback_clients", return_value=[]),
        patch("bauer.agent._build_system_prompt", return_value="sys"),
        patch("bauer.agent.run_one_turn_with_fallback", side_effect=_turn),
    ], seen


def _run(args, cfg, turn_responses, cwd: Path):
    patches, seen = _patches(cfg, turn_responses)
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        # CWD real = tmp project (é onde bauer run pega o workspace)
        with patch("bauer.commands.run_cmd.Path.cwd", return_value=cwd):
            result = runner.invoke(app, ["run", *args])
    return result, seen


# ─── contrato ────────────────────────────────────────────────────────────────


def test_run_appears_in_root_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output


def test_run_is_in_comecar_aqui_panel():
    """Fatia B: as portas principais ganham um painel próprio no --help, e o
    epílogo lidera com `bauer run` (é onde o olho cai primeiro)."""
    result = runner.invoke(app, ["--help"])
    assert "Começar aqui" in result.output
    # o epílogo (rodapé) aponta o bauer run como a porta principal
    assert 'bauer run' in result.output


def test_no_command_removed_still_discoverable():
    """Nenhum comando 'avançado' sumiu — só mudou de painel de exibição."""
    result = runner.invoke(app, ["--help"])
    for cmd in ("kernel", "orchestrate", "dispatch", "daemon", "runtime", "budget"):
        assert cmd in result.output, f"comando {cmd} sumiu do help"


def test_all_commands_grouped_into_named_panels():
    """Todo comando está num painel temático — nada no 'Commands' genérico."""
    result = runner.invoke(app, ["--help"])
    for panel in ("Começar aqui", "Projeto & specs", "Autonomia & orquestração",
                  "Observabilidade & custo", "Memória, skills & aprendizado",
                  "Conectividade", "Config & sistema"):
        assert panel in result.output, f"painel '{panel}' faltando"
    # o painel genérico 'Commands' não deve mais existir (tudo foi categorizado)
    assert "─ Commands ─" not in result.output


def test_empty_task_is_error_no_prompt():
    result = runner.invoke(app, ["run", ""])
    assert result.exit_code == 1
    assert "informe a tarefa" in result.output
    # nunca abre prompt/stdin: input vazio não trava


def test_cwd_becomes_workspace(tmp_path: Path):
    proj = tmp_path / "meu-projeto"
    proj.mkdir()
    result, seen = _run(["diga oi e confirme"], _cfg(),
                        [("terminei", []), ("confirmo", [])], proj)
    assert result.exit_code == 0
    assert seen["workspace"] == proj.resolve()


def test_config_canonical_wins_over_project_config(tmp_path: Path):
    """O bauer run passa paths.config_path() ao _load_or_die — nunca o
    config.yaml do projeto. Verifica o caminho recebido."""
    proj = tmp_path / "app-com-config"
    proj.mkdir()
    (proj / "config.yaml").write_text("isto: nao_e_config_do_bauer", encoding="utf-8")

    captured = {}
    orig_cfg = _cfg()

    def _fake_load(cfg_path, models_path):
        captured["config_path"] = Path(cfg_path)
        return orig_cfg, MagicMock()

    with patch("bauer.commands._runtime._load_or_die", side_effect=_fake_load), \
         patch("bauer.commands._runtime._build_client", return_value=MagicMock()), \
         patch("bauer.commands._runtime._build_router") as mk_router, \
         patch("bauer.commands.agent_cmd._build_fallback_clients", return_value=[]), \
         patch("bauer.agent._build_system_prompt", return_value="sys"), \
         patch("bauer.agent.run_one_turn_with_fallback", side_effect=[("fim", []), ("ok", [])]), \
         patch("bauer.commands.run_cmd.Path.cwd", return_value=proj):
        r = MagicMock(); r.available_tools.return_value = []; r._approval_callback = None
        mk_router.return_value = r
        result = runner.invoke(app, ["run", "faca algo"])

    assert result.exit_code == 0
    # o config carregado NÃO é o do projeto
    assert captured["config_path"] != proj / "config.yaml"
    assert ".bauer" in str(captured["config_path"]) or captured["config_path"].name == "config.yaml"


def test_sensitive_dir_refused_before_client(tmp_path: Path):
    build_called = {"n": 0}
    with patch("bauer.projects_registry.is_sensitive_dir", return_value=True), \
         patch("bauer.commands._runtime._build_client",
               side_effect=lambda *a, **k: build_called.__setitem__("n", 1)):
        result = runner.invoke(app, ["run", "faca X", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "sensível" in result.output
    assert build_called["n"] == 0  # recusou ANTES de montar cliente


def test_banner_says_estimated_cost(tmp_path: Path):
    proj = tmp_path / "p"; proj.mkdir()
    result, _ = _run(["faca X"], _cfg(max_minutes=15, max_tool_calls=40, max_cost_usd=1.5),
                     [("fim", []), ("ok", [])], proj)
    assert "ESTIMADO" in result.output
    assert "15 min" in result.output and "40" in result.output


def test_exit_code_completed_is_0(tmp_path: Path):
    proj = tmp_path / "p"; proj.mkdir()
    result, _ = _run(["faca X"], _cfg(), [("fim", []), ("confirmo", [])], proj)
    assert result.exit_code == 0
    assert "concluída" in result.output


def test_exit_code_incomplete_is_2(tmp_path: Path):
    """Budget de tools esgota antes de concluir → exit 2."""
    proj = tmp_path / "p"; proj.mkdir()
    # cada rodada usa tools; teto baixo → budget_exhausted
    turns = [("trabalhando", [{"tool": "t"}] * 3)] * 5
    result, _ = _run(["faca X", "--max-tool-calls", "4"], _cfg(), turns, proj)
    assert result.exit_code == 2


def test_overrides_replace_config_for_cli(tmp_path: Path):
    """CLI: --max-tool-calls MAIOR que o config VENCE (clamp=False)."""
    proj = tmp_path / "p"; proj.mkdir()
    # config permite 120; peço 2 → deve limitar a 2 (override substitui p/ MENOS tb)
    turns = [("trab", [{"tool": "t"}] * 3)] * 4
    result, _ = _run(["faca X", "--max-tool-calls", "2"], _cfg(max_tool_calls=120), turns, proj)
    assert result.exit_code == 2  # esgotou nas 2 primeiras


def test_invalid_approval_is_error(tmp_path: Path):
    proj = tmp_path / "p"; proj.mkdir()
    result, _ = _run(["faca X", "--approval", "banana"], _cfg(), [("fim", [])], proj)
    assert result.exit_code == 1
    assert "inválido" in result.output or "Limite inválido" in result.output


def test_max_cost_guardrail_stops_the_run(tmp_path: Path):
    """Regressão: o guardrail --max-cost estava MORTO (budget.consume_cost
    nunca era chamado). Agora, um turno que reporta custo via o cost_sink
    instalado alimenta o budget: com --max-cost baixo, o run para por
    orçamento (exit 2) e o custo aparece no resumo (não mais ~US$ 0.000)."""
    import contextlib

    from bauer.cli import app as _app

    proj = tmp_path / "p"; proj.mkdir()
    cfg = _cfg()

    def _turn_reports_cost(ctx, router, client, model, fallbacks):
        # Simula uma LLM call de US$0.05 reportada ao sink instalado pelo run_cmd.
        from bauer.cost_meter import cost_sink
        sink = cost_sink.get()
        if sink is not None:
            sink("openrouter", "m", {"total_tokens": 100}, 0.05)
        return ("trabalhando", [])

    patches = [
        patch("bauer.commands._runtime._load_or_die", return_value=(cfg, MagicMock())),
        patch("bauer.commands._runtime._build_client", return_value=MagicMock()),
        patch("bauer.commands._runtime._build_router", side_effect=lambda c, ws, **k: _mk_router_stub(ws)),
        patch("bauer.commands.agent_cmd._build_fallback_clients", return_value=[]),
        patch("bauer.agent._build_system_prompt", return_value="sys"),
        patch("bauer.agent.run_one_turn_with_fallback", side_effect=_turn_reports_cost),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with patch("bauer.commands.run_cmd.Path.cwd", return_value=proj):
            result = runner.invoke(_app, ["run", "faca X", "--max-cost", "0.01"])

    assert result.exit_code == 2  # parou por budget (custo estourou o teto)
    # o resumo mostra custo REAL acumulado, não mais 0.000
    assert "0.000" not in result.output.split("estimado")[0][-40:]


def _mk_router_stub(ws):
    r = MagicMock()
    r.available_tools.return_value = []
    r._approval_callback = None
    r.workspace = ws
    return r
