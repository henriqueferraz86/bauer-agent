"""Testes para novos providers e tools expandidos."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_TYPER_AVAILABLE = importlib.util.find_spec("typer") is not None

# ─── Providers — config sections ─────────────────────────────────────────────

class TestNewProviderSections:
    """Verifica que as novas sections existem e têm defaults corretos."""

    def _make_cfg(self, provider: str, tmp_path: Path):
        from bauer.config_loader import BauerConfig
        raw = {
            "model": {"name": "test-model", "provider": provider, "requested_context": 4096}
        }
        return BauerConfig(**raw)

    def test_groq_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("groq", tmp_path)
        assert cfg.groq.api_key == ""
        assert cfg.groq.timeout_seconds == 30

    def test_mistral_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("mistral", tmp_path)
        assert cfg.mistral.api_key == ""
        assert cfg.mistral.timeout_seconds == 60

    def test_xai_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("xai", tmp_path)
        assert cfg.xai.api_key == ""

    def test_together_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("together", tmp_path)
        assert cfg.together.api_key == ""

    def test_deepseek_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("deepseek", tmp_path)
        assert cfg.deepseek.api_key == ""

    def test_anthropic_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("anthropic", tmp_path)
        assert cfg.anthropic.api_key == ""
        assert cfg.anthropic.api_version == "2023-06-01"

    def test_gemini_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("gemini", tmp_path)
        assert cfg.gemini.api_key == ""

    def test_azure_section_defaults(self, tmp_path: Path):
        cfg = self._make_cfg("azure", tmp_path)
        assert cfg.azure.api_key == ""
        assert cfg.azure.api_version == "2024-08-01-preview"
        assert cfg.azure.endpoint == ""

    def test_invalid_provider_rejected(self):
        from bauer.config_loader import BauerConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BauerConfig(**{"model": {"name": "x", "provider": "unknown_provider", "requested_context": 4096}})


# ─── Providers — env_loader ───────────────────────────────────────────────────

class TestEnvLoaderNewProviders:
    def _make_cfg(self):
        from bauer.config_loader import BauerConfig
        return BauerConfig(**{"model": {"name": "m", "provider": "ollama", "requested_context": 4096}})

    def test_groq_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.groq.api_key == "gsk-test"

    def test_mistral_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "mist-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.mistral.api_key == "mist-test"

    def test_xai_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("XAI_API_KEY", "xai-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.xai.api_key == "xai-test"

    def test_anthropic_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.anthropic.api_key == "sk-ant-test"

    def test_gemini_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gem-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.gemini.api_key == "gem-test"

    def test_gemini_google_api_key_fallback(self, monkeypatch):
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "goog-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.gemini.api_key == "goog-test"

    def test_deepseek_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.deepseek.api_key == "ds-test"

    def test_together_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("TOGETHER_API_KEY", "tog-test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.together.api_key == "tog-test"

    def test_azure_key_and_endpoint_from_env(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my.openai.azure.com")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg()
        apply_env_to_config(cfg)
        assert cfg.azure.api_key == "az-key"
        assert cfg.azure.endpoint == "https://my.openai.azure.com"


# ─── _build_client — novos providers ─────────────────────────────────────────

@pytest.mark.skipif(not _TYPER_AVAILABLE, reason="typer not installed")
class TestBuildClientNewProviders:
    """Verifica que _build_client retorna o client correto para cada provider."""

    def _cfg(self, provider: str, **kwargs):
        from bauer.config_loader import BauerConfig
        raw = {"model": {"name": "test-model", "provider": provider, "requested_context": 4096}}
        cfg = BauerConfig(**raw)
        for k, v in kwargs.items():
            section, _, field = k.partition("__")
            setattr(getattr(cfg, section), field, v)
        return cfg

    def _build(self, cfg):
        """Chama _build_client isolando imports de auth (importado via 'from .auth')."""
        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth_cls.return_value.store.load.return_value = None
            from bauer.cli import _build_client
            return _build_client(cfg)

    def test_groq_returns_openai_client(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._cfg("groq", groq__api_key="gsk-test")
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "groq.com" in client.host

    def test_mistral_returns_openai_client(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._cfg("mistral", mistral__api_key="mist-key")
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "mistral.ai" in client.host

    def test_xai_returns_openai_client(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._cfg("xai", xai__api_key="xai-key")
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "x.ai" in client.host

    def test_together_returns_openai_client(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._cfg("together", together__api_key="tog-key")
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "together.xyz" in client.host

    def test_deepseek_returns_openai_client(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._cfg("deepseek", deepseek__api_key="ds-key")
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "deepseek.com" in client.host

    def test_gemini_returns_openai_client(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._cfg("gemini", gemini__api_key="gem-key")
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "googleapis.com" in client.host

    def test_anthropic_returns_anthropic_client(self):
        from bauer.anthropic_client import AnthropicClient
        cfg = self._cfg("anthropic", anthropic__api_key="sk-ant-key")
        client = self._build(cfg)
        assert isinstance(client, AnthropicClient)

    def test_azure_returns_openai_client_with_deployment(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._cfg("azure",
                        azure__api_key="az-key",
                        azure__endpoint="https://myresource.openai.azure.com",
                        azure__deployment="gpt-4o")
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "myresource.openai.azure.com" in client.host
        assert "api-key" in client._headers


# ─── AnthropicClient ─────────────────────────────────────────────────────────

class TestAnthropicClient:
    def test_init_sets_headers(self):
        from bauer.anthropic_client import AnthropicClient
        c = AnthropicClient(api_key="sk-ant-test", api_version="2023-06-01")
        assert c._headers["x-api-key"] == "sk-ant-test"
        assert c._headers["anthropic-version"] == "2023-06-01"

    def test_is_alive_connection_error(self):
        from bauer.anthropic_client import AnthropicClient
        import httpx
        c = AnthropicClient(api_key="key")
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            ok, msg = c.is_alive()
        assert ok is False
        assert "recusada" in msg.lower() or "Conexao" in msg

    def test_has_model_true_when_list_empty(self):
        from bauer.anthropic_client import AnthropicClient
        c = AnthropicClient(api_key="key")
        with patch.object(c, "list_models", return_value=[]):
            assert c.has_model("claude-3-5-sonnet") is True

    def test_chat_stream_filters_system_messages(self):
        """Verifica que mensagens system são extraídas corretamente."""
        from bauer.anthropic_client import AnthropicClient
        import httpx

        c = AnthropicClient(api_key="sk-ant-test")
        streamed_payloads = []

        class FakeStream:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                pass
            def raise_for_status(self):
                pass
            def iter_lines(self):
                yield 'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}'
                yield 'data: {"type":"message_stop"}'

        captured_json: list[dict] = []
        original_stream = httpx.stream

        def mock_stream(method, url, **kwargs):
            captured_json.append(kwargs.get("json", {}))
            return FakeStream()

        with patch("httpx.stream", side_effect=mock_stream):
            messages = [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "Hello"},
            ]
            chunks = list(c.chat_stream("claude-3-5-haiku", messages))

        assert chunks == ["Hi"]
        assert captured_json[0].get("system") == "Be helpful."
        # system não deve aparecer em messages[]
        for msg in captured_json[0].get("messages", []):
            assert msg.get("role") != "system"


# ─── OpenAIClient — api_version (Azure) ──────────────────────────────────────

class TestOpenAIClientApiVersion:
    def test_chat_url_without_api_version(self):
        from bauer.openai_client import OpenAIClient
        c = OpenAIClient(host="https://api.openai.com")
        assert c._chat_url() == "https://api.openai.com/v1/chat/completions"

    def test_chat_url_with_api_version(self):
        from bauer.openai_client import OpenAIClient
        c = OpenAIClient(
            host="https://my.openai.azure.com/openai/deployments/gpt-4o",
            api_version="2024-08-01-preview",
        )
        url = c._chat_url()
        assert "chat/completions" in url
        assert "api-version=2024-08-01-preview" in url
        assert "/v1/" not in url  # Azure não usa /v1/

    def test_chat_url_copilot_no_v1(self):
        """GitHub Copilot: POST /chat/completions (sem /v1/)."""
        from bauer.openai_client import OpenAIClient
        c = OpenAIClient(
            host="https://api.githubcopilot.com",
            chat_path="/chat/completions",
        )
        assert c._chat_url() == "https://api.githubcopilot.com/chat/completions"
        assert "/v1/" not in c._chat_url()

    def test_chat_url_github_models_no_v1(self):
        """GitHub Models: POST /chat/completions (sem /v1/)."""
        from bauer.openai_client import OpenAIClient
        c = OpenAIClient(
            host="https://models.inference.ai.azure.com",
            chat_path="/chat/completions",
        )
        assert c._chat_url() == "https://models.inference.ai.azure.com/chat/completions"

    def test_chat_url_gemini_no_v1(self):
        """Gemini: host ja tem /v1beta/openai, nao adiciona /v1/ extra."""
        from bauer.openai_client import OpenAIClient
        c = OpenAIClient(
            host="https://generativelanguage.googleapis.com/v1beta/openai",
            chat_path="/chat/completions",
        )
        expected = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert c._chat_url() == expected
        assert "/v1/chat" not in c._chat_url()

    def test_chat_path_default_is_v1(self):
        """Default chat_path deve ser /v1/chat/completions para compatibilidade."""
        from bauer.openai_client import OpenAIClient
        c = OpenAIClient(host="https://api.groq.com/openai")
        assert c._chat_url() == "https://api.groq.com/openai/v1/chat/completions"


class TestOpenAIClientChatStreamErrors:
    """Garante que o corpo do erro é lido DENTRO do contexto stream (conexão aberta)."""

    def _make_mock_stream_response(self, status_code: int, body: bytes):
        """Helper: cria um mock de response para httpx.stream context."""
        import io
        from unittest.mock import MagicMock

        resp = MagicMock()
        resp.status_code = status_code
        resp.request = MagicMock()

        # iter_bytes retorna o body em chunks
        resp.iter_bytes.return_value = iter([body])

        # Suporte ao context manager
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_400_error_body_is_read_and_shown(self):
        """HTTP 400: corpo do erro deve aparecer na mensagem (não vazio)."""
        from bauer.openai_client import OpenAIClient, OpenAIClientError
        from unittest.mock import patch, MagicMock

        body_content = b'{"error":{"message":"model not found","type":"invalid_request_error"}}'
        mock_resp = self._make_mock_stream_response(400, body_content)

        c = OpenAIClient(host="https://api.openai.com", api_key="sk-test")
        with patch("httpx.stream", return_value=mock_resp):
            with pytest.raises(OpenAIClientError) as exc_info:
                list(c.chat_stream("gpt-invalid", [{"role": "user", "content": "hi"}]))

        msg = str(exc_info.value)
        assert "400" in msg
        assert "model not found" in msg  # corpo do erro deve aparecer

    def test_401_error_shows_auth_hint(self):
        """HTTP 401: deve mostrar hint de autenticação."""
        from bauer.openai_client import OpenAIClient, OpenAIClientError
        from unittest.mock import patch

        body_content = b'{"error":{"message":"invalid api key"}}'
        mock_resp = self._make_mock_stream_response(401, body_content)

        c = OpenAIClient(host="https://api.openai.com", api_key="sk-bad")
        with patch("httpx.stream", return_value=mock_resp):
            with pytest.raises(OpenAIClientError) as exc_info:
                list(c.chat_stream("gpt-4o", [{"role": "user", "content": "hi"}]))

        msg = str(exc_info.value)
        assert "autenticacao" in msg.lower() or "401" in msg

    def test_429_error_shows_rate_limit_hint(self):
        """HTTP 429: deve mostrar hint de rate limit."""
        from bauer.openai_client import OpenAIClient, OpenAIClientError
        from unittest.mock import patch

        body_content = b'{"error":{"message":"rate limit exceeded"}}'
        mock_resp = self._make_mock_stream_response(429, body_content)

        c = OpenAIClient(host="https://api.openai.com", api_key="sk-test")
        with patch("httpx.stream", return_value=mock_resp):
            with pytest.raises(OpenAIClientError) as exc_info:
                list(c.chat_stream("gpt-4o", [{"role": "user", "content": "hi"}]))

        msg = str(exc_info.value)
        assert "limite" in msg.lower() or "429" in msg

    def test_500_error_shows_server_hint(self):
        """HTTP 500: deve mostrar hint de erro no servidor."""
        from bauer.openai_client import OpenAIClient, OpenAIClientError
        from unittest.mock import patch

        body_content = b"Internal Server Error"
        mock_resp = self._make_mock_stream_response(500, body_content)

        c = OpenAIClient(host="https://api.openai.com", api_key="sk-test")
        with patch("httpx.stream", return_value=mock_resp):
            with pytest.raises(OpenAIClientError) as exc_info:
                list(c.chat_stream("gpt-4o", [{"role": "user", "content": "hi"}]))

        msg = str(exc_info.value)
        assert "servidor" in msg.lower() or "500" in msg

    def test_200_streams_normally(self):
        """HTTP 200: deve fazer yield dos chunks normalmente."""
        from bauer.openai_client import OpenAIClient
        from unittest.mock import patch, MagicMock

        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_lines.return_value = iter(lines)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        c = OpenAIClient(host="https://api.openai.com", api_key="sk-test")
        with patch("httpx.stream", return_value=mock_resp):
            chunks = list(c.chat_stream("gpt-4o", [{"role": "user", "content": "hi"}]))

        assert chunks == ["Hello", " world"]

    def test_400_empty_body_does_not_crash(self):
        """HTTP 400 com body vazio: deve levantar erro sem crash."""
        from bauer.openai_client import OpenAIClient, OpenAIClientError
        from unittest.mock import patch

        mock_resp = self._make_mock_stream_response(400, b"")

        c = OpenAIClient(host="https://api.openai.com", api_key="sk-test")
        with patch("httpx.stream", return_value=mock_resp):
            with pytest.raises(OpenAIClientError) as exc_info:
                list(c.chat_stream("gpt-4o", [{"role": "user", "content": "hi"}]))

        assert "400" in str(exc_info.value)


# ─── ToolRouter — novas tools ─────────────────────────────────────────────────

@pytest.fixture
def router(tmp_path: Path):
    from bauer.tool_router import ToolRouter
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ToolRouter(workspace=ws)


@pytest.fixture
def router_web(tmp_path: Path):
    from bauer.tool_router import ToolRouter
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ToolRouter(workspace=ws, web_enabled=True)


class TestCreateDir:
    def test_creates_nested_dir(self, router, tmp_path):
        router.execute('{"action":"create_dir","args":{"path":"a/b/c"}}')
        assert (tmp_path / "workspace" / "a" / "b" / "c").is_dir()

    def test_requires_path(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router.execute('{"action":"create_dir","args":{}}')


class TestDeleteFile:
    def test_deletes_file_with_confirm(self, router, tmp_path):
        f = tmp_path / "workspace" / "to_del.txt"
        f.write_text("bye")
        result = router.execute('{"action":"delete_file","args":{"path":"to_del.txt","confirm":true}}')
        assert not f.exists()
        assert "removido" in result.lower()

    def test_requires_confirm(self, router, tmp_path):
        from bauer.tool_router import ToolError
        f = tmp_path / "workspace" / "safe.txt"
        f.write_text("safe")
        with pytest.raises(ToolError, match="confirm"):
            router.execute('{"action":"delete_file","args":{"path":"safe.txt"}}')

    def test_rejects_nonexistent(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router.execute('{"action":"delete_file","args":{"path":"ghost.txt","confirm":true}}')

    def test_rejects_directory(self, router, tmp_path):
        from bauer.tool_router import ToolError
        d = tmp_path / "workspace" / "mydir"
        d.mkdir()
        with pytest.raises(ToolError, match="diretorio"):
            router.execute('{"action":"delete_file","args":{"path":"mydir","confirm":true}}')


class TestAppendFile:
    def test_appends_to_existing(self, router, tmp_path):
        f = tmp_path / "workspace" / "log.txt"
        f.write_text("line1\n")
        router.execute('{"action":"append_file","args":{"path":"log.txt","content":"line2\\n"}}')
        assert f.read_text() == "line1\nline2\n"

    def test_creates_file_if_not_exists(self, router, tmp_path):
        router.execute('{"action":"append_file","args":{"path":"new.txt","content":"hello"}}')
        assert (tmp_path / "workspace" / "new.txt").read_text() == "hello"

    def test_requires_content(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router.execute('{"action":"append_file","args":{"path":"f.txt"}}')


class TestMoveFile:
    def test_moves_file(self, router, tmp_path):
        src = tmp_path / "workspace" / "src.txt"
        src.write_text("content")
        router.execute('{"action":"move_file","args":{"src":"src.txt","dst":"dst.txt"}}')
        assert not src.exists()
        assert (tmp_path / "workspace" / "dst.txt").read_text() == "content"

    def test_overwrite_false_raises(self, router, tmp_path):
        from bauer.tool_router import ToolError
        (tmp_path / "workspace" / "a.txt").write_text("a")
        (tmp_path / "workspace" / "b.txt").write_text("b")
        with pytest.raises(ToolError, match="overwrite"):
            router.execute('{"action":"move_file","args":{"src":"a.txt","dst":"b.txt"}}')

    def test_overwrite_true_works(self, router, tmp_path):
        (tmp_path / "workspace" / "a.txt").write_text("a")
        (tmp_path / "workspace" / "b.txt").write_text("b")
        router.execute('{"action":"move_file","args":{"src":"a.txt","dst":"b.txt","overwrite":true}}')
        assert (tmp_path / "workspace" / "b.txt").read_text() == "a"


class TestDiffFiles:
    def test_diff_identical(self, router, tmp_path):
        for name in ("f1.txt", "f2.txt"):
            (tmp_path / "workspace" / name).write_text("same\n")
        result = router.execute('{"action":"diff_files","args":{"path_a":"f1.txt","path_b":"f2.txt"}}')
        assert "identicos" in result.lower()

    def test_diff_different(self, router, tmp_path):
        (tmp_path / "workspace" / "f1.txt").write_text("line1\nline2\n")
        (tmp_path / "workspace" / "f2.txt").write_text("line1\nlineX\n")
        result = router.execute('{"action":"diff_files","args":{"path_a":"f1.txt","path_b":"f2.txt"}}')
        assert "---" in result or "+++" in result

    def test_diff_missing_file_raises(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router.execute('{"action":"diff_files","args":{"path_a":"ghost.txt","path_b":"also_ghost.txt"}}')


class TestGlobFiles:
    def test_finds_by_extension(self, router, tmp_path):
        ws = tmp_path / "workspace"
        (ws / "a.py").write_text("a")
        (ws / "b.py").write_text("b")
        (ws / "c.txt").write_text("c")
        result = router.execute('{"action":"glob_files","args":{"pattern":"*.py"}}')
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    def test_no_matches(self, router):
        result = router.execute('{"action":"glob_files","args":{"pattern":"*.nope"}}')
        assert "Nenhum" in result

    def test_requires_pattern(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router.execute('{"action":"glob_files","args":{}}')


class TestRegexSearch:
    def test_finds_pattern(self, router, tmp_path):
        (tmp_path / "workspace" / "code.py").write_text("def hello():\n    pass\n")
        result = router.execute('{"action":"regex_search","args":{"pattern":"def \\\\w+","path":"code.py"}}')
        assert "hello" in result

    def test_case_insensitive_flag(self, router, tmp_path):
        (tmp_path / "workspace" / "note.txt").write_text("Hello World\n")
        result = router.execute('{"action":"regex_search","args":{"pattern":"hello","path":"note.txt","flags":"i"}}')
        assert "Hello" in result

    def test_invalid_regex_raises(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="Regex"):
            router.execute('{"action":"regex_search","args":{"pattern":"[invalid"}}')


class TestCalculate:
    def test_basic_arithmetic(self, router):
        result = router.execute('{"action":"calculate","args":{"expression":"2 + 3 * 4"}}')
        assert "14" in result

    def test_power(self, router):
        result = router.execute('{"action":"calculate","args":{"expression":"2 ** 10"}}')
        assert "1024" in result

    def test_sqrt(self, router):
        result = router.execute('{"action":"calculate","args":{"expression":"sqrt(144)"}}')
        assert "12" in result

    def test_division_by_zero(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="zero"):
            router.execute('{"action":"calculate","args":{"expression":"1 / 0"}}')

    def test_unsafe_expression_blocked(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router.execute('{"action":"calculate","args":{"expression":"__import__(\'os\')"}}')

    def test_requires_expression(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router.execute('{"action":"calculate","args":{}}')


class TestDatetimeNow:
    def test_iso_format(self, router):
        result = router.execute('{"action":"datetime_now","args":{"format":"iso"}}')
        assert "T" in result  # ISO format contains T separator

    def test_br_format(self, router):
        result = router.execute('{"action":"datetime_now","args":{"format":"br"}}')
        assert "/" in result

    def test_timestamp_format(self, router):
        result = router.execute('{"action":"datetime_now","args":{"format":"timestamp"}}')
        assert result.strip().isdigit()

    def test_unknown_format_defaults_to_iso(self, router):
        result = router.execute('{"action":"datetime_now","args":{"format":"unknown"}}')
        assert "T" in result


class TestJsonQuery:
    def test_simple_key_access(self, router):
        data = '{"name":"Alice","age":30}'
        result = router.execute({"action": "json_query", "args": {"data": data, "query": ".name"}})
        assert "Alice" in result

    def test_nested_access(self, router):
        data = '{"user":{"city":"Sao Paulo"}}'
        payload = {"action": "json_query", "args": {"data": data, "query": ".user.city"}}
        result = router.execute(payload)
        assert "Sao Paulo" in result

    def test_array_index(self, router):
        data = '{"items":["a","b","c"]}'
        payload = {"action": "json_query", "args": {"data": data, "query": ".items[1]"}}
        result = router.execute(payload)
        assert "b" in result

    def test_missing_key_raises(self, router):
        from bauer.tool_router import ToolError
        data = '{"x":1}'
        with pytest.raises(ToolError, match="nao encontrada"):
            router.execute({"action": "json_query", "args": {"data": data, "query": ".missing"}})

    def test_from_file(self, router, tmp_path):
        f = tmp_path / "workspace" / "data.json"
        f.write_text('{"key":"value"}')
        result = router.execute({"action": "json_query", "args": {"data": "data.json", "query": ".key"}})
        assert "value" in result

    def test_invalid_json_raises(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="JSON"):
            router.execute({"action": "json_query", "args": {"data": "not json!", "query": ".x"}})


class TestEncodeDecode:
    def test_base64_encode(self, router):
        result = router.execute('{"action":"encode_decode","args":{"input":"hello","operation":"base64_encode"}}')
        import base64
        assert result == base64.b64encode(b"hello").decode()

    def test_base64_decode(self, router):
        import base64
        encoded = base64.b64encode(b"world").decode()
        result = router.execute({"action": "encode_decode", "args": {"input": encoded, "operation": "base64_decode"}})
        assert result == "world"

    def test_url_encode(self, router):
        result = router.execute('{"action":"encode_decode","args":{"input":"hello world","operation":"url_encode"}}')
        assert "%20" in result or "+" in result

    def test_url_decode(self, router):
        result = router.execute('{"action":"encode_decode","args":{"input":"hello%20world","operation":"url_decode"}}')
        assert result == "hello world"

    def test_hex_encode(self, router):
        result = router.execute('{"action":"encode_decode","args":{"input":"hi","operation":"hex_encode"}}')
        assert result == "6869"

    def test_hex_decode(self, router):
        result = router.execute('{"action":"encode_decode","args":{"input":"6869","operation":"hex_decode"}}')
        assert result == "hi"

    def test_unknown_operation_raises(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="nao reconhecida"):
            router.execute('{"action":"encode_decode","args":{"input":"x","operation":"invalid_op"}}')


class TestHttpRequest:
    def test_requires_url(self, router_web):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router_web.execute('{"action":"http_request","args":{}}')

    def test_blocks_localhost(self, router_web):
        from bauer.tool_router import ToolError
        # Blocked by Wave 4.5 url_safety (SSRF) or legacy blocklist.
        with pytest.raises(ToolError, match=r"(?i)(interno|BLOCKED|SSRF|loopback|private|blocked)"):
            router_web.execute('{"action":"http_request","args":{"url":"http://localhost:8080/api"}}')

    def test_blocks_private_ip(self, router_web):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError):
            router_web.execute('{"action":"http_request","args":{"url":"http://192.168.1.1/admin"}}')

    def test_unsupported_method_raises(self, router_web):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="nao suportado"):
            router_web.execute('{"action":"http_request","args":{"url":"https://example.com","method":"TRACE"}}')

    def test_invalid_url_scheme(self, router_web):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="http"):
            router_web.execute('{"action":"http_request","args":{"url":"ftp://example.com"}}')

    def test_get_request_mocked(self, router_web):
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.reason_phrase = "OK"
        mock_resp.headers = {"content-type": "application/json", "content-length": "20"}
        mock_resp.json.return_value = {"status": "ok"}
        mock_resp.text = '{"status":"ok"}'

        with patch("httpx.request", return_value=mock_resp):
            result = router_web.execute('{"action":"http_request","args":{"url":"https://api.example.com/status"}}')

        assert "200" in result
        assert "status" in result

    def test_not_available_without_web_enabled(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="desconhecida"):
            router.execute('{"action":"http_request","args":{"url":"https://example.com"}}')


# ─── GitHub providers ────────────────────────────────────────────────────────

@pytest.mark.skipif(not _TYPER_AVAILABLE, reason="typer not installed")
class TestGithubProviders:
    def _make_cfg(self, provider: str):
        from bauer.config_loader import BauerConfig
        return BauerConfig(**{"model": {"name": "gpt-4o", "provider": provider, "requested_context": 4096}})

    def _build(self, cfg):
        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth_cls.return_value.store.load.return_value = None
            from bauer.cli import _build_client
            return _build_client(cfg)

    def test_github_section_defaults(self):
        cfg = self._make_cfg("github")
        assert cfg.github.token == ""
        assert cfg.github.timeout_seconds == 60

    def test_copilot_section_defaults(self):
        cfg = self._make_cfg("copilot")
        assert cfg.copilot.token == ""
        assert cfg.copilot.timeout_seconds == 60

    def test_github_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg("github")
        apply_env_to_config(cfg)
        assert cfg.github.token == "ghp_test123"

    def test_copilot_token_from_env(self, monkeypatch):
        monkeypatch.setenv("COPILOT_TOKEN", "tid_copilot_test")
        from bauer.env_loader import apply_env_to_config
        cfg = self._make_cfg("copilot")
        apply_env_to_config(cfg)
        assert cfg.copilot.token == "tid_copilot_test"

    def test_github_build_client_uses_inference_endpoint(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._make_cfg("github")
        cfg.github.token = "ghp_mytoken"
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "models.inference.ai.azure.com" in client.host
        assert "Bearer ghp_mytoken" in client._headers.get("Authorization", "")

    def test_copilot_build_client_uses_copilot_endpoint(self):
        from bauer.openai_client import OpenAIClient
        cfg = self._make_cfg("copilot")
        cfg.copilot.token = "tid_test"
        client = self._build(cfg)
        assert isinstance(client, OpenAIClient)
        assert "api.githubcopilot.com" in client.host
        # Deve ter headers de identificação do VS Code
        assert "Copilot-Integration-Id" in client._headers
        assert client._headers["Copilot-Integration-Id"] == "vscode-chat"

    def test_github_invalid_provider_still_rejected(self):
        from bauer.config_loader import BauerConfig
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BauerConfig(**{"model": {"name": "x", "provider": "githubcopilot", "requested_context": 4096}})


# ─── GitHub Device Flow + Copilot auth ────────────────────────────────────────

class TestCopilotDeviceFlowAuth:
    """Tests for login_device_flow, _exchange_copilot_token, refresh_copilot_token."""

    def _make_auth(self, tmp_path: Path):
        from bauer.auth import AuthManager
        return AuthManager(base_dir=tmp_path / ".bauer")

    def _device_code_resp(self, user_code: str = "ABCD-1234") -> MagicMock:
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json.return_value = {
            "device_code": "dev-xyz",
            "user_code": user_code,
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }
        return m

    def _poll_success_resp(self, token: str = "ghp_test") -> MagicMock:
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json.return_value = {"access_token": token}
        return m

    def _poll_error_resp(self, error: str) -> MagicMock:
        m = MagicMock()
        m.raise_for_status = MagicMock()
        m.json.return_value = {"error": error}
        return m

    def _copilot_session_resp(
        self, session_token: str = "tid_sess", expires_at: float = 9_999_999_999.0
    ) -> MagicMock:
        m = MagicMock()
        m.status_code = 200
        m.raise_for_status = MagicMock()
        m.json.return_value = {"token": session_token, "expires_at": expires_at}
        return m

    # ── login_device_flow — happy paths ─────────────────────────────────────

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_github_device_flow_success(self, mock_sleep, mock_wb, tmp_path: Path):
        """GitHub Models: device flow stores PAT directly as api_key."""
        from bauer.auth import AuthToken

        auth = self._make_auth(tmp_path)
        with patch("httpx.post", side_effect=[self._device_code_resp(), self._poll_success_resp("ghp_mypat")]), \
             patch("rich.console.Console"):
            token = auth.login_device_flow("github")

        assert isinstance(token, AuthToken)
        assert token.provider == "github"
        assert token.access_token == "ghp_mypat"
        assert token.api_key == "ghp_mypat"
        assert token.api_base is not None
        assert "azure.com" in token.api_base  # models.inference.ai.azure.com

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_copilot_device_flow_success(self, mock_sleep, mock_wb, tmp_path: Path):
        """Copilot: device flow → exchange session token → stores github_token in extra."""
        auth = self._make_auth(tmp_path)
        cop_resp = self._copilot_session_resp("tid_abc", expires_at=8_888_888_888.0)

        with patch("httpx.post", side_effect=[self._device_code_resp(), self._poll_success_resp("ghp_oauth_tok")]), \
             patch("httpx.get", return_value=cop_resp), \
             patch("rich.console.Console"):
            token = auth.login_device_flow("copilot")

        assert token.provider == "copilot"
        assert token.access_token == "tid_abc"
        assert token.api_key == "tid_abc"
        assert token.expires_at == 8_888_888_888.0
        assert token.extra["github_token"] == "ghp_oauth_tok"
        assert "copilot_token_url" in token.extra

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_device_flow_pending_then_success(self, mock_sleep, mock_wb, tmp_path: Path):
        """authorization_pending is retried silently until success."""
        auth = self._make_auth(tmp_path)
        pending1 = self._poll_error_resp("authorization_pending")
        pending2 = self._poll_error_resp("authorization_pending")
        success  = self._poll_success_resp("ghp_delayed")

        with patch("httpx.post", side_effect=[self._device_code_resp(), pending1, pending2, success]), \
             patch("rich.console.Console"):
            token = auth.login_device_flow("github")

        assert token.access_token == "ghp_delayed"

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_device_flow_slow_down_then_success(self, mock_sleep, mock_wb, tmp_path: Path):
        """slow_down error is handled, polling continues and eventually succeeds."""
        auth = self._make_auth(tmp_path)
        slow    = self._poll_error_resp("slow_down")
        success = self._poll_success_resp("ghp_slow")

        with patch("httpx.post", side_effect=[self._device_code_resp(), slow, success]), \
             patch("rich.console.Console"):
            token = auth.login_device_flow("github")

        assert token.access_token == "ghp_slow"
        # sleep must have been called at least twice (initial interval + slow_down increased)
        assert mock_sleep.call_count >= 2

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_device_flow_saves_token_to_store(self, mock_sleep, mock_wb, tmp_path: Path):
        """Successful device flow persists token in TokenStore."""
        auth = self._make_auth(tmp_path)

        with patch("httpx.post", side_effect=[self._device_code_resp(), self._poll_success_resp("ghp_persisted")]), \
             patch("rich.console.Console"):
            auth.login_device_flow("github")

        stored = auth.store.load("github")
        assert stored is not None
        assert stored.access_token == "ghp_persisted"

    # ── login_device_flow — error paths ──────────────────────────────────────

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_device_flow_expired_token_raises_timeout(self, mock_sleep, mock_wb, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        with patch("httpx.post", side_effect=[self._device_code_resp(), self._poll_error_resp("expired_token")]), \
             patch("rich.console.Console"), \
             pytest.raises(TimeoutError):
            auth.login_device_flow("github")

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_device_flow_access_denied_raises_permission(self, mock_sleep, mock_wb, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        with patch("httpx.post", side_effect=[self._device_code_resp(), self._poll_error_resp("access_denied")]), \
             patch("rich.console.Console"), \
             pytest.raises(PermissionError):
            auth.login_device_flow("github")

    @patch("webbrowser.open")
    @patch("time.sleep")
    def test_device_flow_unknown_error_raises_runtime(self, mock_sleep, mock_wb, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        err_resp = self._poll_error_resp("some_unknown_error")
        err_resp.json.return_value["error_description"] = "something went wrong"

        with patch("httpx.post", side_effect=[self._device_code_resp(), err_resp]), \
             patch("rich.console.Console"), \
             pytest.raises(RuntimeError, match="some_unknown_error"):
            auth.login_device_flow("github")

    def test_device_flow_invalid_provider_raises_value_error(self, tmp_path: Path):
        """Non-device-flow provider raises ValueError."""
        auth = self._make_auth(tmp_path)
        with pytest.raises(ValueError, match="Device Flow"):
            auth.login_device_flow("openai-api")

    def test_device_flow_oauth_provider_raises_value_error(self, tmp_path: Path):
        """'openai' uses OAuth, not Device Flow — must raise."""
        auth = self._make_auth(tmp_path)
        with pytest.raises(ValueError):
            auth.login_device_flow("openai")

    # ── _exchange_copilot_token ────────────────────────────────────────────

    def test_exchange_copilot_token_success(self, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"token": "sess_xyz", "expires_at": 1_999_999_999}

        with patch("httpx.get", return_value=mock_resp):
            data = auth._exchange_copilot_token(
                "ghp_github_tok",
                "https://api.github.com/copilot_internal/v2/token",
            )

        assert data["token"] == "sess_xyz"
        assert data["expires_at"] == 1_999_999_999

    def test_exchange_copilot_token_sends_required_headers(self, tmp_path: Path):
        """Request must carry Authorization, Editor-Version, Copilot-Integration-Id."""
        auth = self._make_auth(tmp_path)
        captured: list[dict] = []

        def fake_get(url, **kwargs):
            captured.append(kwargs)
            m = MagicMock()
            m.status_code = 200
            m.raise_for_status = MagicMock()
            m.json.return_value = {"token": "tok", "expires_at": 9999}
            return m

        with patch("httpx.get", side_effect=fake_get):
            auth._exchange_copilot_token(
                "ghp_hdr_test",
                "https://api.github.com/copilot_internal/v2/token",
            )

        headers = captured[0]["headers"]
        assert headers["Authorization"] == "token ghp_hdr_test"
        assert "Editor-Version" in headers
        assert "Copilot-Integration-Id" in headers

    def test_exchange_copilot_token_401_raises_permission_error(self, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.raise_for_status = MagicMock()  # not reached

        with patch("httpx.get", return_value=mock_resp), \
             pytest.raises(PermissionError, match="Copilot"):
            auth._exchange_copilot_token(
                "bad_token",
                "https://api.github.com/copilot_internal/v2/token",
            )

    def test_exchange_copilot_token_missing_token_field_raises_runtime(self, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"message": "unexpected"}

        with patch("httpx.get", return_value=mock_resp), \
             pytest.raises(RuntimeError, match="inesperada"):
            auth._exchange_copilot_token(
                "ghp_ok",
                "https://api.github.com/copilot_internal/v2/token",
            )

    # ── refresh_copilot_token ─────────────────────────────────────────────

    def test_refresh_copilot_token_success(self, tmp_path: Path):
        import time as _time
        from bauer.auth import AuthManager, AuthToken

        auth = AuthManager(base_dir=tmp_path / ".bauer")
        new_expiry = _time.time() + 1740

        old_token = AuthToken(
            provider="copilot",
            access_token="old_sess",
            api_key="old_sess",
            expires_at=_time.time() - 1,
            api_base="https://api.githubcopilot.com",
            extra={
                "github_token": "ghp_for_refresh",
                "copilot_token_url": "https://api.github.com/copilot_internal/v2/token",
            },
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"token": "new_sess_token", "expires_at": new_expiry}

        with patch("httpx.get", return_value=mock_resp):
            refreshed = auth.refresh_copilot_token(old_token)

        assert refreshed is not None
        assert refreshed.access_token == "new_sess_token"
        assert refreshed.api_key == "new_sess_token"
        assert refreshed.expires_at == new_expiry
        assert refreshed.extra["github_token"] == "ghp_for_refresh"
        assert refreshed.provider == "copilot"

    def test_refresh_copilot_token_preserves_extra(self, tmp_path: Path):
        """refresh keeps the original extra dict (for further refreshes)."""
        import time as _time
        from bauer.auth import AuthManager, AuthToken

        auth = AuthManager(base_dir=tmp_path / ".bauer")
        old_token = AuthToken(
            provider="copilot",
            access_token="old",
            api_key="old",
            expires_at=_time.time() - 1,
            extra={
                "github_token": "ghp_preserve",
                "copilot_token_url": "https://api.github.com/copilot_internal/v2/token",
            },
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"token": "new_tok", "expires_at": _time.time() + 1740}

        with patch("httpx.get", return_value=mock_resp):
            refreshed = auth.refresh_copilot_token(old_token)

        assert refreshed.extra["github_token"] == "ghp_preserve"
        assert refreshed.extra["copilot_token_url"] == "https://api.github.com/copilot_internal/v2/token"

    def test_refresh_copilot_token_no_github_token_returns_none(self, tmp_path: Path):
        import time as _time
        from bauer.auth import AuthManager, AuthToken

        auth = AuthManager(base_dir=tmp_path / ".bauer")
        token = AuthToken(
            provider="copilot",
            access_token="sess",
            expires_at=_time.time() - 1,
            extra={},  # no github_token
        )

        result = auth.refresh_copilot_token(token)
        assert result is None

    def test_refresh_copilot_token_network_error_returns_none(self, tmp_path: Path):
        import time as _time
        from bauer.auth import AuthManager, AuthToken

        auth = AuthManager(base_dir=tmp_path / ".bauer")
        token = AuthToken(
            provider="copilot",
            access_token="sess",
            expires_at=_time.time() - 1,
            extra={"github_token": "ghp_valid"},
        )

        with patch("httpx.get", side_effect=Exception("connection error")):
            result = auth.refresh_copilot_token(token)

        assert result is None

    def test_refresh_copilot_token_saves_to_store(self, tmp_path: Path):
        """refresh_copilot_token persists the new token so next load() gets fresh value."""
        import time as _time
        from bauer.auth import AuthManager, AuthToken

        auth = AuthManager(base_dir=tmp_path / ".bauer")
        old_token = AuthToken(
            provider="copilot",
            access_token="old",
            api_key="old",
            expires_at=_time.time() - 1,
            api_base="https://api.githubcopilot.com",
            extra={"github_token": "ghp_save_test"},
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"token": "saved_new_tok", "expires_at": _time.time() + 1740}

        with patch("httpx.get", return_value=mock_resp):
            auth.refresh_copilot_token(old_token)

        stored = auth.store.load("copilot")
        assert stored is not None
        assert stored.access_token == "saved_new_tok"

    # ── providers catalog ─────────────────────────────────────────────────

    def test_github_in_providers_catalog(self):
        from bauer.auth import PROVIDERS
        assert "github" in PROVIDERS
        assert PROVIDERS["github"]["auth_type"] == "device_flow"
        assert "device_code_url" in PROVIDERS["github"]
        assert "client_id" in PROVIDERS["github"]

    def test_copilot_in_providers_catalog(self):
        from bauer.auth import PROVIDERS
        assert "copilot" in PROVIDERS
        assert PROVIDERS["copilot"]["auth_type"] == "device_flow"
        assert "copilot_token_url" in PROVIDERS["copilot"]
        assert "client_id" in PROVIDERS["copilot"]

    def test_login_interactive_routes_github_to_device_flow(self, tmp_path: Path, monkeypatch):
        """login_interactive('github') delegates to login_device_flow."""
        auth = self._make_auth(tmp_path)
        called_with: list[str] = []

        def fake_device_flow(provider: str):
            called_with.append(provider)
            from bauer.auth import AuthToken
            return AuthToken(provider=provider, access_token="tok")

        monkeypatch.setattr(auth, "login_device_flow", fake_device_flow)
        auth.login_interactive("github")

        assert called_with == ["github"]

    def test_login_interactive_routes_copilot_to_device_flow(self, tmp_path: Path, monkeypatch):
        """login_interactive('copilot') delegates to login_device_flow."""
        auth = self._make_auth(tmp_path)
        called_with: list[str] = []

        def fake_device_flow(provider: str):
            called_with.append(provider)
            from bauer.auth import AuthToken
            return AuthToken(provider=provider, access_token="tok")

        monkeypatch.setattr(auth, "login_device_flow", fake_device_flow)
        auth.login_interactive("copilot")

        assert called_with == ["copilot"]


# ─── ALL_TOOLS catalog ────────────────────────────────────────────────────────

class TestAllToolsCatalog:
    def test_minimum_18_tools(self):
        from bauer.agent_registry import ALL_TOOLS
        assert len(ALL_TOOLS) >= 18

    def test_new_tools_in_all_tools(self):
        from bauer.agent_registry import ALL_TOOLS
        new_tools = [
            "create_dir", "delete_file", "append_file", "move_file", "diff_files",
            "glob_files", "regex_search",
            "calculate", "datetime_now", "json_query", "encode_decode",
            "http_request",
        ]
        for tool in new_tools:
            assert tool in ALL_TOOLS, f"Tool '{tool}' nao encontrada em ALL_TOOLS"

    def test_default_tools_subset_of_all(self):
        from bauer.agent_registry import ALL_TOOLS, DEFAULT_TOOLS
        for tool in DEFAULT_TOOLS:
            assert tool in ALL_TOOLS, f"DEFAULT tool '{tool}' nao esta em ALL_TOOLS"
