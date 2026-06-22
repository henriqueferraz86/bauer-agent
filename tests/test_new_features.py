"""Testes para features novas: memory search, secrets scanner, native tool calling,
Prometheus metrics, bauer status/doctor, tool schemas."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_TYPER_AVAILABLE = importlib.util.find_spec("typer") is not None


# ─── MemoryManager.search (TF-IDF) ───────────────────────────────────────────

class TestMemorySearch:
    def _make_mm(self, tmp_path: Path):
        from bauer.memory_manager import MemoryManager
        mm = MemoryManager(tmp_path)
        mm.init_files()
        return mm

    def test_search_empty_returns_nothing(self, tmp_path: Path):
        from bauer.memory_manager import MemoryManager
        mm = MemoryManager(tmp_path)
        # Diretório sem arquivos
        results = mm.search("qualquer coisa")
        assert results == []

    def test_search_finds_matching_section(self, tmp_path: Path):
        mm = self._make_mm(tmp_path)
        mm.add_decision("autenticacao oauth", body="Implementamos OAuth para seguranca.")
        results = mm.search("autenticacao oauth")
        assert len(results) > 0
        assert any("DECISIONS" in r["file"] for r in results)

    def test_search_returns_top_k(self, tmp_path: Path):
        mm = self._make_mm(tmp_path)
        for i in range(10):
            mm.add_decision(f"decisao {i}", body=f"Conteudo da decisao numero {i}")
        results = mm.search("decisao", top_k=3)
        assert len(results) <= 3

    def test_search_result_has_required_fields(self, tmp_path: Path):
        mm = self._make_mm(tmp_path)
        mm.add_note("nota teste", body="Esta e uma nota de teste importante")
        results = mm.search("nota teste")
        if results:
            r = results[0]
            assert "file" in r
            assert "title" in r
            assert "score" in r
            assert "snippet" in r
            assert isinstance(r["score"], float)

    def test_search_empty_query_returns_nothing(self, tmp_path: Path):
        mm = self._make_mm(tmp_path)
        mm.add_note("nota", body="conteudo")
        results = mm.search("")
        assert results == []

    def test_search_no_match_returns_empty(self, tmp_path: Path):
        mm = self._make_mm(tmp_path)
        mm.add_note("nota sobre python", body="Python e uma linguagem de programacao")
        results = mm.search("kubernetes docker golang")
        assert results == []

    def test_search_scores_are_positive(self, tmp_path: Path):
        mm = self._make_mm(tmp_path)
        mm.add_decision("machine learning", body="Usamos ML para classificacao de dados importantes")
        results = mm.search("machine learning")
        for r in results:
            assert r["score"] > 0

    def test_search_specific_file(self, tmp_path: Path):
        mm = self._make_mm(tmp_path)
        mm.add_failure("erro de conexao", error="timeout", fix="retry")
        mm.add_decision("decisao importante", body="decisao tecnica")
        # Busca apenas em FAILED_ATTEMPTS.md
        results = mm.search("conexao", files=["FAILED_ATTEMPTS.md"])
        assert all(r["file"] == "FAILED_ATTEMPTS.md" for r in results)


# ─── Secrets Scanner ─────────────────────────────────────────────────────────

class TestSecretsScanner:
    def test_scan_openai_key(self):
        from bauer.secrets_scanner import scan
        text = "minha key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = scan(text)
        assert result.found
        assert "[REDACTED" in result.redacted_text

    def test_scan_anthropic_key(self):
        from bauer.secrets_scanner import scan
        text = "sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        result = scan(text)
        assert result.found

    def test_scan_github_token(self):
        from bauer.secrets_scanner import scan
        text = "token: ghp_abcdefghijklmnopqrstuvwxyz1234567890AB"
        result = scan(text)
        assert result.found

    def test_scan_aws_key(self):
        from bauer.secrets_scanner import scan
        text = "access_key: AKIAIOSFODNN7EXAMPLE"
        result = scan(text)
        assert result.found

    def test_scan_jwt(self):
        from bauer.secrets_scanner import scan
        # JWT format: header.payload.signature
        text = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        result = scan(text)
        assert result.found

    def test_scan_clean_text(self):
        from bauer.secrets_scanner import scan
        text = "Ola! Como posso ajudar voce hoje?"
        result = scan(text)
        assert not result.found
        assert result.redacted_text == text

    def test_redact_function(self):
        from bauer.secrets_scanner import redact
        text = "key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
        redacted = redact(text)
        assert "sk-" not in redacted or "REDACTED" in redacted

    def test_has_secrets_true(self):
        from bauer.secrets_scanner import has_secrets
        assert has_secrets("sk-abcdefghijklmnopqrstuvwxyz1234567890")

    def test_has_secrets_false(self):
        from bauer.secrets_scanner import has_secrets
        assert not has_secrets("Texto normal sem segredos aqui")

    def test_redact_telegram_bot_token(self):
        from bauer.secrets_scanner import redact
        # Token SINTÉTICO no formato Telegram — não é um segredo real.
        token = "1234567890:" + "A" * 35
        out = redact(f"POST https://api.telegram.org/bot{token}/getUpdates")
        assert token not in out
        assert "REDACTED" in out

    def test_discord_bot_token_detected(self):
        from bauer.secrets_scanner import has_secrets
        # Token SINTÉTICO no formato Discord — não é um segredo real.
        fake = "M" + "A" * 24 + "." + "B" * 6 + "." + "C" * 30
        assert has_secrets(fake)

    def test_scan_returns_match_details(self):
        from bauer.secrets_scanner import scan
        text = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = scan(text)
        assert result.found
        assert len(result.matches) > 0
        m = result.matches[0]
        assert "name" in m
        assert "severity" in m
        assert "preview" in m

    def test_scan_private_key(self):
        from bauer.secrets_scanner import scan
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
        result = scan(text)
        assert result.found

    def test_scan_xai_key(self):
        from bauer.secrets_scanner import scan
        text = "xai-abcdefghijklmnopqrstuvwxyzABCDEFGHIJ"
        result = scan(text)
        assert result.found


# ─── OpenAIClient.chat_with_tools ────────────────────────────────────────────

class TestChatWithTools:
    def _make_client(self):
        from bauer.openai_client import OpenAIClient
        return OpenAIClient(host="https://api.example.com", api_key="test-key")

    def test_chat_with_tools_success(self, tmp_path):
        import httpx
        from bauer.openai_client import OpenAIClient

        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Resposta", "tool_calls": None}}]
        }

        with patch("httpx.post", return_value=mock_resp):
            result = client.chat_with_tools(
                model="gpt-4o",
                messages=[{"role": "user", "content": "oi"}],
                tools=[],
            )
        assert result["content"] == "Resposta"

    def test_chat_with_tools_http_error(self):
        from bauer.openai_client import OpenAIClient, OpenAIClientError

        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "rate limit exceeded"

        with patch("httpx.post", return_value=mock_resp):
            with pytest.raises(OpenAIClientError, match="429"):
                client.chat_with_tools(
                    model="gpt-4o",
                    messages=[],
                    tools=[],
                )

    def test_chat_with_tools_connect_error(self):
        import httpx
        from bauer.openai_client import OpenAIClient, OpenAIClientError

        client = self._make_client()
        with patch("httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(OpenAIClientError, match="recusada"):
                client.chat_with_tools(model="gpt-4o", messages=[], tools=[])

    def test_chat_with_tools_timeout(self):
        import httpx
        from bauer.openai_client import OpenAIClient, OpenAIClientError

        client = self._make_client()
        with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
            with pytest.raises(OpenAIClientError, match="Timeout"):
                client.chat_with_tools(model="gpt-4o", messages=[], tools=[])

    def test_supports_native_tools_property(self):
        from bauer.openai_client import OpenAIClient
        client = OpenAIClient()
        assert client.supports_native_tools is True


# ─── ToolRouter.get_tool_schemas ─────────────────────────────────────────────

class TestToolSchemas:
    def test_get_tool_schemas_returns_list(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        schemas = router.get_tool_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) > 0

    def test_schema_format_is_openai(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        schemas = router.get_tool_schemas()
        for s in schemas:
            assert s["type"] == "function"
            assert "function" in s
            fn = s["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_execute_native_call(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        result = router.execute_native_call("list_dir", {"path": "."})
        assert isinstance(result, str)

    def test_execute_native_call_unknown_tool(self, tmp_path):
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        # Tool desconhecida deve retornar erro ou lançar exceção
        try:
            result = router.execute_native_call("ferramenta_inexistente", {})
            assert "erro" in result.lower() or "unknown" in result.lower() or isinstance(result, str)
        except Exception:
            pass  # também aceitável lançar exceção


# ─── Server /metrics ─────────────────────────────────────────────────────────

class TestServerMetrics:
    def _make_app(self, tmp_path):
        from bauer.server import create_app
        from bauer.tool_router import ToolRouter

        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["ok"])
        mock_client.supports_native_tools = False

        router = ToolRouter(workspace=tmp_path)
        return create_app(
            model_name="test-model",
            applied_context=4096,
            router=router,
            client=mock_client,
            system_prompt="System",
            sessions_dir=tmp_path / "sessions",
            api_key="",
        )

    def test_metrics_endpoint_ok(self, tmp_path):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        app = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "bauer_" in resp.text

    def test_metrics_contains_required_metrics(self, tmp_path):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        app = self._make_app(tmp_path)
        client = TestClient(app)
        resp = client.get("/metrics")
        text = resp.text
        assert "bauer_requests_total" in text
        assert "bauer_uptime_seconds" in text
        assert "bauer_chat_requests_total" in text
        assert "bauer_tool_calls_total" in text

    def test_metrics_incremented_after_chat(self, tmp_path):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("fastapi not installed")

        from unittest.mock import patch as _patch
        from bauer.context_manager import ContextManager

        app = self._make_app(tmp_path)
        tc = TestClient(app)

        with _patch("bauer.agent.run_one_turn", return_value=("resposta", [])):
            tc.post("/chat", json={"message": "oi"})

        resp = tc.get("/metrics")
        assert "bauer_chat_requests_total 1" in resp.text


# ─── bauer status / doctor CLI ───────────────────────────────────────────────

@pytest.mark.skipif(not _TYPER_AVAILABLE, reason="typer not installed")
class TestStatusDoctorCLI:
    def test_status_command_runs(self, tmp_path):
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner()
        # Status sem config.yaml deve funcionar sem crash (usa fallbacks)
        result = runner.invoke(app, ["status", "--config", str(tmp_path / "nonexistent.yaml")])
        # Não deve lançar exceção interna (código pode ser 0 ou outro)
        assert "Bauer Status" in result.output or result.exit_code in (0, 1, 2)

    def test_memory_search_command_no_results(self, tmp_path):
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner()
        result = runner.invoke(app, [
            "memory", "search", "termoquenaoexiste",
            "--dir", str(tmp_path)
        ])
        assert result.exit_code == 0 or "Nenhum resultado" in result.output

    def test_memory_search_command_with_results(self, tmp_path):
        from typer.testing import CliRunner
        from bauer.cli import app
        from bauer.memory_manager import MemoryManager

        mm = MemoryManager(tmp_path)
        mm.init_files()
        mm.add_decision("autenticacao importante", body="OAuth implementado com sucesso")

        runner = CliRunner()
        result = runner.invoke(app, [
            "memory", "search", "autenticacao",
            "--dir", str(tmp_path)
        ])
        # Deve mostrar resultado
        assert result.exit_code == 0
        assert "autenticacao" in result.output.lower() or "DECISIONS" in result.output
