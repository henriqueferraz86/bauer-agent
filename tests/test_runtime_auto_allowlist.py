"""Testes do tool_allowlist automático para modelos locais (_runtime).

Regra: modelo local (ollama) com contexto pequeno recebe um toolset enxuto
automaticamente — as ~79 tools estouram o contexto e o Ollama trunca o prompt.
Um tool_allowlist explícito sempre vence; provider cloud e contexto grande
expõem tudo.
"""

from __future__ import annotations

from types import SimpleNamespace

from bauer.commands._runtime import _LOCAL_DEFAULT_ALLOWLIST, _effective_tool_allowlist


def _cfg(provider="ollama", ctx=4096, allowlist=None, auto=True):
    return SimpleNamespace(
        model=SimpleNamespace(provider=provider, requested_context=ctx),
        tools=SimpleNamespace(tool_allowlist=allowlist or [], auto_tool_allowlist=auto),
    )


def test_explicit_allowlist_wins():
    cfg = _cfg(allowlist=["read_file", "web_search"])
    assert _effective_tool_allowlist(cfg) == ["read_file", "web_search"]


def test_local_small_context_gets_slim_default():
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=4096)) == _LOCAL_DEFAULT_ALLOWLIST


def test_local_large_context_exposes_all():
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=32768)) is None


def test_cloud_provider_exposes_all():
    assert _effective_tool_allowlist(_cfg(provider="openrouter", ctx=4096)) is None


def test_auto_off_exposes_all():
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=4096, auto=False)) is None


def test_none_cfg_is_safe():
    assert _effective_tool_allowlist(None) is None


def test_context_zero_does_not_slim():
    # requested_context não setado (0) → conservador, não arrisca aplicar slim
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=0)) is None
