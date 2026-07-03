"""Testes para bauer/agent.py — funções utilitárias e run_agent_session."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from bauer.agent import (
    _build_system_prompt,
    _collect_response,
    _extract_text_from_pseudo_json,
    _handle_spec_cmd,
    _run_orchestrator_inline,
    _specs_section,
    _try_parse_tool,
    run_agent_session,
    run_one_turn,
)
from bauer.tool_router import ToolError, ToolRouter


# ─── _extract_text_from_pseudo_json ──────────────────────────────────────────

class TestExtractTextFromPseudoJson:
    def test_returns_none_for_plain_text(self):
        assert _extract_text_from_pseudo_json("Hello world") is None

    def test_extracts_conteudo(self):
        obj = {"action": "resposta", "args": {"conteudo": "Olá!"}}
        result = _extract_text_from_pseudo_json(json.dumps(obj))
        assert result == "Olá!"

    def test_extracts_content(self):
        obj = {"action": "text", "args": {"content": "Hi there"}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) == "Hi there"

    def test_extracts_text(self):
        obj = {"action": "text", "args": {"text": "My answer"}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) == "My answer"

    def test_extracts_resposta(self):
        obj = {"action": "resposta", "args": {"resposta": "Resposta aqui"}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) == "Resposta aqui"

    def test_extracts_message(self):
        obj = {"action": "msg", "args": {"message": "Hello"}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) == "Hello"

    def test_extracts_mensagem(self):
        obj = {"action": "msg", "args": {"mensagem": "Oi"}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) == "Oi"

    def test_extracts_response(self):
        obj = {"action": "r", "args": {"response": "Done"}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) == "Done"

    def test_returns_none_for_list_json(self):
        assert _extract_text_from_pseudo_json("[1, 2, 3]") is None

    def test_returns_none_for_no_known_key(self):
        obj = {"action": "x", "args": {"unknown_key": "val"}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) is None

    def test_returns_none_for_empty_args(self):
        obj = {"action": "x", "args": {}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) is None

    def test_handles_whitespace(self):
        obj = {"action": "t", "args": {"conteudo": "ok"}}
        assert _extract_text_from_pseudo_json("  " + json.dumps(obj) + "  ") == "ok"

    def test_returns_none_if_args_value_not_string(self):
        obj = {"action": "t", "args": {"conteudo": 42}}
        assert _extract_text_from_pseudo_json(json.dumps(obj)) is None


# ─── _try_parse_tool ─────────────────────────────────────────────────────────

class TestTryParseTool:
    def _make_router(self, tmp_path: Path) -> ToolRouter:
        return ToolRouter(workspace=tmp_path)

    def test_returns_none_for_plain_text(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        assert _try_parse_tool("Just a normal answer", router) is None

    def test_parses_valid_json_tool(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        obj = {"action": "list_dir", "args": {"path": "."}}
        result = _try_parse_tool(json.dumps(obj), router)
        assert result is not None
        assert result["action"] == "list_dir"

    def test_parses_markdown_code_block(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        response = '```json\n{"action": "list_dir", "args": {"path": "."}}\n```'
        result = _try_parse_tool(response, router)
        assert result is not None
        assert result["action"] == "list_dir"

    def test_returns_none_for_unknown_action(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        obj = {"action": "nonexistent_tool", "args": {}}
        assert _try_parse_tool(json.dumps(obj), router) is None

    def test_parses_json_at_start_of_response(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        obj = {"action": "list_dir", "args": {"path": "."}}
        # JSON + trailing text
        response = json.dumps(obj) + "\n\nSome extra text after"
        result = _try_parse_tool(response, router)
        assert result is not None
        assert result["action"] == "list_dir"

    def test_returns_none_for_invalid_json(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        assert _try_parse_tool("{bad json", router) is None

    def test_returns_none_for_empty_string(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        assert _try_parse_tool("", router) is None

    def test_parses_read_file_action(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        obj = {"action": "read_file", "args": {"path": "README.md"}}
        result = _try_parse_tool(json.dumps(obj), router)
        assert result is not None
        assert result["action"] == "read_file"


# ─── _specs_section ──────────────────────────────────────────────────────────

class TestSpecsSection:
    def test_returns_string(self):
        result = _specs_section()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_format_hint(self):
        result = _specs_section()
        assert "SPEC" in result

    def test_returns_hint_when_specs_unavailable(self):
        with patch("bauer.spec_manager.SpecManager", side_effect=ImportError):
            result = _specs_section()
        assert isinstance(result, str)

    def test_appends_specs_context_when_present(self, tmp_path: Path):
        # Create a specs dir with a spec
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        spec_content = """id: test-spec
title: Test Spec
version: "1.0"
status: approved
purpose: Testing purposes
behavior:
  - Rule 1
