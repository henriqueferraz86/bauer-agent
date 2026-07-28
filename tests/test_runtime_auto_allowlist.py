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


def test_allowlist_so_tem_nomes_de_tools_reais():
    """Nome errado na lista some em silêncio — o router ignora o desconhecido.

    Um typo aqui tira uma ferramenta do agente sem erro nenhum, e a única
    evidência seria o modelo "esquecendo" de usá-la.
    """
    from pathlib import Path

    from bauer.shell_runner import ShellRunner
    from bauer.tool_router import ToolRouter

    # com shell_runner: sem ele `run_command` nem existe e o teste daria
    # falso positivo de nome inválido.
    router = ToolRouter(workspace="x", web_enabled=True,
                        shell_runner=ShellRunner(workspace=Path("x")))
    desconhecidos = sorted(set(_LOCAL_DEFAULT_ALLOWLIST) - set(router.available_tools()))
    assert not desconhecidos, f"nomes que não são tools: {desconhecidos}"
    assert len(_LOCAL_DEFAULT_ALLOWLIST) == len(set(_LOCAL_DEFAULT_ALLOWLIST)), "há duplicados"


def test_custo_fixo_do_toolset_local_cabe_na_janela():
    """Schemas + system prompt, ANTES de qualquer conversa, têm que caber.

    Com as 84 tools esse custo fixo é ~14k tokens — acima do teto de ~12,5k
    medido no qwen3-coder:30b, então toda requisição nascia estourada. Este
    teto de 8k mantém folga real numa janela de 32k; se alguém engordar a
    lista até passar disso, o modo native volta a devolver resposta vazia.
    """
    import json
    from pathlib import Path

    from bauer.agent import _build_system_prompt
    from bauer.shell_runner import ShellRunner
    from bauer.tool_router import ToolRouter

    router = ToolRouter(workspace="x", web_enabled=True,
                        shell_runner=ShellRunner(workspace=Path("x")),
                        tool_allowlist=_LOCAL_DEFAULT_ALLOWLIST)
    schemas_tokens = len(json.dumps(router.get_tool_schemas())) // 4
    prompt_tokens = len(_build_system_prompt(router, tool_mode="native")) // 4
    fixo = schemas_tokens + prompt_tokens

    assert fixo < 8000, (
        f"custo fixo do toolset local subiu para {fixo} tokens "
        f"(schemas={schemas_tokens}, system_prompt={prompt_tokens}). "
        "Acima de ~12,5k o qwen3-coder:30b devolve resposta vazia."
    )


def test_router_recebe_a_secao_mcp_do_config():
    """`mcp.servers` do config.yaml precisa chegar ao ROUTER, não só ao doctor.

    O único lugar que atribuía `_mcp_config` era o probe do `bauer doctor`
    (cli.py). Nos caminhos reais — serve, run, agent — o router nascia sem ele,
    então servidor MCP configurado aparecia `ok` no diagnóstico e a tool
    `mcp_list_servers` respondia "Nenhum servidor MCP configurado". Medido no
    Beelink com 2 servidores configurados.
    """
    from pathlib import Path
    from types import SimpleNamespace

    from bauer.commands._runtime import _build_router

    servidor = SimpleNamespace(command=["toolbox", "--stdio"], url=None, env={},
                               timeout=30, cwd=None, headers={})
    cfg = SimpleNamespace(
        model=SimpleNamespace(provider="ollama", name="m", requested_context=32768),
        tools=SimpleNamespace(shell_enabled=False, web_enabled=False, max_tool_calls=500,
                              tool_allowlist=[], auto_tool_allowlist=False, safe_mode=True,
                              timeout_seconds=30, max_output_kb=64, extra_allowed_commands=[]),
        web=None, auxiliary=None,
        postiz=SimpleNamespace(api_key="", api_url=""),
        mcp=SimpleNamespace(servers={"bauer_db": servidor}),
    )

    # llm_client=False evita construir client de verdade (None dispara o build).
    router = _build_router(cfg, Path("x"), llm_client=False)

    assert getattr(router, "_mcp_config", None) is not None, "router nasceu sem a config MCP"
    assert "bauer_db" in (getattr(router._mcp_config, "servers", None) or {})


def test_contexto_aplicado_chega_ao_ollama():
    """`applied_context` tem que virar `options.num_ctx` na requisição.

    Sem isso o Ollama usa o PRÓPRIO default (bem menor) e trunca o prompt em
    SILÊNCIO — o sintoma é resposta vazia, sem erro nenhum. `bauer chat` e
    `bauer agent` faziam a atribuição (cópia colada em cada um); `bauer serve`
    e `bauer run` calculavam o contexto, exibiam no boot e nunca o enviavam.
    """
    from types import SimpleNamespace

    from bauer.commands._runtime import _apply_ollama_runtime
    from bauer.ollama_client import OllamaClient

    cfg = SimpleNamespace(model=SimpleNamespace(provider="ollama", think=None))
    client = OllamaClient()
    assert client.num_ctx is None

    _apply_ollama_runtime(client, cfg, 32768)
    assert client.num_ctx == 32768


def test_apply_ollama_runtime_e_noop_para_cloud():
    """Provider OpenAI-compat leva o contexto no modelo, não por parâmetro."""
    from types import SimpleNamespace

    from bauer.commands._runtime import _apply_ollama_runtime
    from bauer.ollama_client import OllamaClient

    client = OllamaClient()
    _apply_ollama_runtime(client, SimpleNamespace(model=SimpleNamespace(provider="openrouter")), 32768)
    assert client.num_ctx is None

    _apply_ollama_runtime(client, None, 32768)  # cfg ausente não pode explodir
    assert client.num_ctx is None


def test_todos_os_entrypoints_aplicam_o_contexto():
    """Guarda a classe do bug: `serve` e `run` foram esquecidos por serem cópias.

    Qualquer entrypoint que construa um client Ollama e calcule contexto tem
    que passar pelo helper — se voltar a existir atribuição solta de
    `num_ctx`, é sinal de que a cópia renasceu.
    """
    from pathlib import Path

    raiz = Path(__file__).parent.parent / "bauer"
    entrypoints = ["cli.py", "commands/serve_cmd.py", "commands/run_cmd.py",
                   "commands/agent_cmd.py"]
    faltando = [
        e for e in entrypoints
        if "_apply_ollama_runtime" not in (raiz / e).read_text(encoding="utf-8")
    ]
    assert not faltando, f"entrypoints sem o helper de num_ctx: {faltando}"


def test_none_cfg_is_safe():
    assert _effective_tool_allowlist(None) is None


def test_context_zero_does_not_slim():
    # requested_context não setado (0) → conservador, não arrisca aplicar slim
    assert _effective_tool_allowlist(_cfg(provider="ollama", ctx=0)) is None
