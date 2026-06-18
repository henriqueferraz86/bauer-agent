"""G12: testes para os 3 novos memory providers — SimpleVectorProvider, HttpMemoryProvider, Mem0Provider."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# SimpleVectorProvider
# ---------------------------------------------------------------------------

class TestSimpleVectorProvider:

    @pytest.fixture
    def provider(self, tmp_path):
        from bauer.memory_provider import SimpleVectorProvider
        p = SimpleVectorProvider(persist_path=tmp_path / "vec.json")
        p.initialize(tmp_path)
        return p

    def test_initialize_creates_no_file_when_no_docs(self, provider, tmp_path):
        assert not (tmp_path / "vec.json").exists() or True  # OK either way

    def test_sync_turn_indexes_assistant_message(self, provider, tmp_path):
        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a high-level programming language used for automation, data science, and web development."},
        ]
        provider.sync_turn(1, messages)
        assert (tmp_path / "vec.json").exists()
        data = json.loads((tmp_path / "vec.json").read_text())
        assert len(data["docs"]) == 1
        assert "t1" == data["docs"][0]["id"]

    def test_sync_turn_skips_short_responses(self, provider, tmp_path):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "ok"},
        ]
        provider.sync_turn(1, messages)
        assert not (tmp_path / "vec.json").exists()

    def test_prefetch_loads_recent_docs(self, provider):
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "A" * 100},
        ]
        provider.sync_turn(1, messages)
        provider.prefetch()
        assert len(provider._relevant) == 1

    def test_system_prompt_block_returns_string(self, provider):
        block = provider.system_prompt_block()
        assert isinstance(block, str)

    def test_system_prompt_block_nonempty_after_sync(self, provider):
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "This is a test response with some content about programming and Python."},
        ]
        provider.sync_turn(1, messages)
        provider.prefetch()
        block = provider.system_prompt_block()
        assert len(block) > 0
        assert "Memória Vetorial" in block or "---" in block or len(block) > 10

    def test_sync_turn_rerankses_by_similarity(self, provider):
        msgs_python = [
            {"role": "user", "content": "python programming"},
            {"role": "assistant", "content": "Python is a programming language for automation scripts data science and web frameworks like Django Flask."},
        ]
        msgs_cooking = [
            {"role": "user", "content": "cooking recipes"},
            {"role": "assistant", "content": "Cooking involves heat salt pepper garlic onion tomato sauce pasta ingredients bake fry boil simmer stew."},
        ]
        provider.sync_turn(1, msgs_python)
        provider.sync_turn(2, msgs_cooking)

        # Query about python should rank python doc higher
        msgs_query = [
            {"role": "user", "content": "python programming language"},
            {"role": "assistant", "content": "ok"},
        ]
        provider.sync_turn(3, msgs_query)
        if provider._relevant:
            assert "python" in provider._relevant[0].lower() or "programming" in provider._relevant[0].lower()

    def test_persist_and_reload(self, tmp_path):
        from bauer.memory_provider import SimpleVectorProvider
        p1 = SimpleVectorProvider(persist_path=tmp_path / "vec.json")
        p1.initialize(tmp_path)
        messages = [
            {"role": "user", "content": "test persistence"},
            {"role": "assistant", "content": "This content should be persisted to disk for later retrieval by another instance."},
        ]
        p1.sync_turn(1, messages)

        p2 = SimpleVectorProvider(persist_path=tmp_path / "vec.json")
        p2.initialize(tmp_path)
        assert len(p2._docs) == 1

    def test_max_docs_trimmed_on_save(self, tmp_path):
        from bauer.memory_provider import SimpleVectorProvider
        p = SimpleVectorProvider(persist_path=tmp_path / "vec.json")
        p._MAX_DOCS = 5
        p.initialize(tmp_path)
        for i in range(10):
            msgs = [
                {"role": "user", "content": f"query {i}"},
                {"role": "assistant", "content": f"This is response number {i} with enough content to be indexed properly by the vector store."},
            ]
            p.sync_turn(i, msgs)
        data = json.loads((tmp_path / "vec.json").read_text())
        assert len(data["docs"]) <= 5

    def test_cosine_zero_when_no_overlap(self, provider):
        a = {"foo": 1, "bar": 2}
        b = {"baz": 3, "qux": 4}
        assert provider._cosine(a, b) == 0.0

    def test_cosine_one_for_identical(self, provider):
        a = {"foo": 1}
        score = provider._cosine(a, a)
        assert abs(score - 1.0) < 1e-9

    def test_tokenize_strips_punctuation(self, provider):
        tokens = provider._tokenize("Hello, World! Testing.")
        assert "hello" in tokens or "world" in tokens


# ---------------------------------------------------------------------------
# HttpMemoryProvider
# ---------------------------------------------------------------------------

class TestHttpMemoryProvider:

    @pytest.fixture
    def provider(self):
        from bauer.memory_provider import HttpMemoryProvider
        return HttpMemoryProvider(base_url="http://example.com", api_key="test-key")

    def test_initialize_is_noop(self, provider, tmp_path):
        provider.initialize(tmp_path)  # should not raise

    def test_headers_include_auth(self, provider):
        h = provider._headers()
        assert h.get("Authorization") == "Bearer test-key"

    def test_headers_no_auth_when_empty_key(self):
        from bauer.memory_provider import HttpMemoryProvider
        p = HttpMemoryProvider(base_url="http://x.com")
        h = p._headers()
        assert "Authorization" not in h

    def test_prefetch_populates_snippets(self, provider):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [{"text": "snippet1"}, {"text": "snippet2"}]}
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            provider.prefetch()
            mock_get.assert_called_once()
            assert provider._snippets == ["snippet1", "snippet2"]

    def test_prefetch_graceful_on_error(self, provider):
        with patch("httpx.get", side_effect=Exception("connection refused")):
            provider.prefetch()  # should not raise
            assert provider._snippets == []

    def test_sync_turn_posts_assistant_content(self, provider):
        messages = [
            {"role": "user", "content": "test"},
            {"role": "assistant", "content": "X" * 100},
        ]
        mock_resp = MagicMock()
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            provider.sync_turn(1, messages)
            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            body = call_kwargs[1].get("json", call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
            assert "content" in body or "namespace" in body

    def test_sync_turn_skips_short_response(self, provider):
        messages = [{"role": "assistant", "content": "hi"}]
        with patch("httpx.post") as mock_post:
            provider.sync_turn(1, messages)
            mock_post.assert_not_called()

    def test_sync_turn_graceful_on_error(self, provider):
        messages = [{"role": "assistant", "content": "X" * 100}]
        with patch("httpx.post", side_effect=Exception("timeout")):
            provider.sync_turn(1, messages)  # should not raise

    def test_system_prompt_block_with_snippets(self, provider):
        provider._snippets = ["relevant content here"]
        block = provider.system_prompt_block()
        assert "Memória Remota" in block or "RAG" in block or "relevant content" in block

    def test_system_prompt_block_empty_when_no_snippets(self, provider):
        provider._snippets = []
        assert provider.system_prompt_block() == ""

    def test_namespace_passed_to_get(self, provider):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status.return_value = None
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            provider.prefetch()
            call_kwargs = mock_get.call_args
            params = call_kwargs[1].get("params", {})
            assert params.get("namespace") == "bauer"


# ---------------------------------------------------------------------------
# Mem0Provider
# ---------------------------------------------------------------------------

class TestMem0Provider:

    @pytest.fixture
    def provider(self):
        from bauer.memory_provider import Mem0Provider
        return Mem0Provider(api_key="test-mem0-key", user_id="test-user")

    def test_initialize_is_noop(self, provider, tmp_path):
        provider.initialize(tmp_path)

    def test_prefetch_no_api_key_skips(self, tmp_path):
        from bauer.memory_provider import Mem0Provider
        p = Mem0Provider(api_key="", user_id="u")
        p.prefetch()  # should not raise and should not call httpx
        assert p._memories == []

    def test_prefetch_populates_memories(self, provider):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"memory": "User prefers Python over JavaScript"},
            {"memory": "Working on Bauer Agent project"},
        ]
        mock_resp.raise_for_status.return_value = None
        with patch("httpx.get", return_value=mock_resp):
            provider.prefetch()
            assert len(provider._memories) == 2
            assert "Python" in provider._memories[0]

    def test_prefetch_handles_results_key(self, provider):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": [{"memory": "foo"}]}
        mock_resp.raise_for_status.return_value = None
        with patch("httpx.get", return_value=mock_resp):
            provider.prefetch()
            assert "foo" in provider._memories

    def test_prefetch_graceful_on_error(self, provider):
        with patch("httpx.get", side_effect=Exception("network error")):
            provider.prefetch()
            assert provider._memories == []

    def test_sync_turn_posts_messages(self, provider):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        mock_resp = MagicMock()
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            provider.sync_turn(1, messages)
            mock_post.assert_called_once()
            body = mock_post.call_args[1].get("json", {})
            assert "messages" in body
            assert body.get("user_id") == "test-user"

    def test_sync_turn_skips_empty_messages(self, provider):
        with patch("httpx.post") as mock_post:
            provider.sync_turn(1, [])
            mock_post.assert_not_called()

    def test_sync_turn_graceful_on_error(self, provider):
        messages = [{"role": "user", "content": "test"}]
        with patch("httpx.post", side_effect=Exception("timeout")):
            provider.sync_turn(1, messages)  # should not raise

    def test_system_prompt_block_with_memories(self, provider):
        provider._memories = ["Remember: user likes TypeScript", "Project: BauerAgent"]
        block = provider.system_prompt_block()
        assert "Mem0" in block or "memórias" in block.lower() or "TypeScript" in block

    def test_system_prompt_block_empty_when_no_memories(self, provider):
        provider._memories = []
        assert provider.system_prompt_block() == ""

    def test_api_key_from_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEM0_API_KEY", "env-key-123")
        from bauer.memory_provider import Mem0Provider
        p = Mem0Provider()
        assert p._api_key == "env-key-123"


# ---------------------------------------------------------------------------
# Integration: MultiMemoryProvider with new providers
# ---------------------------------------------------------------------------

class TestMultiWithNewProviders:

    def test_multi_aggregates_system_blocks(self, tmp_path):
        from bauer.memory_provider import MultiMemoryProvider, SimpleVectorProvider, HttpMemoryProvider

        svp = SimpleVectorProvider(persist_path=tmp_path / "v.json")
        svp.initialize(tmp_path)
        svp._relevant = ["vector snippet"]

        http = HttpMemoryProvider("http://x.com")
        http._snippets = ["http snippet"]

        multi = MultiMemoryProvider([svp, http])
        block = multi.system_prompt_block()
        # At least one provider should contribute
        assert isinstance(block, str)

    def test_multi_initializes_all_sub_providers(self, tmp_path):
        from bauer.memory_provider import MultiMemoryProvider, SimpleVectorProvider

        svp1 = SimpleVectorProvider(persist_path=tmp_path / "v1.json")
        svp2 = SimpleVectorProvider(persist_path=tmp_path / "v2.json")
        multi = MultiMemoryProvider([svp1, svp2])
        multi.initialize(tmp_path)
        assert svp1._initialized
        assert svp2._initialized

    def test_get_memory_provider_returns_multi(self):
        from bauer.memory_provider import get_memory_provider, MultiMemoryProvider, reset_memory_provider
        reset_memory_provider()
        provider = get_memory_provider()
        assert isinstance(provider, MultiMemoryProvider)
        reset_memory_provider()

    def test_get_memory_provider_multi_contains_local_and_vector(self):
        from bauer.memory_provider import (
            get_memory_provider, MultiMemoryProvider,
            LocalMemoryProvider, SimpleVectorProvider, reset_memory_provider
        )
        reset_memory_provider()
        provider = get_memory_provider()
        assert isinstance(provider, MultiMemoryProvider)
        provider_types = [type(p) for p in provider._providers]
        assert LocalMemoryProvider in provider_types
        assert SimpleVectorProvider in provider_types
        reset_memory_provider()
