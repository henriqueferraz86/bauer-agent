"""Testes do tool_allowlist automático para modelos locais (_runtime).

Regra: modelo local (ollama) com contexto pequeno recebe um toolset enxuto
automaticamente — as ~83 tools estouram o contexto e o Ollama trunca o prompt.
Um tool_allowlist explícito sempre vence; provider cloud e contexto grande
expõem tudo.

O corte de "contexto pequeno" subiu de 16384 para 65536 quando o Ollama passou
a usar function calling NATIVO: no bridge as tools iam no system prompt (~6k
tokens) e 32k aguentava; no native o array `tools=` custa ~11k tokens sozinho.
Medido no Beelink com qwen3-coder:30b — 75 tools / 12,5k de prompt responde
certo, 80 tools / 13k devolve vazio (eval_count=1, zero tool calls), e um
`bauer run` trivial passava 7 minutos sem executar uma única tool.
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
    # "grande" agora é >= 65536: com 128k de janela, gastar 11k em schemas cabe.
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=131072)) is None


def test_local_32k_agora_recebe_slim():
    """32k era considerado 'grande' e expunha as 83 tools.

    Regressão medida: com tool calling nativo isso põe ~11k tokens de schema em
    TODA chamada — um terço da janela antes de existir conversa — e o
    qwen3-coder:30b devolve resposta vazia. Ver docstring do módulo.
    """
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=32768)) == _LOCAL_DEFAULT_ALLOWLIST


def test_cloud_provider_exposes_all():
    assert _effective_tool_allowlist(_cfg(provider="openrouter", ctx=4096)) is None


def test_auto_off_exposes_all():
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=4096, auto=False)) is None


def test_none_cfg_is_safe():
    assert _effective_tool_allowlist(None) is None


def test_context_zero_does_not_slim():
    # requested_context não setado (0) → conservador, não arrisca aplicar slim
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=0)) is None
