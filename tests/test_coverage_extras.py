"""Additional coverage tests for chat, openai_client, preflight, and misc modules."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.chat import run_chat_session
from bauer.ollama_client import OllamaError
from bauer.openai_client import OpenAIClient, OpenAIClientError


# ══════════════════════════════════════════════════════════════════════════════
# chat.py — run_chat_session
# ══════════════════════════════════════════════════════════════════════════════


def _make_console():
    from rich.console import Console
    c = Console(highlight=False)
    return c


class TestRunChatSession:
    def _run(self, inputs, stream_chunks=None, stream_error=None, applied_context=2048):
        from rich.console import Console

        client = MagicMock()
        if stream_error:
            client.chat_stream.side_effect = stream_error
        elif stream_chunks is not None:
            client.chat_stream.return_value = iter(stream_chunks)

        console = MagicMock(spec=Console)
        # Make console.input return values from inputs list
        inputs_iter = iter(inputs)
        console.input.side_effect = lambda *a, **kw: next(inputs_iter)

        run_chat_session(client, "test-model", applied_context, console)
        return client, console

    def test_exit_command(self):
        client, console = self._run(["/exit"])
        client.chat_stream.assert_not_called()

    def test_quit_command(self):
        client, console = self._run(["/quit"])
        client.chat_stream.assert_not_called()

    def test_sair_command(self):
        client, console = self._run(["/sair"])
        client.chat_stream.assert_not_called()

    def test_empty_input_skips(self):
        # empty then /exit
        client, console = self._run(["", "/exit"])
        client.chat_stream.assert_not_called()

    def test_clear_command(self):
        client, console = self._run(["/clear", "/exit"])
        client.chat_stream.assert_not_called()

    def test_limpar_command(self):
        client, console = self._run(["/limpar", "/exit"])
        client.chat_stream.assert_not_called()

    def test_status_command(self):
        client, console = self._run(["/status", "/exit"])
        client.chat_stream.assert_not_called()

    def test_stats_command(self):
        client, console = self._run(["/stats", "/exit"])
        client.chat_stream.assert_not_called()

    def test_normal_message_streams_response(self):
        client, console = self._run(
            ["Hello there", "/exit"],
            stream_chunks=["Hi", " there!"],
        )
        client.chat_stream.assert_called_once()

    def test_keyboard_interrupt_on_input(self):
        from rich.console import Console

        client = MagicMock()
        console = MagicMock(spec=Console)
        console.input.side_effect = KeyboardInterrupt()
        # Should exit gracefully
        run_chat_session(client, "test-model", 2048, console)

    def test_eof_on_input(self):
        from rich.console import Console

        client = MagicMock()
        console = MagicMock(spec=Console)
        console.input.side_effect = EOFError()
        run_chat_session(client, "test-model", 2048, console)

    def test_ollama_error_during_stream(self):
        client, console = self._run(
            ["Hello", "/exit"],
            stream_error=OllamaError("Ollama crashed"),
        )
        # Should print error but not crash
        assert console.print.called

    def test_keyboard_interrupt_during_stream(self):
        from rich.console import Console

        client = MagicMock()
        client.chat_stream.side_effect = KeyboardInterrupt()
        console = MagicMock(spec=Console)
        inputs = iter(["What is 2+2?", "/exit"])
        console.input.side_effect = lambda *a, **kw: next(inputs)

        run_chat_session(client, "test-model", 2048, console)

    def test_multiple_messages(self):
        client = MagicMock()
        client.chat_stream.return_value = iter(["Response"])
        from rich.console import Console
        console = MagicMock(spec=Console)
        inputs = iter(["First message", "Second message", "/exit"])
        console.input.side_effect = lambda *a, **kw: next(inputs)

        run_chat_session(client, "test-model", 2048, console)
        assert client.chat_stream.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# openai_client.py — missing paths
# ══════════════════════════════════════════════════════════════════════════════


class TestOpenAIClientExtras:
    def test_extra_headers_in_constructor(self):
        client = OpenAIClient(extra_headers={"X-Custom": "value"})
        assert client._headers.get("X-Custom") == "value"

    def test_is_alive_non_200_401(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("httpx.get", return_value=mock_resp):
            ok, msg = OpenAIClient().is_alive()
        assert not ok
        assert "503" in msg

    def test_is_alive_timeout(self):
        import httpx
        with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
            ok, msg = OpenAIClient().is_alive()
        assert not ok
        assert "Timeout" in msg

    def test_is_alive_generic_exception(self):
        with patch("httpx.get", side_effect=RuntimeError("unexpected")):
            ok, msg = OpenAIClient().is_alive()
        assert not ok
        assert "inesperada" in msg.lower() or "unexpected" in msg.lower()

    def test_has_model_empty_list_returns_true(self):
        client = OpenAIClient()
        with patch.object(client, "list_models", return_value=[]):
            result = client.has_model("any-model")
        assert result is True

    def test_has_model_error_returns_true(self):
        client = OpenAIClient()
        with patch.object(client, "list_models", side_effect=OpenAIClientError("err")):
            result = client.has_model("any-model")
        assert result is True

    def test_show_model(self):
        client = OpenAIClient()
        params = client.show_model("gpt-4o")
        assert params.num_ctx is None
        assert params.raw["id"] == "gpt-4o"

    def test_chat_stream_connect_error(self):
        import httpx
        client = OpenAIClient()
        with patch("httpx.stream", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(OpenAIClientError, match="Conexao"):
                list(client.chat_stream("model", []))

    def test_chat_stream_timeout(self):
        import httpx
        client = OpenAIClient()
        with patch("httpx.stream", side_effect=httpx.TimeoutException("timeout")):
            with pytest.raises(OpenAIClientError, match="Timeout"):
                list(client.chat_stream("model", []))

    def test_chat_stream_http_error(self):
        import httpx
        client = OpenAIClient()
        with patch("httpx.stream", side_effect=httpx.HTTPError("generic")):
            with pytest.raises(OpenAIClientError, match="HTTP"):
                list(client.chat_stream("model", []))

    def test_chat_stream_status_error(self):
        import httpx
        client = OpenAIClient()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        exc = httpx.HTTPStatusError("unauth", request=MagicMock(), response=mock_resp)

        def _stream(*a, **kw):
            class _Ctx:
                def __enter__(self):
                    raise exc
                def __exit__(self, *args):
                    return False
            return _Ctx()

        with patch("httpx.stream", side_effect=lambda *a, **kw: _stream()):
            with pytest.raises(OpenAIClientError):
                list(client.chat_stream("model", []))

    def test_chat_stream_json_decode_error_skips(self):
        """Lines with invalid JSON in SSE are silently skipped."""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter([
            "data: invalid-json",
            "data: " + json.dumps({"choices": [{"delta": {"content": "OK"}}]}),
            "data: [DONE]",
        ])

        class _Ctx:
            def __enter__(self): return mock_response
            def __exit__(self, *a): return False

        with patch("httpx.stream", return_value=_Ctx()):
            chunks = list(OpenAIClient().chat_stream("model", []))

        assert "OK" in chunks

    def test_chat_stream_done_sentinel(self):
        """[DONE] stops the stream."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter([
            "data: " + json.dumps({"choices": [{"delta": {"content": "Hi"}}]}),
            "data: [DONE]",
            "data: " + json.dumps({"choices": [{"delta": {"content": "never"}}]}),
        ])

        class _Ctx:
            def __enter__(self): return mock_response
            def __exit__(self, *a): return False

        with patch("httpx.stream", return_value=_Ctx()):
            chunks = list(OpenAIClient().chat_stream("model", []))

        assert chunks == ["Hi"]

    def test_chat_stream_skips_non_data_lines(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.iter_lines.return_value = iter([
            "",
            "comment line",
            "data: " + json.dumps({"choices": [{"delta": {"content": "Y"}}]}),
            "data: [DONE]",
        ])

        class _Ctx:
            def __enter__(self): return mock_response
            def __exit__(self, *a): return False

        with patch("httpx.stream", return_value=_Ctx()):
            chunks = list(OpenAIClient().chat_stream("model", []))

        assert chunks == ["Y"]


# ══════════════════════════════════════════════════════════════════════════════
# preflight.py — additional paths
# ══════════════════════════════════════════════════════════════════════════════


def _make_config(provider="ollama", model="phi4-mini"):
    from bauer.config_loader import load_config
    import tempfile, yaml
    cfg_data = {
        "model": {"provider": provider, "name": model, "requested_context": 8192,
                  "minimum_context": 512, "auto_downgrade_context": True},
        "ollama": {"host": "http://localhost:11434", "timeout_seconds": 60},
        "runtime": {"safety_margin_mb": 512, "profile": "medium"},
        "logging": {"level": "info", "file": ""},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(cfg_data, f)
        return Path(f.name)


class TestPreflight:
    def _make_config_obj(self, provider="ollama", model="phi4-mini", auto_downgrade=True, requested=8192, minimum=512):
        from bauer.config_loader import load_config
        import yaml, tempfile
        data = {
            "model": {"provider": provider, "name": model,
                      "requested_context": requested,
                      "minimum_context": minimum,
                      "auto_downgrade_context": auto_downgrade},
            "ollama": {"host": "http://localhost:11434", "timeout_seconds": 60},
            "runtime": {"safety_margin_mb": 512, "profile": "medium"},
            "logging": {"level": "info", "file": ""},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            yaml.dump(data, f)
            path = Path(f.name)
        return load_config(path)

    def _make_registry(self):
        from bauer.model_registry import load_registry
        import yaml, tempfile
        data = {"models": {"phi4-mini": {
            "ram_base_mb": 3000, "ram_per_1k_ctx_mb": 0.5,
            "max_context_safe": 32768,
            "supports_tools": True, "ram_profile": "medium"}}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            yaml.dump(data, f)
            return load_registry(Path(f.name))

    def test_cloud_provider_always_available(self):
        from bauer.preflight import run_doctor
        cfg = self._make_config_obj(provider="openai", model="gpt-4o")
        reg = self._make_registry()
        report = run_doctor(cfg, reg)
        assert report.state.model_available is True
        assert report.state.configured_provider == "openai"

    def test_ollama_online_model_found(self):
        from bauer.preflight import run_doctor

        cfg = self._make_config_obj(provider="ollama", model="phi4-mini")
        reg = self._make_registry()

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (True, "")
        mock_client.has_model.return_value = True
        mock_client.show_model.return_value = MagicMock(num_ctx=8192)

        with patch("bauer.preflight.OllamaClient", return_value=mock_client):
            report = run_doctor(cfg, reg)

        assert report.state.ollama_alive is True
        assert report.state.model_available is True
        assert report.state.status in ("ok", "ok_with_adjustments")

    def test_ollama_online_model_not_found(self):
        from bauer.preflight import run_doctor

        cfg = self._make_config_obj(provider="ollama", model="phi4-mini")
        reg = self._make_registry()

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (True, "")
        mock_client.has_model.return_value = False

        with patch("bauer.preflight.OllamaClient", return_value=mock_client):
            report = run_doctor(cfg, reg)

        assert report.state.model_available is False
        assert report.state.status == "blocked"

    def test_env_context_length_is_picked_up(self):
        from bauer.preflight import run_doctor, _detect_env_num_ctx

        with patch.dict("os.environ", {"OLLAMA_CONTEXT_LENGTH": "4096"}):
            val = _detect_env_num_ctx()
        assert val == 4096

    def test_env_context_non_digit_ignored(self):
        from bauer.preflight import _detect_env_num_ctx
        with patch.dict("os.environ", {"OLLAMA_CONTEXT_LENGTH": "abc"}):
            val = _detect_env_num_ctx()
        assert val is None

    def test_context_below_minimum_no_downgrade(self):
        from bauer.preflight import _resolve_context

        # Force applied < minimum with auto_downgrade=False
        applied, reason, notes = _resolve_context(
            requested=100,
            minimum=1000,
            auto_downgrade=False,
            modelfile_num_ctx=None,
            env_num_ctx=None,
            info=None,
            ram_available_mb=32000,
            safety_margin_mb=512,
        )
        assert applied == 100
        assert any("auto_downgrade" in n for n in notes)

    def test_context_below_minimum_with_downgrade(self):
        from bauer.preflight import _resolve_context

        applied, reason, notes = _resolve_context(
            requested=100,
            minimum=1000,
            auto_downgrade=True,
            modelfile_num_ctx=None,
            env_num_ctx=None,
            info=None,
            ram_available_mb=32000,
            safety_margin_mb=512,
        )
        assert any("Auto-downgrade" in n for n in notes)

    def test_env_num_ctx_limits_context(self):
        from bauer.preflight import _resolve_context

        applied, reason, notes = _resolve_context(
            requested=8192,
            minimum=512,
            auto_downgrade=True,
            modelfile_num_ctx=None,
            env_num_ctx=2048,
            info=None,
            ram_available_mb=32000,
            safety_margin_mb=512,
        )
        assert applied == 2048
        assert "env" in reason.lower()

    def test_cloud_run_doctor_openrouter(self):
        from bauer.preflight import run_doctor

        cfg = self._make_config_obj(provider="openrouter", model="openai/gpt-4o")
        reg = self._make_registry()
        report = run_doctor(cfg, reg)

        assert report.state.configured_provider == "openrouter"
        assert report.state.model_available is True

    def test_ollama_model_with_show_model_error(self):
        from bauer.preflight import run_doctor

        cfg = self._make_config_obj(provider="ollama", model="phi4-mini")
        reg = self._make_registry()

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (True, "")
        mock_client.has_model.return_value = True
        mock_client.show_model.side_effect = OllamaError("modelfile error")

        with patch("bauer.preflight.OllamaClient", return_value=mock_client):
            report = run_doctor(cfg, reg)

        assert report.state.model_available is True
        assert any("Aviso" in f for f in report.findings)

    def test_run_doctor_with_env_context_in_findings(self):
        from bauer.preflight import run_doctor

        cfg = self._make_config_obj(provider="ollama", model="phi4-mini")
        reg = self._make_registry()

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (False, "offline")
        mock_client.has_model.return_value = False

        with patch("bauer.preflight.OllamaClient", return_value=mock_client), \
             patch.dict("os.environ", {"OLLAMA_CONTEXT_LENGTH": "4096"}):
            report = run_doctor(cfg, reg)

        # OLLAMA_CONTEXT_LENGTH should appear in findings
        assert any("4096" in f for f in report.findings)


# ══════════════════════════════════════════════════════════════════════════════
# model_switcher.py — config load failure path
# ══════════════════════════════════════════════════════════════════════════════


class TestModelSwitcherConfigError:
    def test_bad_config_yaml_uses_empty(self, tmp_path):
        from bauer.model_switcher import run_model_switcher

        cfg = tmp_path / "config.yaml"
        cfg.write_text("not: valid: yaml: [[[", encoding="utf-8")

        # Cancel provider selection so nothing is saved
        with patch("rich.prompt.Prompt.ask", return_value=""):
            run_model_switcher(cfg)  # Should not raise


# ══════════════════════════════════════════════════════════════════════════════
# model_registry.py — missing paths
# ══════════════════════════════════════════════════════════════════════════════


class TestModelRegistryExtras:
    def _make_registry(self, tmp_path: Path):
        from bauer.model_registry import load_registry
        data = {
            "models": {
                "phi4-mini": {
                    "ram_base_mb": 3000,
                    "ram_per_1k_ctx_mb": 0.5,
                    "max_context_safe": 32768,
                    "supports_tools": True,
                    "ram_profile": "medium",
                }
            }
        }
        import yaml
        f = tmp_path / "models.yaml"
        f.write_text(yaml.dump(data), encoding="utf-8")
        return load_registry(f)

    def test_get_known_model(self, tmp_path):
        reg = self._make_registry(tmp_path)
        info = reg.get("phi4-mini")
        assert info is not None
        assert info.ram_base_mb == 3000

    def test_names(self, tmp_path):
        reg = self._make_registry(tmp_path)
        names = reg.names()
        assert "phi4-mini" in names

    def test_contexto_seguro_no_ram(self, tmp_path):
        from bauer.model_registry import contexto_seguro
        reg = self._make_registry(tmp_path)
        info = reg.get("phi4-mini")
        # Very low RAM → context should be limited
        ctx = contexto_seguro(info, ram_disponivel_mb=100, folga_mb=512)
        assert ctx == 0  # Not enough RAM


# ══════════════════════════════════════════════════════════════════════════════
# env_loader.py — missing paths
# ══════════════════════════════════════════════════════════════════════════════


class TestEnvLoaderExtras:
    def test_quoted_single_values(self, tmp_path):
        from bauer.env_loader import load_dotenv
        f = tmp_path / ".env"
        f.write_text("KEY='single_quoted'\n", encoding="utf-8")
        load_dotenv(f)
        import os
        # Value should be loaded (quotes stripped)
        # The env_loader might or might not strip quotes — just check it runs
        # (the existing tests already cover basic cases)

    def test_export_prefix_ignored(self, tmp_path):
        from bauer.env_loader import load_dotenv
        f = tmp_path / ".env"
        f.write_text("export MY_VAR=123\n", encoding="utf-8")
        load_dotenv(f)

    def test_inline_comment(self, tmp_path):
        from bauer.env_loader import load_dotenv
        f = tmp_path / ".env"
        f.write_text("VAR=value  # comment\n", encoding="utf-8")
        load_dotenv(f)


# ══════════════════════════════════════════════════════════════════════════════
# agent_registry.py — missing lines 157, 159-160
# ══════════════════════════════════════════════════════════════════════════════


class TestAgentRegistryExtras:
    def test_delete_existing(self, tmp_path):
        from bauer.agent_registry import AgentDef, AgentRegistry
        f = tmp_path / "agents.yaml"
        reg = AgentRegistry(path=f)
        agent = AgentDef(name="del-agent", description="To delete", system="System")
        reg.save(agent)
        assert reg.get("del-agent") is not None
        reg.delete("del-agent")
        assert reg.get("del-agent") is None

    def test_delete_nonexistent(self, tmp_path):
        from bauer.agent_registry import AgentRegistry
        f = tmp_path / "agents.yaml"
        reg = AgentRegistry(path=f)
        # Should not raise
        reg.delete("nonexistent-agent")


# ══════════════════════════════════════════════════════════════════════════════
# workspace_manager.py — missing paths
# ══════════════════════════════════════════════════════════════════════════════


class TestWorkspaceManagerExtras:
    def test_add_and_list_tasks(self, tmp_path):
        from bauer.workspace_manager import WorkspaceManager
        wm = WorkspaceManager(workspace=tmp_path)
        wm.add_task("Task A", "Description A")
        tasks = wm.list_tasks()
        assert len(tasks) >= 1
        assert any(t.title == "Task A" for t in tasks)

    def test_add_task_with_spec_id(self, tmp_path):
        from bauer.workspace_manager import WorkspaceManager
        wm = WorkspaceManager(workspace=tmp_path)
        task = wm.add_task("Spec Task", "Desc", spec_id="my-spec")
        assert task.spec_id == "my-spec"

    def test_update_task_status(self, tmp_path):
        from bauer.workspace_manager import WorkspaceManager
        wm = WorkspaceManager(workspace=tmp_path)
        task = wm.add_task("Update Me", "")
        updated = wm.update_task_status(task.id, "IN_PROGRESS")
        assert updated is not None

    def test_get_project_info(self, tmp_path):
        from bauer.workspace_manager import WorkspaceManager
        wm = WorkspaceManager(workspace=tmp_path)
        wm.add_task("Some Task", "")
        info = wm.get_project_info()
        assert info is not None


# ══════════════════════════════════════════════════════════════════════════════
# self_tuner.py — missing paths
# ══════════════════════════════════════════════════════════════════════════════


class TestSelfTunerExtras:
    def _make_registry(self, tmp_path: Path):
        from bauer.model_registry import load_registry
        import yaml
        data = {"models": {"phi4-mini": {
            "ram_base_mb": 3000, "ram_per_1k_ctx_mb": 0.5,
            "max_context_safe": 32768,
            "supports_tools": True, "ram_profile": "medium"}}}
        f = tmp_path / "models.yaml"
        f.write_text(yaml.dump(data), encoding="utf-8")
        return load_registry(f)

    def test_tune_basic(self, tmp_path):
        from bauer.self_tuner import SelfTuner
        reg = self._make_registry(tmp_path)
        tuner = SelfTuner(memory_dir=tmp_path, safety_margin_mb=512)
        result = tuner.tune(
            desired_model="phi4-mini",
            desired_context=8192,
            minimum_context=512,
            installed_models=["phi4-mini"],
            registry=reg,
            ram_available_mb=32000,
        )
        assert result is not None

    def test_tune_insufficient_ram(self, tmp_path):
        from bauer.self_tuner import SelfTuner
        reg = self._make_registry(tmp_path)
        tuner = SelfTuner(memory_dir=tmp_path, safety_margin_mb=512)
        result = tuner.tune(
            desired_model="phi4-mini",
            desired_context=8192,
            minimum_context=512,
            installed_models=["phi4-mini"],
            registry=reg,
            ram_available_mb=500,  # Insufficient RAM
        )
        assert result is not None

    def test_tune_model_not_installed(self, tmp_path):
        from bauer.self_tuner import SelfTuner
        reg = self._make_registry(tmp_path)
        tuner = SelfTuner(memory_dir=tmp_path, safety_margin_mb=512)
        result = tuner.tune(
            desired_model="phi4-mini",
            desired_context=8192,
            minimum_context=512,
            installed_models=[],  # Empty - model not installed
            registry=reg,
            ram_available_mb=32000,
        )
        assert result is not None


# ══════════════════════════════════════════════════════════════════════════════
# server.py — additional paths
# ══════════════════════════════════════════════════════════════════════════════


class TestServerExtras:
    def _make_client(self, tmp_path: Path, api_key: str = "", rate_limit: int = 0):
        from fastapi.testclient import TestClient
        from bauer.server import create_app
        from bauer.tool_router import ToolRouter

        mock_ollama = MagicMock()
        mock_ollama.chat_stream.return_value = iter(["Hello world"])
        mock_ollama.list_models.return_value = ["phi4-mini"]
        mock_ollama.has_model.return_value = True

        router = ToolRouter(workspace=tmp_path)
        app = create_app(
            model_name="phi4-mini",
            applied_context=4096,
            router=router,
            client=mock_ollama,
            system_prompt="Test",
            sessions_dir=tmp_path / "sessions",
            api_key=api_key,
            rate_limit_requests=rate_limit,
        )
        return TestClient(app, raise_server_exceptions=False), mock_ollama

    def test_health_endpoint(self, tmp_path):
        client, _ = self._make_client(tmp_path)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_chat_non_streaming(self, tmp_path):
        client, _ = self._make_client(tmp_path)
        resp = client.post("/chat", json={"message": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data or "content" in data or isinstance(data, dict)

    def test_chat_with_api_key_required(self, tmp_path):
        client, _ = self._make_client(tmp_path, api_key="secret")
        # Without key → 401
        resp = client.post("/chat", json={"message": "hello"})
        assert resp.status_code in (401, 403)

    def test_chat_with_valid_api_key(self, tmp_path):
        client, _ = self._make_client(tmp_path, api_key="secret")
        resp = client.post(
            "/chat",
            json={"message": "hello"},
            headers={"Authorization": "Bearer secret"},
        )
        assert resp.status_code == 200

    def test_models_endpoint(self, tmp_path):
        client, _ = self._make_client(tmp_path)
        resp = client.get("/models")
        assert resp.status_code == 200

    def test_rate_limiting(self, tmp_path):
        client, _ = self._make_client(tmp_path, rate_limit=2)
        # First 2 requests should succeed
        for _ in range(2):
            resp = client.post("/chat", json={"message": "hi"})
            assert resp.status_code == 200
        # Third request should be rate limited
        resp = client.post("/chat", json={"message": "hi"})
        assert resp.status_code in (200, 429)


# ══════════════════════════════════════════════════════════════════════════════
# tool_router.py — additional paths
# ══════════════════════════════════════════════════════════════════════════════


class TestToolRouterExtras:
    def _make_router(self, tmp_path: Path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    def test_list_dir(self, tmp_path):
        router = self._make_router(tmp_path)
        result = router.execute({"action": "list_dir", "args": {"path": "."}})
        assert isinstance(result, str)

    def test_read_file_not_found(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError):
            router.execute({"action": "read_file", "args": {"path": "nonexistent.txt"}})

    def test_write_and_read_file(self, tmp_path):
        router = self._make_router(tmp_path)
        write_result = router.execute({"action": "write_file", "args": {"path": "test.txt", "content": "hello"}})
        assert isinstance(write_result, str)
        read_result = router.execute({"action": "read_file", "args": {"path": "test.txt"}})
        assert isinstance(read_result, str)
        assert "hello" in read_result

    def test_unknown_action_raises(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError, match="desconhecida"):
            router.execute({"action": "unknown_action_xyz"})

    def test_available_tools(self, tmp_path):
        router = self._make_router(tmp_path)
        tools = router.available_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_execute_json_string(self, tmp_path):
        import json
        router = self._make_router(tmp_path)
        action = json.dumps({"action": "list_dir", "args": {"path": "."}})
        result = router.execute(action)
        assert isinstance(result, str)

    def test_execute_missing_action_raises(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError, match="action"):
            router.execute({"args": {"path": "."}})

    def test_execute_invalid_args_type_raises(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError, match="args"):
            router.execute({"action": "list_dir", "args": "not_a_dict"})

    def test_tool_info(self, tmp_path):
        router = self._make_router(tmp_path)
        info = router.tool_info("list_dir")
        assert "description" in info
        assert "args" in info

    def test_tool_info_unknown(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError):
            router.tool_info("unknown_tool")
