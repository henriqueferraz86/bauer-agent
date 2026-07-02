"""Testes para o modo `/loop` (bauer/agent.py) — autonomia turno-após-turno.

Cobre: parsing de flags, resolução de config (flag > config.yaml > default),
o driver `_run_loop_mode` (critério de parada, orçamento, guardrail
cross-turno, Ctrl+C), o wiring do `approval_callback` no `ToolRouter`, e o
dispatch `/loop` dentro de `run_agent_session`.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from bauer.agent import (
    MAX_TOOL_TURNS,
    _parse_loop_args,
    _resolve_loop_config,
    _run_loop_mode,
    _TurnState,
)
from bauer.config_loader import LoopSection
from bauer.context_manager import ContextManager
from bauer.performance_tracker import SessionStats
from bauer.tool_router import ToolError, ToolRouter


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


def _client(side_effect) -> MagicMock:
    """Cliente mock — `side_effect` recebe (*args, **kwargs) e retorna um iterável
    de chunks de texto (mesmo protocolo de `client.chat_stream`)."""
    c = MagicMock()
    c.chat_stream.side_effect = side_effect
    c.last_usage = {}
    return c


def _fixed_response_client(*responses: str) -> MagicMock:
    """Cliente cujo chat_stream devolve uma resposta NOVA por chamada (cicla
    se `responses` acabar) — ao contrário de MagicMock puro, cada chamada
    gera um iterador fresco (comportamento real de um client de verdade)."""
    calls = {"n": 0}

    def _side_effect(*args, **kwargs):
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return iter([responses[idx]])

    return _client(_side_effect)


def _driver_env(client, workspace: Path):
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    console = Console(file=io.StringIO(), force_terminal=False, width=120)
    router = ToolRouter(workspace=workspace)
    stats = SessionStats(model="fake-model", context_tokens=4096, machine_id="x", provider="")
    state = _TurnState(client=client, active_model="fake-model", native_session_ok=False, fb_idx=0, mem_turn_idx=0)
    return ctx, console, router, stats, state


def _run_loop(client, workspace: Path, task="tarefa de teste", overrides=None, loop_skill=None):
    ctx, console, router, stats, state = _driver_env(client, workspace)
    _run_loop_mode(
        task_description=task,
        overrides=overrides or {},
        ctx=ctx,
        router=router,
        state=state,
        console=console,
        fallback_clients=None,
        stats=stats,
        tool_timeout_s=5.0,
        session_store=None,
        session_id=None,
        active_workspace=str(workspace),
        memprov=None,
        loop_skill=loop_skill,
    )
    return console.file.getvalue(), router, state


# ─── _parse_loop_args ─────────────────────────────────────────────────────────


def test_parse_loop_args_all_flags():
    task, overrides = _parse_loop_args(
        "conserta os testes --max-minutes 5 --max-tool-calls 40 --max-cost 1.5 --approval deny_all"
    )
    assert task == "conserta os testes"
    assert overrides == {
        "max_minutes": "5",
        "max_tool_calls": "40",
        "max_cost_usd": "1.5",
        "approval_mode": "deny_all",
    }


def test_parse_loop_args_yolo_flag():
    task, overrides = _parse_loop_args("faz tudo --yolo")
    assert task == "faz tudo"
    assert overrides == {"approval_mode": "yolo"}


def test_parse_loop_args_no_flags():
    task, overrides = _parse_loop_args("apenas uma tarefa sem flags")
    assert task == "apenas uma tarefa sem flags"
    assert overrides == {}


def test_parse_loop_args_empty():
    task, overrides = _parse_loop_args("")
    assert task == ""
    assert overrides == {}


# ─── _resolve_loop_config ───────────────────────────────────────────────────


def test_resolve_loop_config_defaults_when_config_unavailable():
    with patch("bauer.config_loader.load_config", side_effect=FileNotFoundError("no config")):
        cfg = _resolve_loop_config({})
    defaults = LoopSection()
    assert cfg.max_minutes == defaults.max_minutes
    assert cfg.max_tool_calls == defaults.max_tool_calls
    assert cfg.max_cost_usd == defaults.max_cost_usd
    assert cfg.approval_mode == "threshold"


def test_resolve_loop_config_flag_overrides_win():
    with patch("bauer.config_loader.load_config", side_effect=FileNotFoundError):
        cfg = _resolve_loop_config({"max_minutes": "5", "max_cost_usd": "0.5", "approval_mode": "yolo"})
    assert cfg.max_minutes == 5
    assert cfg.max_cost_usd == 0.5
    assert cfg.approval_mode == "yolo"


def test_resolve_loop_config_invalid_number_raises_value_error():
    with patch("bauer.config_loader.load_config", side_effect=FileNotFoundError):
        with pytest.raises(ValueError, match="max-cost"):
            _resolve_loop_config({"max_cost_usd": "not-a-number"})


def test_resolve_loop_config_invalid_approval_mode_raises_value_error():
    with patch("bauer.config_loader.load_config", side_effect=FileNotFoundError):
        with pytest.raises(ValueError):
            _resolve_loop_config({"approval_mode": "bogus"})


# ─── ToolRouter approval_callback wiring (regressão + /loop) ────────────────


def test_router_approval_callback_defaults_to_none(ws: Path):
    router = ToolRouter(workspace=ws)
    assert router._approval_callback is None


def test_router_passes_approval_callback_to_guard_check(ws: Path):
    """Regressão do plano: o router SEMPRE passa yolo=False e delega a decisão
    de risco ao callback — nunca ao próprio ToolRouter."""
    from bauer.approval import ApprovalDecision

    router = ToolRouter(workspace=ws, shell_runner=MagicMock())
    sentinel_cb = lambda cmd, desc: "deny"
    router._approval_callback = sentinel_cb

    with patch("bauer.tool_router._check_command_guards") as mock_guard:
        mock_guard.return_value = ApprovalDecision(action="denied", reason="test", scope="test")
        with pytest.raises(ToolError):
            router.execute({"action": "run_command", "args": {"command": "echo hi"}})

    mock_guard.assert_called_once()
    _, kwargs = mock_guard.call_args
    assert kwargs["approval_callback"] is sentinel_cb
    assert kwargs["yolo"] is False


def test_router_dangerous_command_denied_without_callback(ws: Path):
    """Sem approval_callback (comportamento fora do /loop) um comando
    DANGEROUS continua negado — exatamente como antes desta feature."""
    from bauer.approval import revoke_session, save_permanent_allowlist

    router = ToolRouter(workspace=ws, shell_runner=MagicMock())
    assert router._approval_callback is None
    save_permanent_allowlist(set())
    revoke_session()

    with pytest.raises(ToolError, match=r"\[BLOCKED\]"):
        router.execute({"action": "run_command", "args": {"command": "rm -rf /tmp/build"}})


def test_router_hardline_never_bypassed_even_with_yolo_callback(ws: Path):
    router = ToolRouter(workspace=ws, shell_runner=MagicMock())
    router._approval_callback = lambda cmd, desc: "always"  # sempre aprova

    with pytest.raises(ToolError, match=r"\[BLOCKED\]"):
        router.execute({"action": "run_command", "args": {"command": "rm -rf /"}})


# ─── _run_loop_mode — critério de parada ────────────────────────────────────


def test_loop_stops_after_two_consecutive_text_replies(ws: Path):
    client = _fixed_response_client("Tarefa concluída, nada mais a fazer.")
    output, router, _ = _run_loop(client, ws)

    assert client.chat_stream.call_count == 2  # 1ª resposta + nudge de confirmação
    assert "tarefa concluída" in output.lower()
    assert router._approval_callback is None  # resetado ao sair


def test_loop_confirm_pending_resets_when_tool_call_follows_nudge(ws: Path, capsys):
    """Texto (nudge) -> o modelo volta a chamar tool antes de responder ->
    essa rodada NÃO conta como sinal de "terminei" (teve tool call no meio);
    só para quando uma rodada SEM nenhuma tool call vier duas vezes seguidas."""
    responses = [
        "Acho que terminei.",  # round 1 (outer): final, sem tools -> dispara nudge
        '{"action": "list_dir", "args": {"path": "."}}',  # round 2: 1o passo interno, chama tool
        "Ainda trabalhando, um segundo.",  # round 2: 2o passo interno -> final da rodada 2, MAS com tool_log -> não conta como sinal, reseta pending
        "Agora sim terminei.",  # round 3 (outer): final, sem tools -> dispara nudge de novo
        "Confirmado, terminei.",  # round 4 (outer): final, sem tools de novo -> para
    ]
    client = _fixed_response_client(*responses)
    output, _, _ = _run_loop(client, ws)
    # As respostas "final" são escritas via sys.stdout.write (não console.print),
    # então ficam em stdout puro — capturado pelo capsys, não pelo console.file.
    stdout_text = capsys.readouterr().out

    assert client.chat_stream.call_count == 5
    assert "ainda trabalhando" in stdout_text.lower()
    assert "confirmado, terminei" in stdout_text.lower()
    assert "/loop encerrado" in output


def test_loop_budget_exhaustion_stops_driver(ws: Path):
    """Cliente que sempre chama uma tool (nunca responde só texto) — o /loop
    deve parar exatamente ao esgotar max_tool_calls, sem propagar exceção."""
    tool_action = '{"action": "list_dir", "args": {"path": "."}}'
    client = _fixed_response_client(*([tool_action] * 50))

    output, _, _ = _run_loop(client, ws, overrides={"max_tool_calls": "3", "max_minutes": "5"})

    assert "orçamento" in output.lower()
    assert "/loop encerrado" in output


def test_loop_interrupted_by_keyboard_interrupt_does_not_propagate(ws: Path):
    def _side_effect(*args, **kwargs):
        raise KeyboardInterrupt()

    client = _client(_side_effect)
    output, router, _ = _run_loop(client, ws)  # não deve levantar

    assert "interrompido" in output.lower()
    assert router._approval_callback is None


def test_loop_provider_error_stops_without_fallback(ws: Path):
    from bauer.openai_client import OpenAIClientError

    def _side_effect(*args, **kwargs):
        raise OpenAIClientError("boom")

    client = _client(_side_effect)
    output, _, _ = _run_loop(client, ws)

    assert "/loop encerrado" in output


# ─── _run_loop_mode — guardrail cross-turno ──────────────────────────────────


def test_loop_guardrail_hard_stop_is_cumulative_across_rounds(ws: Path):
    """`hard_stop_total_failures` deve somar falhas de VÁRIAS rodadas (várias
    chamadas a _run_tool_loop_body dentro do mesmo /loop), não reiniciar a
    cada rodada — essa é a garantia central do wiring do guardrail."""
    from bauer.tool_guardrails import GuardrailConfig, ToolCallGuardrailController

    # Threshold baixo pra não precisar de dezenas de rodadas no teste; thresholds
    # por-tool/por-assinatura ficam altos pra não bloquear antes do hard stop.
    tiny_guardrail = ToolCallGuardrailController(
        GuardrailConfig(
            hard_stop_total_failures=2,
            same_tool_block_threshold=100,
            exact_failure_block_threshold=100,
            same_tool_warn_threshold=100,
            exact_failure_warn_threshold=100,
        )
    )

    calls = {"n": 0}

    def _side_effect(*args, **kwargs):
        calls["n"] += 1
        # path diferente a cada chamada — evita o hard-stop de _detect_loop
        # (repetição EXATA), isolando o teste ao guardrail de falhas.
        return iter([f'{{"action": "read_file", "args": {{"path": "missing_{calls["n"]}.txt"}}}}'])

    client = _client(_side_effect)

    with patch("bauer.tool_guardrails.ToolCallGuardrailController", return_value=tiny_guardrail):
        output, _, _ = _run_loop(client, ws)

    assert "guardrail" in output.lower()
    # antes do halt, exatamente 2 falhas reais foram executadas (3ª chamada é bloqueada no before_call)
    assert tiny_guardrail._total_failures == 2


# ─── observabilidade: incident + kanban event ───────────────────────────────


def test_loop_records_incident_with_expected_fields(ws: Path):
    client = _fixed_response_client("Feito.")

    with patch("bauer.incidents.record_incident") as mock_incident:
        _run_loop(client, ws, task="minha tarefa de teste")

    mock_incident.assert_called_once()
    args, kwargs = mock_incident.call_args
    assert args[0] == "autonomous_loop_stopped"
    assert kwargs["reason"] == "completed"
    assert kwargs["task_description"].startswith("minha tarefa de teste")
    assert "elapsed_seconds" in kwargs
    assert "tool_calls" in kwargs
    assert "llm_calls" in kwargs
    assert "cost_usd" in kwargs


def test_loop_appends_kanban_event_best_effort(ws: Path):
    client = _fixed_response_client("Feito.")

    _run_loop(client, ws)

    from bauer.kanban_store import KanbanStore

    events = KanbanStore(ws).list_events(limit=5)
    assert any(e.event_type == "autonomous_loop_stopped" for e in events)


def test_loop_survives_kanban_failure(ws: Path):
    """Observabilidade nunca deve derrubar o /loop, mesmo se o KanbanStore falhar."""
    client = _fixed_response_client("Feito.")

    with patch("bauer.kanban_store.KanbanStore", side_effect=RuntimeError("disk full")):
        output, _, _ = _run_loop(client, ws)  # não deve levantar

    assert "/loop encerrado" in output


# ─── dispatch `/loop` dentro de run_agent_session ───────────────────────────


def test_run_agent_session_loop_status_no_active_loop(ws: Path, router: ToolRouter):
    from bauer.agent import run_agent_session

    client = MagicMock()
    console = Console()

    with patch("builtins.input", side_effect=["/loop status", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_run_agent_session_loop_stop_no_active_loop(ws: Path, router: ToolRouter):
    from bauer.agent import run_agent_session

    client = MagicMock()
    console = Console()

    with patch("builtins.input", side_effect=["/loop stop", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_run_agent_session_loop_empty_shows_usage(ws: Path, router: ToolRouter):
    from bauer.agent import run_agent_session

    client = MagicMock()
    console = Console()

    with patch("builtins.input", side_effect=["/loop", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_run_agent_session_loop_runs_task_end_to_end(ws: Path, router: ToolRouter):
    from bauer.agent import run_agent_session

    client = _fixed_response_client("Tarefa concluída via /loop.")
    console = Console()

    with patch("builtins.input", side_effect=["/loop diga oi e pare --max-minutes 1", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    # 1 resposta final + 1 nudge de confirmação = 2 chamadas
    assert client.chat_stream.call_count == 2
    # sessão manual continua normal depois do /loop (não travou/crashou)
    assert router._approval_callback is None


def test_run_agent_session_loop_does_not_leak_approval_callback_to_manual_turn(ws: Path, router: ToolRouter):
    """Depois que o /loop termina, uma mensagem manual comum não deve herdar
    o approval_callback do /loop (ele já foi resetado para None)."""
    from bauer.agent import run_agent_session

    client = _fixed_response_client(
        "Feito via /loop.",  # /loop round 1 -> final
        "Confirmado.",  # /loop round 2 (nudge) -> final -> encerra /loop
        "Oi! Tudo bem?",  # turno manual seguinte
    )
    console = Console()

    with patch("builtins.input", side_effect=["/loop tarefa curta", "oi", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert router._approval_callback is None
    assert client.chat_stream.call_count == 3


# ─── loop-skills: gate de verificação (_run_loop_mode com loop_skill=) ──────


def _make_skill(**overrides):
    from bauer.loop_skills import LoopSkill

    defaults = dict(
        name="test-skill", trigger_pattern="x", task_template="faz x", max_minutes=5,
    )
    defaults.update(overrides)
    return LoopSkill(**defaults)


def test_loop_skill_verification_pass_no_retry_needed(ws: Path):
    client = _fixed_response_client("Feito.")
    skill = _make_skill(verify_command="python -c \"pass\"")

    output, _, _ = _run_loop(client, ws, loop_skill=skill)

    # 2 chamadas (final + nudge de confirmação) — nenhuma rodada extra de correção.
    assert client.chat_stream.call_count == 2
    assert "verificação passou" in output.lower()
    assert "tentando 1 correção" not in output.lower()


def test_loop_skill_verification_fail_then_retry_then_pass(ws: Path):
    calls = {"n": 0}

    def _verify_side_effect(*a, **k):
        calls["n"] += 1
        rc = 1 if calls["n"] == 1 else 0
        proc = MagicMock(returncode=rc, stdout="", stderr="")
        return proc

    client = _fixed_response_client("Feito.")
    skill = _make_skill(verify_command="echo ok")

    with patch("subprocess.run", side_effect=_verify_side_effect):
        output, _, _ = _run_loop(client, ws, loop_skill=skill)

    assert calls["n"] == 2  # exatamente 1 verificação inicial + 1 reverificação
    # 2 chamadas do /loop normal + 1 rodada extra de correção = 3
    assert client.chat_stream.call_count == 3
    assert "verificação passou após correção" in output.lower()


def test_loop_skill_verification_fail_twice_stops_bounded(ws: Path):
    def _always_fail(*a, **k):
        return MagicMock(returncode=1, stdout="", stderr="erro persistente")

    client = _fixed_response_client("Feito.")
    skill = _make_skill(verify_command="false")

    with patch("subprocess.run", side_effect=_always_fail) as mock_run:
        output, _, _ = _run_loop(client, ws, loop_skill=skill)

    assert mock_run.call_count == 2  # bounded — nunca mais que 2 tentativas
    assert "verificação falhou de novo" in output.lower()
    assert "Motivo: verificação de loop-skill falhou após tentativa de correção" in output

    from bauer.decision_memory import DecisionMemory
    rows = DecisionMemory(db_path=ws / "decisions.db").search("faz x", top_k=5, min_score=0.0)
    assert rows[0].outcome == "bad"
    assert rows[0].score == 0.0


def test_loop_skill_verification_skipped_when_stop_reason_not_completed(ws: Path):
    """budget_exhausted não é 'completed' — a verificação nunca é chamada."""
    tool_action = '{"action": "list_dir", "args": {"path": "."}}'
    client = _fixed_response_client(*([tool_action] * 20))
    skill = _make_skill(verify_auto=True, max_tool_calls=3)

    with patch("bauer.app_verify.verify_project") as mock_verify:
        output, _, _ = _run_loop(client, ws, overrides={"max_tool_calls": "3"}, loop_skill=skill)

    mock_verify.assert_not_called()
    assert "orçamento" in output.lower()


def test_loop_skill_verification_none_when_not_configured(ws: Path):
    """Sem verify_command nem verify_auto: sem gate, mas ainda grava memória (good/0.5)."""
    client = _fixed_response_client("Feito.")
    skill = _make_skill()  # verify_command="" e verify_auto=False (defaults)

    output, _, _ = _run_loop(client, ws, loop_skill=skill)

    assert "verificação" not in output.lower()

    from bauer.decision_memory import DecisionMemory
    rows = DecisionMemory(db_path=ws / "decisions.db").search("faz x", top_k=5, min_score=0.0)
    assert rows[0].outcome == "good"
    assert rows[0].score == 0.5


# ─── loop-skills: gravação de memória estruturada ───────────────────────────


def test_loop_skill_memory_write_good_on_verified_success(ws: Path):
    client = _fixed_response_client("Feito.")
    skill = _make_skill(name="verified-skill", verify_command="python -c \"pass\"")

    _run_loop(client, ws, loop_skill=skill)

    from bauer.decision_memory import DecisionMemory
    rows = DecisionMemory(db_path=ws / "decisions.db").search("faz x", top_k=5, min_score=0.0)
    assert rows[0].outcome == "good"
    assert rows[0].score == 1.0
    assert "verified-skill" in rows[0].tags
    assert "loop-skill" in rows[0].tags


def test_loop_skill_memory_write_neutral_on_budget_exhausted(ws: Path):
    tool_action = '{"action": "list_dir", "args": {"path": "."}}'
    client = _fixed_response_client(*([tool_action] * 20))
    skill = _make_skill(name="budget-skill")

    _run_loop(client, ws, overrides={"max_tool_calls": "2"}, loop_skill=skill)

    from bauer.decision_memory import DecisionMemory
    rows = DecisionMemory(db_path=ws / "decisions.db").search("faz x", top_k=5, min_score=0.0)
    assert rows[0].outcome == "neutral"
    assert rows[0].score == 0.5


def test_loop_skill_memory_write_not_created_for_plain_loop(ws: Path):
    """Um /loop manual (sem loop_skill) não grava em DecisionMemory."""
    client = _fixed_response_client("Feito sem skill.")

    _run_loop(client, ws, task="tarefa manual sem skill")  # loop_skill=None (default)

    from bauer.decision_memory import DecisionMemory
    rows = DecisionMemory(db_path=ws / "decisions.db").search("tarefa manual sem skill", top_k=5, min_score=0.0)
    assert rows == []


# ─── loop-skills: auto-gatilho + cooldown + anti-recursão ───────────────────


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    return tmp_path / "bauer-home"


def _install_loop_skill(bauer_home: Path, filename: str, *, name: str, trigger: str, task: str, extra: str = "") -> Path:
    from bauer.paths import loop_skills_dir

    d = loop_skills_dir()
    p = d / filename
    p.write_text(
        f"name: {name}\ntrigger_pattern: '{trigger}'\ntask_template: '{task}'\n{extra}",
        encoding="utf-8",
    )
    return p


def test_auto_trigger_fires_on_match(ws: Path, router: ToolRouter, bauer_home: Path):
    from bauer.agent import run_agent_session

    _install_loop_skill(
        bauer_home, "fix.yaml", name="fix-tests", trigger="conserta.*testes.*falhando",
        task="Corrija os testes falhando",
    )
    client = _fixed_response_client("Tarefa concluída.")
    console = Console()

    with patch("builtins.input", side_effect=["conserta os testes que estao falhando", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 2  # /loop rodou (final + nudge)


def test_auto_trigger_does_not_fire_on_non_matching_input(ws: Path, router: ToolRouter, bauer_home: Path):
    from bauer.agent import run_agent_session

    _install_loop_skill(
        bauer_home, "fix.yaml", name="fix-tests", trigger="conserta.*testes.*falhando",
        task="Corrija os testes falhando",
    )
    client = _fixed_response_client("Oi! Tudo bem?")
    console = Console()

    with patch("builtins.input", side_effect=["oi, tudo bem?", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    # 1 turno manual normal — não entrou no /loop (que faria 2 chamadas p/ essa resposta).
    assert client.chat_stream.call_count == 1


def test_auto_trigger_no_installed_skills_is_noop(ws: Path, router: ToolRouter, bauer_home: Path):
    """Diretório de loop-skills vazio = recurso é um no-op completo."""
    from bauer.agent import run_agent_session

    client = _fixed_response_client("Resposta normal.")
    console = Console()

    with patch("builtins.input", side_effect=["conserta os testes falhando", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 1  # turno manual, não /loop


def test_auto_trigger_cooldown_prevents_immediate_retrigger(ws: Path, router: ToolRouter, bauer_home: Path):
    from bauer.agent import run_agent_session

    _install_loop_skill(
        bauer_home, "fix.yaml", name="fix-tests", trigger="conserta.*testes.*falhando",
        task="Corrija os testes falhando",
    )
    client = _fixed_response_client("Tarefa concluída.", "Tarefa concluída.", "Resposta manual.")
    console = Console()

    with patch("bauer.agent.time") as mock_time:
        # Primeiro disparo em t=1000 (bem longe do default 0.0 do dict de
        # cooldown, senão o PRIMEIRO match já cairia em "cooldown"); segundo
        # input chega 0.5s depois — dentro do cooldown de 60s.
        mock_time.monotonic.side_effect = [1000.0, 1000.5]
        with patch(
            "builtins.input",
            side_effect=["conserta os testes falhando", "conserta os testes falhando", EOFError],
        ):
            run_agent_session(client, "test-model", 4096, console, router)

    # 1º input: dispara /loop (2 chamadas). 2º input: em cooldown, cai pro turno
    # manual normal (1 chamada) em vez de disparar o /loop de novo.
    assert client.chat_stream.call_count == 3


def test_auto_trigger_does_not_recursively_refire_from_loop_generated_text(
    ws: Path, router: ToolRouter, bauer_home: Path
):
    """Regressão crítica: texto gerado DENTRO do /loop (que por acaso casa o
    mesmo trigger) nunca re-dispara o auto-gatilho — o matcher só roda no
    ponto onde `user_input` é lido de verdade do teclado."""
    from bauer.agent import run_agent_session

    _install_loop_skill(
        bauer_home, "fix.yaml", name="fix-tests", trigger="conserta.*testes.*falhando",
        task="Corrija os testes falhando",
    )
    # A resposta do modelo DENTRO do loop contém a mesma frase-gatilho —
    # se o auto-gatilho fosse re-checado sobre texto gerado, isso re-disparia.
    client = _fixed_response_client(
        "Ainda preciso conserta os testes que estao falhando antes de terminar.",
        "Tarefa concluída.",
    )
    console = Console()

    with patch("builtins.input", side_effect=["conserta os testes que estao falhando", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    output = console.file.getvalue() if hasattr(console.file, "getvalue") else ""
    # Só 2 chamadas (1 round de texto "ainda preciso..." + 1 nudge de confirmação
    # "Tarefa concluída.") — nenhuma chamada extra que indicaria um 2º /loop disparado.
    assert client.chat_stream.call_count == 2


# ─── /loop-skill list / /loop-skill run ─────────────────────────────────────


def test_loop_skill_list_cmd_empty(ws: Path, router: ToolRouter, bauer_home: Path):
    from bauer.agent import run_agent_session

    client = MagicMock()
    console = Console()

    with patch("builtins.input", side_effect=["/loop-skill list", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_loop_skill_list_cmd_shows_installed(ws: Path, router: ToolRouter, bauer_home: Path):
    from bauer.agent import run_agent_session

    _install_loop_skill(
        bauer_home, "fix.yaml", name="fix-tests", trigger="conserta",
        task="Corrija", extra="description: Corrige testes falhando\n",
    )
    client = MagicMock()
    console = Console()

    with patch("builtins.input", side_effect=["/loop-skill list", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_loop_skill_run_cmd_unknown_name(ws: Path, router: ToolRouter, bauer_home: Path):
    from bauer.agent import run_agent_session

    client = MagicMock()
    console = Console()

    with patch("builtins.input", side_effect=["/loop-skill run does-not-exist", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_loop_skill_run_cmd_executes(ws: Path, router: ToolRouter, bauer_home: Path):
    from bauer.agent import run_agent_session

    _install_loop_skill(
        bauer_home, "fix.yaml", name="fix-tests", trigger="conserta os testes",
        task="Corrija os testes",
    )
    client = _fixed_response_client("Feito via /loop-skill run.")
    console = Console()

    with patch("builtins.input", side_effect=["/loop-skill run fix-tests texto livre aqui", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 2  # final + nudge
    assert router._approval_callback is None
