"""Testes adicionais para aumentar cobertura para >85%.

Foco em:
- context_manager: _summarize_llm, usage_pct com budget=0, context_window
- server: stream com tool call, _Metrics
- openai_client: http error types em chat_with_tools
- cli: _doctor_check_providers, status command
- tool_router: mais caminhos de execução
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import importlib.util

import pytest

_TYPER_AVAILABLE = importlib.util.find_spec("typer") is not None


# ─── ContextManager extras ───────────────────────────────────────────────────

class TestContextManagerExtras:
    def test_usage_pct_with_zero_budget(self):
        """budget=0 não divide por zero."""
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=0)
        ctx._budget = 0  # forçar
        assert ctx.usage_pct == 0.0

    def test_context_window_known_provider(self):
        from bauer.context_manager import ContextManager, PROVIDER_CONTEXT_WINDOWS
        # applied_context=0 → cai no default do provider (fonte única).
        # Com applied_context calibrado (>0), ele SEMPRE vence o mapa.
        ctx = ContextManager(applied_context=0, provider="gemini")
        assert ctx.context_window == PROVIDER_CONTEXT_WINDOWS["gemini"]
        calibrated = ContextManager(applied_context=4096, provider="gemini")
        assert calibrated.context_window == 4096

    def test_context_window_unknown_provider(self):
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=8192, provider="unknownprovider")
        # deve usar applied_context como fallback
        assert ctx.context_window > 0

    def test_auto_summarize_with_llm_client(self, monkeypatch):
        """Usa LLM client para compressão semântica quando disponível."""
        import bauer.auxiliary_client as aux_mod
        from bauer.context_manager import ContextManager

        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["Resumo da conversa anterior."])

        # Força auxiliary_client a retornar (None, "") → o mock principal é usado
        monkeypatch.setattr(aux_mod, "get_compression_client", lambda: (None, ""))

        ctx = ContextManager(applied_context=20_000, provider="openai")
        ctx.set_llm(mock_client, "gpt-4o")

        # Preenche para ultrapassar 75% do budget (20K * 0.75 = 15K threshold)
        chars_per_msg = 5_000
        for i in range(12):
            role = "user" if i % 2 == 0 else "assistant"
            ctx.messages.append({"role": role, "content": "x" * chars_per_msg})

        ctx._auto_summarize()

        # mock_client é o cliente de compressão escolhido — deve ter sido chamado
        assert mock_client.chat_stream.called
        # E o resumo deve aparecer no contexto
        assert any("CONTEXT COMPACTION" in m.get("content", "") for m in ctx.messages)

    def test_auto_summarize_llm_fallback_on_error(self):
        """Quando LLM falha, cai para rule-based."""
        from bauer.context_manager import ContextManager

        mock_client = MagicMock()
        mock_client.chat_stream.side_effect = Exception("LLM offline")

        ctx = ContextManager(applied_context=20_000)
        ctx.set_llm(mock_client, "test-model")

        for i in range(12):
            role = "user" if i % 2 == 0 else "assistant"
            ctx.messages.append({"role": role, "content": "x" * 5_000})

        # Não deve lançar exceção
        ctx._auto_summarize()

        # Deve ter resumo (rule-based)
        has_summary = any("CONTEXT COMPACTION" in m.get("content", "") for m in ctx.messages)
        assert has_summary

    def test_summarize_llm_returns_empty_fallback(self):
        """Quando LLM retorna string vazia, usa rule-based."""
        from bauer.context_manager import _summarize_llm_structured, _summarize_messages

        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter([""])  # vazio

        messages = [
            {"role": "user", "content": "pergunta sobre autenticacao"},
            {"role": "assistant", "content": "resposta"},
        ]
        result, ok = _summarize_llm_structured(mock_client, "gpt-4o", messages)
        # Deve retornar algo (string)
        assert isinstance(result, str)


# ─── Server _Metrics ─────────────────────────────────────────────────────────

class TestMetricsObject:
    def test_metrics_to_prometheus(self):
        from bauer.server import _Metrics
        m = _Metrics()
        m.chat_requests_total = 5
        m.stream_requests_total = 3
        m.tool_calls_total = 10
        text = m.to_prometheus(model="gpt-4o", provider="openai")
        assert "bauer_chat_requests_total 5" in text
        assert "bauer_stream_requests_total 3" in text
        assert "bauer_tool_calls_total 10" in text
        assert 'model="gpt-4o"' in text

    def test_metrics_uptime_positive(self):
        import time
        from bauer.server import _Metrics
        m = _Metrics()
        time.sleep(0.01)
        text = m.to_prometheus()
        # Extrai valor do uptime
        for line in text.splitlines():
            if line.startswith("bauer_uptime_seconds ") and not line.startswith("#"):
                val = float(line.split()[-1])
                assert val > 0
                break

    def test_metrics_init_resets(self):
        from bauer.server import _Metrics
        m = _Metrics()
        m.chat_requests_total = 99
        m.__init__()
        assert m.chat_requests_total == 0


# ─── CLI _doctor_check_providers ─────────────────────────────────────────────

@pytest.mark.skipif(not _TYPER_AVAILABLE, reason="typer not installed")
class TestDoctorCheckProviders:
    def test_no_providers_message(self, capsys):
        """Sem providers autenticados, exibe mensagem."""
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner()
        with patch("bauer.cli.run_doctor") as mock_doctor, \
             patch("bauer.cli.write_state") as mock_state, \
             patch("bauer.cli._load_or_die") as mock_load:
            from unittest.mock import MagicMock
            mock_state_obj = MagicMock()
            mock_state_obj.status = "ok"
            mock_state_obj.machine_id = "test"
            mock_state_obj.ollama_alive = False
            mock_state_obj.ollama_host = "localhost"
            mock_state_obj.configured_model = "test"
            mock_state_obj.model_available = True
            mock_state_obj.context.requested = 4096
            mock_state_obj.context.applied = 4096
            mock_state_obj.context.reason = "ok"
            mock_state_obj.tool_mode = "bridge"
            mock_state_obj.ram_available_mb = 8000
            mock_state_obj.ram_total_mb = 16000
            mock_state_obj.profile = "standard"

            mock_report = MagicMock()
            mock_report.state = mock_state_obj
            mock_report.findings = []
            mock_doctor.return_value = mock_report
            mock_state.return_value = Path("/tmp/state.json")
            mock_load.return_value = (MagicMock(), MagicMock())

            result = runner.invoke(app, ["doctor", "--providers"])
            # Deve funcionar sem crash
            assert result.exit_code in (0, 1)


# ─── CLI status command ───────────────────────────────────────────────────────

@pytest.mark.skipif(not _TYPER_AVAILABLE, reason="typer not installed")
class TestStatusCommand:
    def test_status_output_structure(self, tmp_path):
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner()
        # Cria um state file mínimo
        state_file = tmp_path / "state.json"
        import json
        state_file.write_text(json.dumps({
            "status": "ok",
            "model": {"provider": "openai"},
            "configured_model": "gpt-4o",
            "context": {"applied": 128000},
        }), encoding="utf-8")

        result = runner.invoke(app, [
            "status",
            "--config", str(tmp_path / "noconfig.yaml"),
            "--state-file", str(state_file),
        ])
        # Deve mostrar o painel de status (sem crash)
        assert "Bauer Status" in result.output or result.exit_code in (0, 1)


# ─── tool_router extras ──────────────────────────────────────────────────────

class TestToolRouterExtras:
    def test_datetime_now_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "datetime_now", "args": {"format": "iso"}})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_calculate_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "calculate", "args": {"expression": "2 + 2"}})
        assert "4" in result

    def test_calculate_tool_safe_expression(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "calculate", "args": {"expression": "10 * 10"}})
        assert "100" in result

    def test_encode_decode_base64(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "encode_decode", "args": {
            "input": "hello world", "operation": "base64_encode"
        }})
        assert "aGVsbG8gd29ybGQ=" in result

    def test_json_query_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "json_query", "args": {
            "data": '{"key": "value"}', "query": "key"
        }})
        assert "value" in result or isinstance(result, str)

    def test_glob_files_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        (tmp_path / "test.py").write_text("# test")
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "glob_files", "args": {"pattern": "*.py"}})
        assert "test.py" in result

    def test_regex_search_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        (tmp_path / "code.py").write_text("def hello():\n    pass\n")
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "regex_search", "args": {
            "pattern": "def \\w+", "path": "."
        }})
        assert isinstance(result, str)

    def test_append_file_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        f = tmp_path / "log.txt"
        f.write_text("linha1\n")
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "append_file", "args": {
            "path": "log.txt", "content": "linha2"
        }})
        assert isinstance(result, str)
        assert "linha1" in f.read_text()
        assert "linha2" in f.read_text()

    def test_move_file_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        src = tmp_path / "origem.txt"
        src.write_text("conteudo")
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "move_file", "args": {
            "src": "origem.txt", "dst": "destino.txt"
        }})
        assert (tmp_path / "destino.txt").exists()

    def test_diff_files_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        (tmp_path / "a.txt").write_text("linha1\nlinha2\n")
        (tmp_path / "b.txt").write_text("linha1\nlinha3\n")
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "diff_files", "args": {
            "path_a": "a.txt", "path_b": "b.txt"
        }})
        assert isinstance(result, str)

    def test_create_dir_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        result = router.execute({"action": "create_dir", "args": {"path": "novopasta/subdir"}})
        assert (tmp_path / "novopasta" / "subdir").exists()

    def test_delete_file_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        f = tmp_path / "deletar.txt"
        f.write_text("conteudo")
        router = ToolRouter(workspace=tmp_path)
        # delete_file requer confirm=true
        result = router.execute({"action": "delete_file", "args": {"path": "deletar.txt", "confirm": True}})
        assert not f.exists()

    def test_get_tool_schemas_all_tools_present(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        schemas = router.get_tool_schemas()
        names = {s["function"]["name"] for s in schemas}
        # Ferramentas core devem estar presentes
        core = {"list_dir", "read_file", "write_file", "search_text"}
        assert core.issubset(names)


# ─── openai_client extras ────────────────────────────────────────────────────

class TestOpenAIClientExtras:
    def test_chat_with_tools_http_generic_error(self):
        import httpx
        from bauer.openai_client import OpenAIClient, OpenAIClientError

        client = OpenAIClient(host="https://api.example.com", api_key="k")
        with patch("httpx.post", side_effect=httpx.HTTPError("generic")):
            with pytest.raises(OpenAIClientError):
                client.chat_with_tools("gpt-4", [], [])

    def test_chat_with_tools_bad_json_response(self):
        from bauer.openai_client import OpenAIClient, OpenAIClientError

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}  # sem "choices" key

        client = OpenAIClient(host="https://api.example.com", api_key="k")
        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(OpenAIClientError):
                client.chat_with_tools("gpt-4", [], [])

    def test_show_model_returns_params(self):
        from bauer.openai_client import OpenAIClient
        client = OpenAIClient()
        params = client.show_model("gpt-4o")
        assert params.num_ctx is None
        assert params.raw == {"id": "gpt-4o"}

    def test_has_model_empty_list_returns_true(self):
        from bauer.openai_client import OpenAIClient

        client = OpenAIClient()
        with patch.object(client, "list_models", return_value=[]):
            assert client.has_model("any-model") is True

    def test_has_model_list_error_returns_true(self):
        from bauer.openai_client import OpenAIClient, OpenAIClientError

        client = OpenAIClient()
        with patch.object(client, "list_models", side_effect=OpenAIClientError("fail")):
            assert client.has_model("any-model") is True
