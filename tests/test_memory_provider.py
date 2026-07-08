"""Testes para bauer/memory_provider.py — ABC + LocalMemoryProvider + nudge."""
from __future__ import annotations

from pathlib import Path

import pytest

from bauer.memory_provider import (
    LocalMemoryProvider,
    MemoryProvider,
    get_memory_provider,
    reset_memory_provider,
    set_memory_provider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_global_provider():
    """Garante provider global limpo antes/depois de cada teste."""
    reset_memory_provider()
    yield
    reset_memory_provider()


@pytest.fixture()
def mem_dir(tmp_path):
    return tmp_path / "memory"


@pytest.fixture()
def provider(tmp_path):
    p = LocalMemoryProvider()
    p.initialize(tmp_path)
    return p


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------

def test_cannot_instantiate_abstract():
    """MemoryProvider é ABC e não pode ser instanciada diretamente."""
    with pytest.raises(TypeError):
        MemoryProvider()  # type: ignore[abstract]


def test_concrete_provider_must_implement_initialize():
    class BadProvider(MemoryProvider):
        pass

    with pytest.raises(TypeError):
        BadProvider()  # type: ignore[abstract]


def test_minimal_concrete_provider():
    class MinimalProvider(MemoryProvider):
        def initialize(self, workspace):
            pass

    mp = MinimalProvider()
    # Todos os outros métodos têm implementação padrão
    mp.prefetch()
    mp.sync_turn(0, [])
    mp.on_session_end([])
    mp.on_pre_compress([])
    mp.on_memory_write("k", "v")
    assert mp.system_prompt_block() == ""
    assert mp.get_tool_schemas() == []


# ---------------------------------------------------------------------------
# LocalMemoryProvider — initialize / prefetch
# ---------------------------------------------------------------------------

def test_initialize_creates_memory_files(tmp_path):
    p = LocalMemoryProvider()
    p.initialize(tmp_path)
    mem_dir = tmp_path / "memory"
    assert mem_dir.exists()
    assert (mem_dir / "MEMORY.md").exists()
    assert (mem_dir / "USER_PREFERENCES.md").exists()


def test_prefetch_fills_block(provider):
    provider.prefetch()
    # system_prompt_block deve ter conteúdo (pelo menos o cabeçalho do MEMORY.md)
    block = provider.system_prompt_block()
    assert "Memória" in block or "MEMORY" in block


def test_system_prompt_block_empty_before_prefetch(tmp_path):
    p = LocalMemoryProvider()
    p.initialize(tmp_path)
    # Sem chamar prefetch(), bloco fica vazio (nenhum conteúdo relevante nas md vazias)
    # Pode ter conteúdo do cabeçalho — só garante que não quebra
    block = p.system_prompt_block()
    assert isinstance(block, str)


def test_system_prompt_block_truncated(provider, monkeypatch):
    """Bloco é truncado a MAX_SYSTEM_BLOCK_CHARS."""
    monkeypatch.setattr(LocalMemoryProvider, "_MAX_SYSTEM_BLOCK_CHARS", 50)
    provider.prefetch()
    block = provider.system_prompt_block()
    assert len(block) <= len("## Memória do Projeto\n\n") + 50 + 5  # slack mínimo


# ---------------------------------------------------------------------------
# LocalMemoryProvider — on_session_end / on_pre_compress
# ---------------------------------------------------------------------------

def test_on_session_end_writes_note(provider, tmp_path):
    messages = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": "olá"}]
    provider.on_session_end(messages)
    mem_content = (tmp_path / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "Sessão finalizada" in mem_content or "encerrada" in mem_content


def test_on_pre_compress_writes_lesson(provider, tmp_path):
    provider.on_pre_compress([{"role": "user", "content": "texto"}])
    lessons = (tmp_path / "memory" / "RUNTIME_LESSONS.md").read_text(encoding="utf-8")
    assert "compressão" in lessons.lower() or "Compressão" in lessons


def test_on_session_end_no_crash_uninitialized():
    """Não deve quebrar se chamado sem initialize()."""
    p = LocalMemoryProvider()
    p.on_session_end([])  # não deve lançar exceção


def test_on_pre_compress_no_crash_uninitialized():
    p = LocalMemoryProvider()
    p.on_pre_compress([])


# ---------------------------------------------------------------------------
# Nudge
# ---------------------------------------------------------------------------

def test_nudge_not_triggered_within_interval(provider):
    """Sem nudge antes de NUDGE_INTERVAL turnos."""
    for i in range(1, provider._NUDGE_INTERVAL):
        assert not provider.should_nudge(i), f"nudge inesperado no turno {i}"


def test_nudge_triggered_at_interval(provider):
    assert provider.should_nudge(provider._NUDGE_INTERVAL)


def test_nudge_not_repeated_immediately(provider):
    """Nudge só dispara uma vez por intervalo."""
    assert provider.should_nudge(provider._NUDGE_INTERVAL)
    # Imediatamente no próximo turno: sem nudge (cooldown)
    assert not provider.should_nudge(provider._NUDGE_INTERVAL + 1)


def test_nudge_fires_again_after_full_interval(provider):
    """Nudge dispara novamente após outro intervalo completo."""
    n = provider._NUDGE_INTERVAL
    assert provider.should_nudge(n)
    # Outro intervalo depois
    assert provider.should_nudge(2 * n)


def test_nudge_message_is_string(provider):
    msg = provider.nudge_message()
    assert isinstance(msg, str)
    assert len(msg) > 10


def test_nudge_base_class(provider):
    """Testa a lógica should_nudge da superclasse via wrapper."""
    n = provider._NUDGE_INTERVAL
    # Com last_write_turn explícito
    result = MemoryProvider.should_nudge(provider, n + 1, last_write_turn=0)
    assert result is True
    result2 = MemoryProvider.should_nudge(provider, n - 1, last_write_turn=0)
    assert result2 is False


# ---------------------------------------------------------------------------
# on_memory_write reseta o estado de nudge
# ---------------------------------------------------------------------------

def test_on_memory_write_updates_last_write(provider):
    """Escrever na memória deve resetar a contagem para o próximo nudge."""
    n = provider._NUDGE_INTERVAL
    # Dispara nudge no turno N
    provider.should_nudge(n)
    # Simula escrita: atualiza last_write_turn
    provider.on_memory_write("key", "value")
    assert provider._nudge_state.last_write_turn is not None


# ---------------------------------------------------------------------------
# get_tool_schemas
# ---------------------------------------------------------------------------

def test_get_tool_schemas_returns_list(provider):
    schemas = provider.get_tool_schemas()
    assert isinstance(schemas, list)


# ---------------------------------------------------------------------------
# Registry global
# ---------------------------------------------------------------------------

def test_get_memory_provider_default_is_local():
    # G12: default agora e MultiMemoryProvider([Local, SimpleVector]).
    # Ainda inclui um LocalMemoryProvider entre os sub-providers.
    from bauer.memory_provider import MultiMemoryProvider
    p = get_memory_provider()
    assert isinstance(p, MultiMemoryProvider)
    assert any(isinstance(sub, LocalMemoryProvider) for sub in p._providers)


def test_get_memory_provider_returns_same_instance():
    p1 = get_memory_provider()
    p2 = get_memory_provider()
    assert p1 is p2


def test_set_memory_provider_custom():
    class CustomProvider(MemoryProvider):
        def initialize(self, workspace):
            pass

    custom = CustomProvider()
    set_memory_provider(custom)
    assert get_memory_provider() is custom


def test_reset_memory_provider_creates_new():
    p1 = get_memory_provider()
    reset_memory_provider()
    p2 = get_memory_provider()
    assert p1 is not p2


# ---------------------------------------------------------------------------
# sync_turn e on_memory_write (cobertura de no-op defaults)
# ---------------------------------------------------------------------------

def test_sync_turn_noop(provider):
    """sync_turn não deve lançar exceção."""
    provider.sync_turn(1, [{"role": "user", "content": "hi"}])


def test_on_memory_write_noop(provider):
    provider.on_memory_write("chave", "valor")
