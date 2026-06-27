"""Testes para os gaps fechados no sprint de maturidade.

Cobre:
  - SEG-1: Path traversal protection (ToolRouter._sandbox)
  - SEG-2: Fernet encryption (auth.py _encrypt_token / _decrypt_token)
  - SEG-3: Audit log (audit_logger.py AuditLogger)
  - LOOP-1: Error classifier (error_classifier.py classify_api_error)
  - LOOP-2: Retry with backoff (retry_utils.py retry_with_backoff)
  - LOOP-3: Provider fallback (_collect_with_fallback em agent.py)
  - LOOP-4: Parallel tool execution (ThreadPoolExecutor em run_agent_session)
  - MEM-2: Memory cleanup TTL (memory_manager.py cleanup_old_entries)
  - MCP-2: Plugin hook system (plugin_hooks.py HookRegistry)
  - CLI-1: Tab completion (agent.py _SlashCompleter)
  - CLI-2: Slash commands (agent.py _SLASH_BASE)
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── SEG-1: Path traversal ───────────────────────────────────────────────────

class TestPathTraversalProtection:
    """ToolRouter._sandbox() deve bloquear todas as formas de path traversal."""

    def _make_router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=str(tmp_path))

    def test_explicit_dotdot_parts_blocked(self, tmp_path):
        from bauer.tool_router import SandboxError
        router = self._make_router(tmp_path)
        with pytest.raises(SandboxError, match="Acesso negado"):
            router._sandbox("../etc/passwd")

    def test_symlink_traversal_blocked(self, tmp_path):
        """Symlink que aponta para fora do workspace deve ser bloqueado."""
        from bauer.tool_router import SandboxError
        router = self._make_router(tmp_path)
        # Cria symlink dentro do workspace apontando para fora
        target = tmp_path.parent / "secret.txt"
        target.write_text("secret", encoding="utf-8")
        link = tmp_path / "link_to_secret"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("Symlinks não suportados neste ambiente")
        with pytest.raises(SandboxError, match="Acesso negado|path resolve"):
            router._sandbox("link_to_secret")

    def test_absolute_path_inside_workspace_ok(self, tmp_path):
        router = self._make_router(tmp_path)
        result = router._sandbox(str(tmp_path / "allowed.txt"))
        assert str(tmp_path) in str(result)

    def test_relative_path_inside_workspace_ok(self, tmp_path):
        router = self._make_router(tmp_path)
        result = router._sandbox("subdir/file.txt")
        assert "subdir" in str(result)

    def test_absolute_path_outside_workspace_blocked(self, tmp_path):
        from bauer.tool_router import SandboxError
        router = self._make_router(tmp_path)
        # tmp_path.parent is always outside the workspace (tmp_path)
        outside = str(tmp_path.parent / "outside_secret.txt")
        with pytest.raises(SandboxError):
            router._sandbox(outside)


# ─── SEG-2: Fernet encryption ────────────────────────────────────────────────

class TestFernetEncryption:
    """auth._encrypt_token / _decrypt_token deve usar Fernet quando disponível."""

    def test_encrypt_decrypt_roundtrip(self):
        from bauer.auth import _encrypt_token, _decrypt_token
        token = "sk-secret-api-key-12345"
        key = "my-test-key-32bytes-padded-here!"
        encrypted = _encrypt_token(token, key)
        assert encrypted != token  # deve ser diferente do original
        decrypted = _decrypt_token(encrypted, key)
        assert decrypted == token

    def test_fernet_prefix_present_when_cryptography_available(self):
        from bauer.auth import _encrypt_token, _FERNET_PREFIX
        try:
            from cryptography.fernet import Fernet  # noqa: F401
            has_cryptography = True
        except ImportError:
            has_cryptography = False

        if not has_cryptography:
            pytest.skip("cryptography não instalado")

        encrypted = _encrypt_token("token", "key")
        assert encrypted.startswith(_FERNET_PREFIX)

    def test_xor_legacy_still_decrypts(self):
        """Tokens encriptados com XOR antigo devem continuar funcionando."""
        from bauer.auth import _xor_encrypt, _decrypt_token
        token = "legacy-token"
        key = "legacy-key"
        xor_encrypted = _xor_encrypt(token, key)
        # _decrypt_token deve detectar que não tem prefixo fernet e usar XOR
        decrypted = _decrypt_token(xor_encrypted, key)
        assert decrypted == token

    def test_empty_token_returns_empty(self):
        from bauer.auth import _encrypt_token, _decrypt_token
        assert _encrypt_token("", "key") == ""
        assert _decrypt_token("", "key") == ""

    def test_empty_key_returns_token_unchanged(self):
        from bauer.auth import _encrypt_token, _decrypt_token
        assert _encrypt_token("token", "") == "token"
        assert _decrypt_token("token", "") == "token"


# ─── SEG-3: Audit log ────────────────────────────────────────────────────────

class TestAuditLogger:
    """AuditLogger deve gravar entradas JSONL thread-safe e sanitizar segredos."""

    def test_log_tool_call_writes_jsonl(self, tmp_path):
        from bauer.audit_logger import AuditLogger
        logger = AuditLogger(log_dir=tmp_path, session_id="test-session")
        logger.log_tool_call(
            "read_file",
            {"path": "test.txt"},
            status="ok",
            duration_ms=12.5,
            result_preview="conteudo do arquivo",
        )
        audit_file = tmp_path / "audit.jsonl"
        assert audit_file.exists()
        entry = json.loads(audit_file.read_text())
        assert entry["action"] == "read_file"
        assert entry["status"] == "ok"
        assert entry["session"] == "test-session"
        assert "ts" in entry
        assert entry["duration_ms"] == 12.5

    def test_sensitive_args_redacted(self, tmp_path):
        from bauer.audit_logger import AuditLogger
        logger = AuditLogger(log_dir=tmp_path)
        logger.log_tool_call(
            "http_request",
            {"url": "https://api.example.com", "api_key": "sk-secret", "token": "Bearer xyz"},
            status="ok",
            duration_ms=50.0,
        )
        entry = json.loads((tmp_path / "audit.jsonl").read_text())
        assert entry["args"]["api_key"] == "[REDACTED]"
        assert entry["args"]["token"] == "[REDACTED]"
        assert entry["args"]["url"] == "https://api.example.com"

    def test_error_status_includes_error_msg(self, tmp_path):
        from bauer.audit_logger import AuditLogger
        logger = AuditLogger(log_dir=tmp_path)
        logger.log_tool_call(
            "execute_code",
            {"code": "raise ValueError('boom')"},
            status="error",
            duration_ms=5.0,
            error_msg="ValueError: boom",
        )
        entry = json.loads((tmp_path / "audit.jsonl").read_text())
        assert entry["status"] == "error"
        assert "boom" in entry["error"]

    def test_thread_safe_concurrent_writes(self, tmp_path):
        from bauer.audit_logger import AuditLogger
        logger = AuditLogger(log_dir=tmp_path, session_id="concurrent")
        errors: list[Exception] = []

        def _write(i: int):
            try:
                logger.log_tool_call(
                    f"tool_{i}", {"i": i}, status="ok", duration_ms=float(i)
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
        assert len(lines) == 20


# ─── LOOP-1: Error classifier ────────────────────────────────────────────────

class TestErrorClassifier:
    """classify_api_error deve classificar corretamente os tipos de erro."""

    def test_rate_limit_429(self):
        from bauer.error_classifier import classify_api_error, FailReason
        from bauer.openai_client import OpenAIClientError
        exc = OpenAIClientError("[Provedor] HTTP 429. rate limit exceeded")
        result = classify_api_error(exc)
        assert result.reason == FailReason.RATE_LIMIT
        assert result.retryable is True

    def test_auth_error_401(self):
        from bauer.error_classifier import classify_api_error, FailReason
        from bauer.openai_client import OpenAIClientError
        exc = OpenAIClientError("[Provedor] HTTP 401. invalid api key")
        result = classify_api_error(exc)
        assert result.reason == FailReason.AUTH_ERROR
        assert result.retryable is False

    def test_server_error_503(self):
        from bauer.error_classifier import classify_api_error, FailReason
        from bauer.openai_client import OpenAIClientError
        exc = OpenAIClientError("[Provedor] HTTP 503. service unavailable")
        result = classify_api_error(exc)
        assert result.retryable is True
        assert result.should_fallback is True

    def test_timeout_error(self):
        from bauer.error_classifier import classify_api_error, FailReason
        from bauer.openai_client import OpenAIClientError
        exc = OpenAIClientError("Timeout (60s) em https://api.openai.com.")
        result = classify_api_error(exc)
        assert result.reason == FailReason.TIMEOUT
        assert result.retryable is True

    def test_context_overflow(self):
        from bauer.error_classifier import classify_api_error, FailReason
        from bauer.openai_client import OpenAIClientError
        exc = OpenAIClientError("context_length_exceeded: max tokens is 4096")
        result = classify_api_error(exc)
        assert result.reason == FailReason.CONTEXT_OVERFLOW
        assert result.should_compress is True

    def test_generic_error_unknown(self):
        from bauer.error_classifier import classify_api_error, FailReason
        exc = ValueError("algo inesperado aconteceu")
        result = classify_api_error(exc)
        assert result.reason == FailReason.UNKNOWN


# ─── LOOP-2: Retry with backoff ──────────────────────────────────────────────

class TestRetryWithBackoff:
    """retry_with_backoff deve tentar N vezes com delay e classificar erros."""

    def test_success_on_first_attempt(self):
        from bauer.retry_utils import retry_with_backoff
        result = retry_with_backoff(lambda: "ok", max_retries=2)
        assert result == "ok"

    def test_retries_on_retryable_error(self):
        from bauer.retry_utils import retry_with_backoff
        from bauer.openai_client import OpenAIClientError

        calls = [0]

        def _flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise OpenAIClientError("[Provedor] HTTP 429. rate limit exceeded")
            return "eventual-success"

        result = retry_with_backoff(
            _flaky,
            max_retries=3,
            base_delay=0.01,
            max_delay=0.1,
        )
        assert result == "eventual-success"
        assert calls[0] == 3

    def test_raises_immediately_on_non_retryable(self):
        from bauer.retry_utils import retry_with_backoff
        from bauer.openai_client import OpenAIClientError

        calls = [0]

        def _auth_fail():
            calls[0] += 1
            raise OpenAIClientError("[Provedor] HTTP 401. invalid api key")

        with pytest.raises(OpenAIClientError):
            retry_with_backoff(_auth_fail, max_retries=3, base_delay=0.01)

        assert calls[0] == 1  # não tentou de novo

    def test_on_retry_callback_called(self):
        from bauer.retry_utils import retry_with_backoff
        from bauer.openai_client import OpenAIClientError

        retry_attempts = []

        def _cb(attempt, classified, wait):
            retry_attempts.append(attempt)

        calls = [0]

        def _fail_twice():
            calls[0] += 1
            if calls[0] < 3:
                raise OpenAIClientError("[Provedor] HTTP 503. service unavailable")
            return "ok"

        retry_with_backoff(_fail_twice, max_retries=3, base_delay=0.01, on_retry=_cb)
        assert retry_attempts == [1, 2]

    def test_jittered_backoff_positive_delay(self):
        from bauer.retry_utils import jittered_backoff
        for attempt in range(1, 5):
            delay = jittered_backoff(attempt, base_delay=1.0, max_delay=60.0)
            assert delay > 0
            assert delay <= 60.0 + 0.5 * 60.0  # max + max_jitter


# ─── LOOP-3: Provider fallback ───────────────────────────────────────────────

class TestProviderFallback:
    """_collect_with_fallback deve tentar providers alternativos quando o principal falha."""

    def _make_mock_client(self, response="ok"):
        client = MagicMock(spec=[])  # spec=[] garante que chat_with_retry não existe
        # Adiciona apenas o que precisamos
        client.chat_stream = MagicMock(return_value=iter([response]))
        return client

    def _make_failing_client(self, error_msg="[Provedor] HTTP 503. service unavailable"):
        from bauer.openai_client import OpenAIClientError
        client = MagicMock(spec=[])  # spec=[] → sem chat_with_retry
        client.chat_stream = MagicMock(side_effect=OpenAIClientError(error_msg))
        return client

    def test_fallback_used_when_primary_fails_with_server_error(self):
        from bauer.agent import _collect_with_fallback
        from rich.console import Console

        primary = self._make_failing_client()
        fallback = self._make_mock_client("fallback-response")
        console = Console(quiet=True)

        resp, active_client, active_model = _collect_with_fallback(
            primary, "gpt-4", [], [(fallback, "fallback-model")], console
        )
        assert resp == "fallback-response"
        assert active_client is fallback

    def test_no_fallback_raises_original_error(self):
        from bauer.agent import _collect_with_fallback
        from bauer.openai_client import OpenAIClientError
        from rich.console import Console

        primary = self._make_failing_client()
        console = Console(quiet=True)

        with pytest.raises(OpenAIClientError):
            _collect_with_fallback(primary, "gpt-4", [], None, console)

    def test_auth_error_does_not_trigger_fallback(self):
        """Erros de autenticação (401) não devem tentar fallback."""
        from bauer.agent import _collect_with_fallback
        from bauer.openai_client import OpenAIClientError
        from rich.console import Console

        primary = self._make_failing_client("[Provedor] HTTP 401. invalid api key")
        fallback = self._make_mock_client("should-not-reach")
        console = Console(quiet=True)

        with pytest.raises(OpenAIClientError):
            _collect_with_fallback(
                primary, "gpt-4", [], [(fallback, "fallback-model")], console
            )
        fallback.chat_stream.assert_not_called()


# ─── MEM-2: Memory cleanup TTL ───────────────────────────────────────────────

class TestMemoryCleanupTTL:
    """cleanup_old_entries deve remover entradas antigas e preservar recentes."""

    def _make_memory_dir(self, tmp_path: Path) -> Path:
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        return mem_dir

    def test_removes_old_entries(self, tmp_path):
        from datetime import datetime, timedelta, timezone
        from bauer.memory_manager import MemoryManager
        mem_dir = self._make_memory_dir(tmp_path)

        # Data "recente" relativa ao agora (evita time-bomb: datas fixas vencem o TTL).
        recente = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M UTC")

        # Cria arquivo com entradas antigas e recentes
        content = (
            "# MEMORY.md — cabeçalho do arquivo\n\n"
            "## [2020-01-01 10:00 UTC] Entrada antiga\n"
            "- campo: valor antigo\n\n"
            f"## [{recente}] Entrada recente\n"
            "- campo: valor recente\n\n"
        )
        (mem_dir / "MEMORY.md").write_text(content, encoding="utf-8")

        mm = MemoryManager(mem_dir)
        removed = mm.cleanup_old_entries(max_age_days=30, files=["MEMORY.md"])

        assert removed["MEMORY.md"] == 1  # apenas entrada antiga removida
        result = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "Entrada antiga" not in result
        assert "Entrada recente" in result

    def test_dry_run_does_not_modify_file(self, tmp_path):
        from bauer.memory_manager import MemoryManager
        mem_dir = self._make_memory_dir(tmp_path)

        content = (
            "# Cabeçalho\n\n"
            "## [2020-01-01 10:00 UTC] Velha entrada\n- x: y\n\n"
        )
        (mem_dir / "MEMORY.md").write_text(content, encoding="utf-8")
        original = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")

        mm = MemoryManager(mem_dir)
        removed = mm.cleanup_old_entries(max_age_days=30, files=["MEMORY.md"], dry_run=True)

        assert removed["MEMORY.md"] == 1
        # Arquivo NÃO modificado
        assert (mem_dir / "MEMORY.md").read_text(encoding="utf-8") == original

    def test_preserves_header_block(self, tmp_path):
        """Bloco de cabeçalho (sem timestamp) nunca deve ser removido."""
        from bauer.memory_manager import MemoryManager
        mem_dir = self._make_memory_dir(tmp_path)

        content = (
            "# MEMORY.md — Notas gerais\n\n"
            "Resumos de sessão.\n\n---\n\n"
            "## [2020-01-01 10:00 UTC] Entrada velha\n- x: y\n\n"
        )
        (mem_dir / "MEMORY.md").write_text(content, encoding="utf-8")

        mm = MemoryManager(mem_dir)
        mm.cleanup_old_entries(max_age_days=30, files=["MEMORY.md"])

        result = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "Notas gerais" in result
        assert "Entrada velha" not in result

    def test_no_entries_old_enough_returns_zero(self, tmp_path):
        from datetime import datetime, timedelta, timezone
        from bauer.memory_manager import MemoryManager
        mem_dir = self._make_memory_dir(tmp_path)

        # Data "recente" relativa ao agora (evita time-bomb: datas fixas vencem o TTL).
        recente = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M UTC")
        content = f"# Cabeçalho\n\n## [{recente}] Recente\n- x: y\n\n"
        (mem_dir / "MEMORY.md").write_text(content, encoding="utf-8")

        mm = MemoryManager(mem_dir)
        removed = mm.cleanup_old_entries(max_age_days=30, files=["MEMORY.md"])

        assert removed["MEMORY.md"] == 0


# ─── MCP-2: Plugin hook system ───────────────────────────────────────────────

class TestPluginHooks:
    """HookRegistry deve registrar, emitir e carregar plugins corretamente."""

    def setup_method(self):
        # Cada teste usa um registry limpo para evitar contaminação
        from bauer.plugin_hooks import HookRegistry
        self.registry = HookRegistry()

    def test_register_and_emit(self):
        calls = []

        @self.registry.on("pre_tool_call")
        def handler(action, args):
            calls.append((action, args))

        self.registry.emit("pre_tool_call", action="read_file", args={"path": "x"})
        assert calls == [("read_file", {"path": "x"})]

    def test_multiple_handlers_all_called(self):
        calls = []
        self.registry.register("post_tool_call", lambda **kw: calls.append("h1"))
        self.registry.register("post_tool_call", lambda **kw: calls.append("h2"))
        self.registry.emit("post_tool_call", action="x", args={}, result="r", error=None)
        assert calls == ["h1", "h2"]

    def test_error_in_handler_does_not_propagate(self):
        def bad_handler(**kw):
            raise RuntimeError("handler crash")

        self.registry.register("pre_llm_call", bad_handler)
        # Não deve levantar exceção
        self.registry.emit("pre_llm_call", model="gpt", messages=[])

    def test_invalid_event_name_ignored(self):
        # Não deve explodir, mas também não deve registrar
        self.registry.register("evento_invalido", lambda: None)
        handlers = self.registry._handlers.get("evento_invalido", [])
        assert len(handlers) == 0

    def test_unregister_removes_handler(self):
        calls = []

        def handler(**kw):
            calls.append(1)

        self.registry.register("session_start", handler)
        self.registry.unregister("session_start", handler)
        self.registry.emit("session_start", session_id="s", model="m")
        assert calls == []

    def test_clear_removes_all_handlers(self):
        calls = []
        self.registry.register("session_end", lambda **kw: calls.append(1))
        self.registry.register("session_end", lambda **kw: calls.append(2))
        self.registry.clear("session_end")
        self.registry.emit("session_end", session_id="s", model="m")
        assert calls == []

    def test_load_plugins_from_directory(self, tmp_path):
        """Plugins em tmp_path/*.py devem ser carregados e registrar hooks."""
        plugin_code = """
from bauer.plugin_hooks import HookRegistry
# O registry do teste NÃO é o global; usamos um side-channel para verificar
import os
os.environ["_TEST_PLUGIN_LOADED"] = "yes"
"""
        (tmp_path / "test_plugin.py").write_text(plugin_code, encoding="utf-8")
        import os
        os.environ.pop("_TEST_PLUGIN_LOADED", None)
        loaded = self.registry.load_plugins(plugin_dir=tmp_path)
        assert "test_plugin" in loaded
        assert os.environ.get("_TEST_PLUGIN_LOADED") == "yes"

    def test_load_plugins_bad_plugin_does_not_crash(self, tmp_path):
        """Plugin com erro de sintaxe não deve impedir outros plugins."""
        (tmp_path / "bad.py").write_text("this is not valid python!!!", encoding="utf-8")
        loaded = self.registry.load_plugins(plugin_dir=tmp_path)
        assert "bad" not in loaded  # falha silenciosa


# ─── CLI-1: Tab completion ────────────────────────────────────────────────────

class TestTabCompletion:
    """_SlashCompleter e _SLASH_BASE devem existir e cobrir os comandos esperados."""

    def test_slash_base_has_expected_commands(self):
        from bauer.agent import _SLASH_BASE
        required = {"/exit", "/clear", "/status", "/model", "/sessions",
                    "/memory", "/task", "/spec", "/project", "/agents"}
        for cmd in required:
            assert cmd in _SLASH_BASE, f"Comando {cmd!r} ausente de _SLASH_BASE"

    def test_slash_completer_returns_matches(self):
        try:
            from bauer.agent import _SlashCompleter
            from prompt_toolkit.document import Document
            from prompt_toolkit.completion import CompleteEvent
        except ImportError:
            pytest.skip("prompt_toolkit não instalado")

        completer = _SlashCompleter()
        doc = Document("/mem")
        event = CompleteEvent()
        completions = list(completer.get_completions(doc, event))
        texts = [c.text for c in completions]
        # /memory e /memory search devem aparecer
        assert any("memory" in t for t in texts)

    def test_slash_completer_ignores_non_slash(self):
        try:
            from bauer.agent import _SlashCompleter
            from prompt_toolkit.document import Document
            from prompt_toolkit.completion import CompleteEvent
        except ImportError:
            pytest.skip("prompt_toolkit não instalado")

        completer = _SlashCompleter()
        doc = Document("oi como vai")  # sem /
        event = CompleteEvent()
        completions = list(completer.get_completions(doc, event))
        assert completions == []


# ─── CLI-2: Slash commands ────────────────────────────────────────────────────

class TestSlashCommands:
    """Conjuntos de comandos slash devem estar definidos e não vazios."""

    def test_exit_cmds_defined(self):
        from bauer.agent import _EXIT_CMDS
        assert "/exit" in _EXIT_CMDS
        assert "/quit" in _EXIT_CMDS

    def test_clear_cmds_defined(self):
        from bauer.agent import _CLEAR_CMDS
        assert "/clear" in _CLEAR_CMDS

    def test_status_cmds_defined(self):
        from bauer.agent import _STATUS_CMDS
        assert "/status" in _STATUS_CMDS

    def test_model_cmds_defined(self):
        from bauer.agent import _MODEL_CMDS
        assert "/model" in _MODEL_CMDS

    def test_slash_descriptions_nonempty(self):
        from bauer.agent import _SLASH_DESCRIPTIONS
        assert len(_SLASH_DESCRIPTIONS) >= 10
        for cmd, desc in _SLASH_DESCRIPTIONS.items():
            assert cmd.startswith("/"), f"{cmd!r} deve começar com /"
            assert desc, f"{cmd!r} tem descrição vazia"


# ─── config_loader fallback_providers ────────────────────────────────────────

class TestFallbackProvidersConfig:
    """ModelSection deve aceitar e expor fallback_providers."""

    def test_fallback_providers_default_empty(self):
        from bauer.config_loader import ModelSection
        m = ModelSection(name="gpt-4o-mini", requested_context=16384, provider="openai")
        assert m.fallback_providers == []

    def test_fallback_providers_set_from_config(self):
        from bauer.config_loader import ModelSection
        m = ModelSection(
            name="gpt-4o-mini",
            requested_context=16384,
            provider="openai",
            fallback_providers=["openrouter", "groq"],
        )
        assert m.fallback_providers == ["openrouter", "groq"]