acceptance_criteria:
  - Given A, when B, then C
"""
        (specs_dir / "test-spec.yaml").write_text(spec_content, encoding="utf-8")
        result = _specs_section(str(specs_dir))
        assert isinstance(result, str)

    def test_handles_exception_from_spec_manager(self, tmp_path: Path):
        # Passing a path where SpecManager will just return empty context
        result = _specs_section(str(tmp_path / "nonexistent"))
        assert isinstance(result, str)


# ─── _build_system_prompt ────────────────────────────────────────────────────

class TestBuildSystemPrompt:
    def test_returns_string(self, tmp_path: Path):
        router = ToolRouter(workspace=tmp_path)
        result = _build_system_prompt(router)
        assert isinstance(result, str)

    def test_contains_tool_names(self, tmp_path: Path):
        router = ToolRouter(workspace=tmp_path)
        result = _build_system_prompt(router)
        assert "list_dir" in result
        assert "read_file" in result

    def test_contains_date(self, tmp_path: Path):
        router = ToolRouter(workspace=tmp_path)
        result = _build_system_prompt(router)
        assert "2026" in result  # current year

    def test_contains_json_example(self, tmp_path: Path):
        router = ToolRouter(workspace=tmp_path)
        result = _build_system_prompt(router)
        assert '"action"' in result


# ─── _collect_response ───────────────────────────────────────────────────────

class TestCollectResponse:
    def test_collects_chunks(self):
        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["Hello ", "world", "!"])
        result = _collect_response(mock_client, "phi4-mini", [])
        assert result == "Hello world!"

    def test_collects_single_chunk(self):
        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["response"])
        result = _collect_response(mock_client, "model", [{"role": "user", "content": "hi"}])
        assert result == "response"


# ─── run_one_turn ────────────────────────────────────────────────────────────

class TestRunOneTurn:
    def _make_ctx(self, applied_context: int = 4096, system_prompt: str = "test"):
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=applied_context, system_prompt=system_prompt)
        return ctx

    def test_returns_text_response(self, tmp_path: Path):
        ctx = self._make_ctx()
        ctx.add_user("oi")

        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["Olá!"])
        router = ToolRouter(workspace=tmp_path)

        response, tool_log = run_one_turn(ctx, router, mock_client, "phi4-mini")
        assert response == "Olá!"
        assert tool_log == []

    def test_executes_tool_call_then_final_response(self, tmp_path: Path):
        ctx = self._make_ctx()
        ctx.add_user("liste arquivos")

        mock_client = MagicMock()
        # First response = tool call JSON, second = final text
        list_dir_json = json.dumps({"action": "list_dir", "args": {"path": "."}})
        mock_client.chat_stream.side_effect = [
            iter([list_dir_json]),
            iter(["Aqui estão os arquivos."]),
        ]
        router = ToolRouter(workspace=tmp_path)

        response, tool_log = run_one_turn(ctx, router, mock_client, "phi4-mini")
        assert "arquivos" in response.lower() or response  # final text response
        assert len(tool_log) >= 1
        assert tool_log[0]["tool"] == "list_dir"

    def test_handles_tool_error_gracefully(self, tmp_path: Path):
        ctx = self._make_ctx()
        ctx.add_user("leia arquivo")

        mock_client = MagicMock()
        read_json = json.dumps({"action": "read_file", "args": {"path": "nonexistent.txt"}})
        mock_client.chat_stream.side_effect = [
            iter([read_json]),
            iter(["Não encontrei o arquivo."]),
        ]
        router = ToolRouter(workspace=tmp_path)

        # Should not raise, just return error info in tool_log
        response, tool_log = run_one_turn(ctx, router, mock_client, "phi4-mini")
        assert isinstance(response, str)


# ─── _handle_spec_cmd ────────────────────────────────────────────────────────


def _recording_console():
    """Console Rich real que captura o texto renderizado (inclui células de
    Table — o repr de um Table mockado NÃO mostra o conteúdo das linhas)."""
    from rich.console import Console as _RC
    return _RC(record=True, width=200, force_terminal=False)


class TestHandleSpecCmd:
    def _make_console(self):
        console = MagicMock()
        return console

    def test_spec_list_no_specs(self, tmp_path: Path):
        console = self._make_console()
        with patch("bauer.spec_manager.SpecManager") as mock_cls:
            mock_cls.return_value.list_specs.return_value = []
            _handle_spec_cmd("/spec list", console)
        console.print.assert_called()

    def test_spec_list_with_specs(self, tmp_path: Path):
        console = self._make_console()
        mock_spec = MagicMock()
        mock_spec.id = "my-spec"
        mock_spec.status = "approved"
        mock_spec.purpose = "Testing"
        mock_spec.acceptance_criteria = ["AC1"]

        with patch("bauer.spec_manager.SpecManager") as mock_cls:
            mock_cls.return_value.list_specs.return_value = [mock_spec]
            _handle_spec_cmd("/spec", console)
        console.print.assert_called()

    def test_spec_ls_alias(self):
        console = self._make_console()
        with patch("bauer.spec_manager.SpecManager") as mock_cls:
            mock_cls.return_value.list_specs.return_value = []
            _handle_spec_cmd("/spec ls", console)
        console.print.assert_called()

    def test_spec_new_calls_wizard(self):
        console = self._make_console()
        with patch("bauer.spec_manager.SpecManager") as mock_cls, \
             patch("bauer.spec_wizard.wizard_create_spec") as mock_wizard:
            _handle_spec_cmd("/spec new", console)
        mock_wizard.assert_called_once()

    def test_spec_new_with_id_hint(self):
        console = self._make_console()
        with patch("bauer.spec_manager.SpecManager") as mock_cls, \
             patch("bauer.spec_wizard.wizard_create_spec") as mock_wizard:
            _handle_spec_cmd("/spec new my-feature", console)
        mock_wizard.assert_called_once()
        # ID hint should be printed
        console.print.assert_called()

    def test_spec_get_existing(self):
        console = self._make_console()
        mock_spec = MagicMock()
        mock_spec.id = "existing"
        mock_spec.title = "Existing Spec"
        mock_spec.to_context.return_value = "# Spec Content"

        with patch("bauer.spec_manager.SpecManager") as mock_cls:
            mock_cls.return_value.get.return_value = mock_spec
            _handle_spec_cmd("/spec existing", console)
        console.print.assert_called()

    def test_spec_get_not_found(self):
        console = self._make_console()
        with patch("bauer.spec_manager.SpecManager") as mock_cls:
            mock_cls.return_value.get.return_value = None
            mock_cls.return_value.list_specs.return_value = []
            _handle_spec_cmd("/spec nonexistent", console)
        # Should print warning
        assert console.print.called

    def test_spec_get_not_found_with_existing_specs(self):
        console = self._make_console()
        mock_spec = MagicMock()
        mock_spec.id = "other-spec"
        with patch("bauer.spec_manager.SpecManager") as mock_cls:
            mock_cls.return_value.get.return_value = None
            mock_cls.return_value.list_specs.return_value = [mock_spec]
            _handle_spec_cmd("/spec missing", console)
        assert console.print.called

    def test_spec_list_with_long_purpose(self):
        """purpose > 55 chars deve ser truncada com '…'"""
        console = self._make_console()
        mock_spec = MagicMock()
        mock_spec.id = "long-spec"
        mock_spec.status = "draft"
        mock_spec.purpose = "A" * 100  # > 55 chars
        mock_spec.acceptance_criteria = []
        with patch("bauer.spec_manager.SpecManager") as mock_cls:
            mock_cls.return_value.list_specs.return_value = [mock_spec]
            _handle_spec_cmd("/spec list", console)
        console.print.assert_called()

    def test_spec_list_resolve_do_workspace_nao_cwd(self, tmp_path: Path):
        """Regressão: /spec list dentro do agente lê workspace/specs, não cwd/specs.

        Rodando `bauer agent` da home, o default cwd 'specs/' apontava para uma
        pasta vazia — os specs (que caem em workspace/specs via write_file
        sandbox) nunca apareciam.
        """
        import yaml
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        (specs_dir / "bauerinvest.yaml").write_text(
            yaml.safe_dump({
                "id": "bauerinvest",
                "title": "BauerInvest",
                "purpose": "Plataforma de investimentos",
                "status": "draft",
                "acceptance_criteria": ["login"],
            }),
            encoding="utf-8",
        )
        console = _recording_console()
        _handle_spec_cmd("/spec list", console, tmp_path)
        assert "bauerinvest" in console.export_text()

    def test_spec_list_vazio_mostra_dir_do_workspace(self, tmp_path: Path):
        console = _recording_console()
        _handle_spec_cmd("/spec list", console, tmp_path)
        assert "specs" in console.export_text()  # aponta p/ <workspace>/specs, não some


# ─── _run_orchestrator_inline ────────────────────────────────────────────────

class TestRunOrchestratorInline:
    def _make_orchestrator(self):
        from bauer.orchestrator import StepResult
        orch = MagicMock()
        orch.plan.return_value = [
            {"id": 1, "goal": "Passo 1", "tools": False, "depends_on": []},
        ]
        orch._topological_batches.return_value = [
            [{"id": 1, "goal": "Passo 1", "tools": False, "depends_on": []}],
        ]
        step_result = StepResult(
            id=1, goal="Passo 1", model_used="phi4-mini",
            response="Resultado do passo 1", tool_log=[]
        )
        orch.execute_parallel_steps.return_value = [step_result]
        orch.synthesize.return_value = "Resposta final sintetizada"
        return orch

    def test_returns_final_response(self):
        console = MagicMock()
        orch = self._make_orchestrator()
        result = _run_orchestrator_inline("tarefa complexa", orch, console)
        assert result == "Resposta final sintetizada"

    def test_calls_plan_and_synthesize(self):
        console = MagicMock()
        orch = self._make_orchestrator()
        _run_orchestrator_inline("minha tarefa", orch, console)
        orch.plan.assert_called_once_with("minha tarefa")
        orch.synthesize.assert_called_once()

    def test_returns_empty_on_plan_error(self):
        console = MagicMock()
        orch = MagicMock()
        orch.plan.side_effect = RuntimeError("modelo offline")
        result = _run_orchestrator_inline("tarefa", orch, console)
        assert result == ""

    def test_returns_empty_when_no_steps(self):
        console = MagicMock()
        orch = MagicMock()
        orch.plan.return_value = []
        result = _run_orchestrator_inline("tarefa", orch, console)
        assert result == ""

    def test_handles_keyboard_interrupt(self):
        console = MagicMock()
        orch = MagicMock()
        orch.plan.return_value = [{"id": 1, "goal": "step", "tools": False, "depends_on": []}]
        orch._topological_batches.return_value = [
            [{"id": 1, "goal": "step", "tools": False, "depends_on": []}]
        ]
        orch.execute_parallel_steps.side_effect = KeyboardInterrupt()
        result = _run_orchestrator_inline("tarefa", orch, console)
        assert result == ""

    def test_handles_step_execution_error(self):
        console = MagicMock()
        orch = MagicMock()
        orch.plan.return_value = [{"id": 1, "goal": "step", "tools": False, "depends_on": []}]
        orch._topological_batches.return_value = [
            [{"id": 1, "goal": "step", "tools": False, "depends_on": []}]
        ]
        orch.execute_parallel_steps.side_effect = RuntimeError("step failed")
        # No results → returns ""
        result = _run_orchestrator_inline("tarefa", orch, console)
        assert result == ""

    def test_with_tool_log_in_results(self):
        from bauer.orchestrator import StepResult
        console = MagicMock()
        orch = MagicMock()
        orch.plan.return_value = [{"id": 1, "goal": "step", "depends_on": []}]
        orch._topological_batches.return_value = [
            [{"id": 1, "goal": "step", "depends_on": []}]
        ]
        step_result = StepResult(
            id=1, goal="step", model_used="phi4-mini",
            response="done", tool_log=[{"tool": "list_dir", "result": "files"}]
        )
        orch.execute_parallel_steps.return_value = [step_result]
        orch.synthesize.return_value = "final"
        result = _run_orchestrator_inline("tarefa", orch, console)
        assert result == "final"

    def test_handles_synthesis_error(self):
        from bauer.orchestrator import StepResult
        console = MagicMock()
        orch = self._make_orchestrator()
        orch.synthesize.side_effect = RuntimeError("synthesis failed")
        # Should fallback to concatenated responses
        result = _run_orchestrator_inline("tarefa", orch, console)
        assert "Resultado do passo 1" in result

    def test_parallel_batch_display(self):
        from bauer.orchestrator import StepResult
        console = MagicMock()
        orch = MagicMock()
        orch.plan.return_value = [
            {"id": 1, "goal": "p1", "depends_on": []},
            {"id": 2, "goal": "p2", "depends_on": []},
        ]
        orch._topological_batches.return_value = [
            [{"id": 1, "goal": "p1", "depends_on": []},
             {"id": 2, "goal": "p2", "depends_on": []}],
        ]
        r1 = StepResult(id=1, goal="p1", model_used="m", response="r1", tool_log=[])
        r2 = StepResult(id=2, goal="p2", model_used="m", response="r2", tool_log=[])
        orch.execute_parallel_steps.return_value = [r1, r2]
        orch.synthesize.return_value = "final"
        result = _run_orchestrator_inline("t", orch, console)
        assert result == "final"


# ─── run_agent_session ───────────────────────────────────────────────────────

class TestRunAgentSession:
    """Testa o loop principal do agente com console.input mockado."""

    def _make_client(self, response: str = "Olá!") -> MagicMock:
        client = MagicMock()
        client.chat_stream.return_value = iter([response])
        return client

    def _make_router(self, tmp_path: Path) -> ToolRouter:
        return ToolRouter(workspace=tmp_path)

    def _run_session(
        self, tmp_path: Path, inputs: list[str], client=None, **kwargs
    ):
        """Helper: roda run_agent_session com inputs simulados e stdout capturado."""
        router = self._make_router(tmp_path)
        if client is None:
            client = self._make_client()

        console = MagicMock()
        inputs_iter = iter(inputs)
        console.input.side_effect = lambda *a, **kw: next(inputs_iter)

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()
            mock_stdout.flush = MagicMock()
            mock_stdout.isatty.return_value = False
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                run_agent_session(
                    client=client,
                    model_name="phi4-mini",
                    applied_context=4096,
                    console=console,
                    router=router,
                    **kwargs,
                )
        return console

    def test_exit_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/exit"])
        console.print.assert_called()

    def test_quit_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/quit"])
        console.print.assert_called()

    def test_sair_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/sair"])
        console.print.assert_called()

    def test_clear_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/clear", "/exit"])
        console.print.assert_called()

    def test_limpar_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/limpar", "/exit"])
        console.print.assert_called()

    def test_status_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/status", "/exit"])
        console.print.assert_called()

    def test_stats_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/stats", "/exit"])
        console.print.assert_called()

    def test_model_command(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/model", "/exit"])
        console.print.assert_called()

    def test_sessions_command_no_store(self, tmp_path: Path):
        console = self._run_session(tmp_path, ["/sessions", "/exit"])
        console.print.assert_called()

    def test_sessions_command_with_store(self, tmp_path: Path):
        store = MagicMock()
        store.list_sessions.return_value = ["sess01", "sess02"]
        console = self._run_session(
            tmp_path, ["/sessions", "/exit"], session_store=store
        )
        console.print.assert_called()

    def test_sessions_command_store_empty(self, tmp_path: Path):
        store = MagicMock()
        store.list_sessions.return_value = []
        console = self._run_session(
            tmp_path, ["/sessions", "/exit"], session_store=store
        )
        console.print.assert_called()

    def test_empty_input_continues(self, tmp_path: Path):
        # Empty input should be skipped
        console = self._run_session(tmp_path, ["", "/exit"])
        console.print.assert_called()

    def test_regular_message_gets_response(self, tmp_path: Path):
        client = self._make_client("Olá, como posso ajudar?")
        console = self._run_session(tmp_path, ["oi", "/exit"], client=client)
        console.print.assert_called()

    def test_eoferror_exits_gracefully(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        console = MagicMock()
        console.input.side_effect = EOFError()

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()
            mock_stdout.flush = MagicMock()
            mock_stdout.isatty.return_value = False
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                run_agent_session(
                    client=self._make_client(),
                    model_name="phi4-mini",
                    applied_context=4096,
                    console=console,
                    router=router,
                )
        console.print.assert_called()

    def test_keyboard_interrupt_exits_gracefully(self, tmp_path: Path):
        router = self._make_router(tmp_path)
        console = MagicMock()
        console.input.side_effect = KeyboardInterrupt()

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = MagicMock()
            mock_stdout.flush = MagicMock()
            mock_stdout.isatty.return_value = False
            with patch("sys.stdin") as mock_stdin:
                mock_stdin.isatty.return_value = False
                run_agent_session(
                    client=self._make_client(),
                    model_name="phi4-mini",
                    applied_context=4096,
                    console=console,
                    router=router,
                )
        console.print.assert_called()

    def test_exit_with_session_store(self, tmp_path: Path):
        store = MagicMock()
        store.load.return_value = []
        console = self._run_session(
            tmp_path, ["/exit"],
            session_store=store, session_id="test-session"
        )
        # Should save session on exit
        store.save.assert_called()

    def test_clear_with_session_store(self, tmp_path: Path):
        store = MagicMock()
        store.load.return_value = []
        console = self._run_session(
            tmp_path, ["/clear", "/exit"],
            session_store=store, session_id="test-session"
        )
        console.print.assert_called()

    def test_status_with_session_id(self, tmp_path: Path):
        store = MagicMock()
        store.load.return_value = []
        console = self._run_session(
            tmp_path, ["/status", "/exit"],
            session_store=store, session_id="my-session"
        )
        console.print.assert_called()

    def test_spec_cmd_dispatched(self, tmp_path: Path):
        with patch("bauer.agent._handle_spec_cmd") as mock_handle:
            console = self._run_session(tmp_path, ["/spec list", "/exit"])
        mock_handle.assert_called_once()

    def test_specs_cmd_dispatched(self, tmp_path: Path):
        with patch("bauer.agent._handle_spec_cmd") as mock_handle:
            console = self._run_session(tmp_path, ["/specs", "/exit"])
        mock_handle.assert_called_once()

    def test_session_loaded_from_store(self, tmp_path: Path):
        store = MagicMock()
        store.load.return_value = [{"role": "user", "content": "previous"}]
        console = self._run_session(
            tmp_path, ["/exit"],
            session_store=store, session_id="existing-session"
        )
        store.load.assert_called_once_with("existing-session")

    def test_ollama_error_handled(self, tmp_path: Path):
        from bauer.ollama_client import OllamaError
        client = MagicMock()
        client.chat_stream.side_effect = OllamaError("connection refused")

        console = self._run_session(tmp_path, ["oi", "/exit"], client=client)
        console.print.assert_called()

    def test_keyboard_interrupt_during_stream(self, tmp_path: Path):
        client = MagicMock()
        client.chat_stream.side_effect = KeyboardInterrupt()

        console = self._run_session(tmp_path, ["oi", "/exit"], client=client)
        console.print.assert_called()

    def test_routing_disabled_uses_default_model(self, tmp_path: Path):
        """Sem model_router, usa o modelo padrão."""
        client = self._make_client("resposta")
        console = self._run_session(tmp_path, ["pergunta", "/exit"], client=client)
        console.print.assert_called()

    def test_with_model_router_direct_route(self, tmp_path: Path):
        from bauer.model_router import ModelRouter, Route, RouteKind, RouterConfig
        router_config = RouterConfig(enabled=True)
        mock_model_router = MagicMock(spec=ModelRouter)
        mock_model_router.config = router_config
        mock_model_router.select_model.return_value = ("phi4-mini", MagicMock(kind="direct", label="direct"))

        client = self._make_client("resposta direta")
        console = self._run_session(
            tmp_path, ["pergunta", "/exit"],
            client=client,
            model_router=mock_model_router,
        )
        console.print.assert_called()

    def test_pseudo_json_response_extracted(self, tmp_path: Path):
        """Resposta em formato pseudo-JSON deve ser extraída como texto."""
        pseudo_json = json.dumps({"action": "resposta", "args": {"conteudo": "Olá!"}})
        client = self._make_client(pseudo_json)
        console = self._run_session(tmp_path, ["oi", "/exit"], client=client)
        console.print.assert_called()


# ─── /agent list — pool mesclado (builtins + user) ───────────────────────────

class TestHandleAgentCmdList:
    """Regressão: /agent list dentro do agente mostra os 10 especialistas
    embutidos + agents do usuário — antes lia 'agents.yaml' do cwd e, rodando
    da home, dizia 'nenhum agent' mesmo com os especialistas disponíveis."""

    def test_agent_list_mostra_builtins_sem_arquivo_do_usuario(self, tmp_path, monkeypatch):
        from bauer.agent import _handle_agent_cmd
        # aponta o agents.yaml do usuário para pasta inexistente (hermético)
        monkeypatch.setenv("BAUER_AGENTS_FILE", str(tmp_path / "nao_existe.yaml"))
        console = _recording_console()
        _handle_agent_cmd("/agents", console)
        printed = console.export_text()
        # pelo menos um especialista embutido conhecido aparece
        assert "specialist" in printed or "especialista" in printed
        assert "Nenhum agent" not in printed

    def test_agent_list_inclui_agent_do_usuario(self, tmp_path, monkeypatch):
        import yaml
        from bauer.agent import _handle_agent_cmd
        user_file = tmp_path / "agents.yaml"
        user_file.write_text(yaml.safe_dump({
            "agents": [{
                "name": "meu-bot", "description": "Bot custom do usuario",
                "system": "Voce e um bot.", "tools": [],
            }]
        }), encoding="utf-8")
        monkeypatch.setenv("BAUER_AGENTS_FILE", str(user_file))
        console = _recording_console()
        _handle_agent_cmd("/agent list", console)
        assert "meu-bot" in console.export_text()


# ─── /project — lista projetos governados pela App Factory ───────────────────

class TestHandleProjectCmd:
    def test_project_lista_projetos_governados(self, tmp_path):
        from bauer.agent import _handle_project_cmd
        from bauer import app_factory as af
        proj = tmp_path / "bauerinvest"
        proj.mkdir()
        af.init_project(proj, idea="Plataforma de investimentos")
        af.set_active_project(tmp_path, proj)
        console = _recording_console()
        _handle_project_cmd(console, tmp_path)
        assert "bauerinvest" in console.export_text()

    def test_project_sem_nada_nao_quebra(self, tmp_path):
        from bauer.agent import _handle_project_cmd
        console = _recording_console()
        _handle_project_cmd(console, tmp_path)  # não deve levantar
        assert console.export_text()  # produziu alguma saída
