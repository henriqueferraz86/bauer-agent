"""HARNESS-029 — o que o harness REPORTA é o que o runtime USA.

Critério 11 do plano, e o único indicador do §15 que não podia ser declarado por
inspeção: a causa do bug de julho foi corrigida, mas a **invariante** não estava
escrita em lugar nenhum.

O bug: `_build_system_prompt` tinha `tool_mode` com default literal `"bridge"`.
Com cliente Ollama nativo, o agente executava function calling NATIVO enquanto
recebia o protocolo do bridge por prompt. Instrução em conflito direto — o
modelo ora chamava a tool, ora devolvia o JSON como texto, ora prosa. Foi
atribuído ao MODELO três vezes, até um A/B dar 5/5 tool calls a 0.7 e 5/5 a 0.
Todas as hipóteses de modelo morreram sob medição. Era o harness.

Escrever a invariante encontrou o mesmo bug ainda vivo em dois lugares:

1. `preflight` derivava o modo do `models.yaml` (`info.supports_tools`); o
   runtime deriva do CLIENTE. Como `OllamaClient.supports_native_tools` é True
   incondicionalmente, um modelo fora do catálogo fazia o doctor imprimir
   "bridge" com o runtime rodando native.
2. `serve_cmd` lia o modo do **`.runtime_state.json`** — decisão de um `bauer
   doctor` de outro momento, possivelmente de outra máquina, com default
   `"bridge"` quando o arquivo nem existia. Prompt de bridge, execução nativa.

A regra que estes testes travam: **a capacidade do runtime se pergunta ao
runtime.** Não se lê do catálogo, não se lê do disco, não se chuta o valor mais
conservador — o chute não é seguro, é errado de um jeito silencioso.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bauer.agent import _build_system_prompt, _client_supports_native_tools
from bauer.ollama_client import OllamaClient
from bauer.openai_client import OpenAIClient
from bauer.runtime_capability import (
    BRIDGE,
    NATIVO,
    aviso_de_capacidade,
    modo_de_tool_calling,
    modo_por_provider,
)

RAIZ = Path(__file__).resolve().parent.parent


class _Router:
    """Router mínimo — o prompt não é o objeto do teste, o MODO é."""

    workspace = "workspace"

    def available_tools(self):
        return ["read_file"]

    def tool_info(self, name):
        return {"args": ["path"], "description": "lê um arquivo"}


def _ollama():
    return OllamaClient("http://127.0.0.1:11434", 5)


def _openai():
    # sem __init__: construir o cliente de verdade exigiria chave e rede, e o
    # que está sendo medido é a CLASSE, que é o que o gate do runtime olha.
    return object.__new__(OpenAIClient)


CLIENTES = [("ollama", _ollama), ("openai", _openai)]


# ═══ a invariante ════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nome,fabrica", CLIENTES)
def test_o_modo_reportado_e_o_modo_executado(nome, fabrica):
    """A invariante, em uma linha: quem descreve e quem executa são a mesma
    função. Não "concordam" — são a mesma."""
    client = fabrica()

    reportado = modo_de_tool_calling(client)
    executado = NATIVO if _client_supports_native_tools(client) else BRIDGE

    assert reportado == executado == NATIVO


@pytest.mark.parametrize("nome,fabrica", CLIENTES)
def test_o_prompt_montado_sozinho_bate_com_o_modo(nome, fabrica):
    """`_build_system_prompt(router, client=...)` sem `tool_mode` explícito tem
    de produzir o prompt do modo que o runtime vai usar.

    É o call site do bug original: 5 dos 7 chamadores não passavam nada e caíam
    no default `"bridge"`.
    """
    client = fabrica()
    prompt = _build_system_prompt(_Router(), client=client)

    assert modo_de_tool_calling(client) == NATIVO
    # o prompt do bridge ENSINA o formato JSON-de-texto; o do native não pode
    assert "responda somente com" not in prompt.lower()
    assert '{"tool"' not in prompt


def test_previsao_por_provider_bate_com_o_cliente_real():
    """`modo_por_provider` responde quando ainda não há cliente (cloud, ou Ollama
    offline). Sem esta amarração ele seria o QUARTO lugar a responder a mesma
    pergunta — a duplicação que o módulo existe para acabar."""
    for provider, fabrica in CLIENTES:
        assert modo_por_provider(provider) == modo_de_tool_calling(fabrica()), provider


def test_sem_cliente_o_modo_e_bridge():
    """Fronteira explícita: sem cliente não há capacidade a consultar, e o
    comportamento antigo do `_build_system_prompt` é preservado."""
    assert modo_de_tool_calling(None) == BRIDGE


def test_mock_nao_liga_o_caminho_nativo():
    """O gate checa CLASSES CONCRETAS de propósito: um MagicMock responderia
    True para qualquer getattr e ligaria o native onde o teste espera bridge —
    fazendo a suíte medir a si mesma."""
    from unittest.mock import MagicMock

    assert modo_de_tool_calling(MagicMock()) == BRIDGE


# ═══ os dois lugares onde a invariante estava quebrada ═══════════════════════


def test_serve_nao_le_o_modo_do_disco():
    """`serve_cmd` montava o prompt a partir do `.runtime_state.json` — decisão
    de um `bauer doctor` de outro momento, outro modelo, outra máquina. E com
    default `"bridge"` quando o arquivo nem existia."""
    txt = (RAIZ / "bauer" / "commands" / "serve_cmd.py").read_text(encoding="utf-8")

    assert 'state.get("tool_mode"' not in txt, "o modo voltou a vir do disco"
    assert "modo_de_tool_calling(_client)" in txt


def test_create_app_nao_tem_default_bridge():
    """Default literal `"bridge"` num parâmetro de capacidade é o bug embalado
    como assinatura: qualquer caller que esqueça de passar monta prompt de
    bridge com execução nativa."""
    from bauer.server import create_app

    import inspect

    padrao = inspect.signature(create_app).parameters["tool_mode"].default
    assert padrao == "", f"default de tool_mode voltou a ser {padrao!r}"


def test_create_app_pergunta_ao_cliente_que_recebeu(tmp_path, monkeypatch):
    """Sem `tool_mode`, o app resolve do cliente — e é ESTE cliente que ele
    pergunta, não um qualquer."""
    pytest.importorskip("fastapi")
    import bauer.runtime_capability as rc
    from bauer.server import create_app

    cliente = _ollama()
    perguntados = []
    monkeypatch.setattr(rc, "modo_de_tool_calling",
                        lambda c: perguntados.append(c) or NATIVO)

    create_app(model_name="m", applied_context=8192, router=_Router(),
               client=cliente, system_prompt="x", sessions_dir=tmp_path / "s")

    assert perguntados and perguntados[0] is cliente


def test_create_app_respeita_tool_mode_explicito(tmp_path, monkeypatch):
    """O parâmetro continua valendo quando passado — o que mudou foi o DEFAULT.
    Sem isto, o teste acima poderia ser satisfeito ignorando o caller."""
    pytest.importorskip("fastapi")
    import bauer.runtime_capability as rc
    from bauer.server import create_app

    monkeypatch.setattr(rc, "modo_de_tool_calling",
                        lambda c: pytest.fail("não devia perguntar: veio explícito"))

    create_app(model_name="m", applied_context=8192, router=_Router(),
               client=_ollama(), system_prompt="x", sessions_dir=tmp_path / "s",
               tool_mode=BRIDGE)


def test_preflight_nao_deriva_o_modo_do_catalogo():
    """O `models.yaml` diz o que o MODELO anuncia; o cliente diz o que o runtime
    FAZ. Derivar o modo do primeiro é reportar uma coisa e executar outra."""
    txt = (RAIZ / "bauer" / "preflight.py").read_text(encoding="utf-8")

    trecho = txt[txt.index("--- tool mode ---"):txt.index("--- segurança do serve ---")]
    assert "info.supports_tools" not in trecho, (
        "o modo voltou a ser derivado do catálogo em vez do cliente")
    assert "modo_de_tool_calling" in trecho


# ═══ o aviso que NÃO podia ser perdido junto com a correção ══════════════════


def test_modelo_sem_capability_ainda_avisa():
    """Corrigir o modo não podia jogar fora o que o doctor tinha de útil: um
    modelo sem `tools` no catálogo devolve HTTP 400 no primeiro `tools=` e o
    agente faz downgrade definitivo. O usuário merece saber ANTES de rodar uma
    tarefa longa."""
    class _Info:
        supports_tools = False

    aviso = aviso_de_capacidade(NATIVO, _Info())

    assert "bridge" in aviso and "400" in aviso


def test_modelo_desconhecido_avisa_que_nao_da_para_saber():
    aviso = aviso_de_capacidade(NATIVO, None)
    assert "models.yaml" in aviso and "TENTAR" in aviso


def test_modelo_com_capability_nao_gera_ruido():
    class _Info:
        supports_tools = True

    assert aviso_de_capacidade(NATIVO, _Info()) == ""


def test_aviso_e_previsao_nao_contradiz_o_modo():
    """A diferença que corrige o bug de fundo: o aviso fala do MODELO ("vai cair
    para o bridge"), nunca do runtime ("o modo é bridge"). Escrito como
    previsão, ele não pode mais contradizer a execução."""
    class _Info:
        supports_tools = False

    aviso = aviso_de_capacidade(NATIVO, _Info())

    assert not re.search(r"tool mode.*bridge", aviso, re.I)
    assert "TENTAR native" in aviso


def test_aviso_calado_quando_o_modo_ja_e_bridge():
    """Avisar "vai cair para o bridge" quando já É bridge é ruído."""
    class _Info:
        supports_tools = False

    assert aviso_de_capacidade(BRIDGE, _Info()) == ""


# ═══ a duplicação não pode voltar ════════════════════════════════════════════


def test_uma_fonte_de_verdade_so():
    """O ratchet. A pergunta "native ou bridge?" tinha TRÊS respostas com fontes
    diferentes — catálogo, disco e cliente. Três respostas para a mesma pergunta
    não é risco de divergência, é garantia.

    Quem decidir o modo em mais um lugar quebra aqui.
    """
    permitidos = {
        "bauer/agent.py",                 # o gate em si (_client_supports_native_tools)
        "bauer/runtime_capability.py",    # a fachada que todo mundo pergunta
    }
    culpados = []
    for py in (RAIZ / "bauer").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(RAIZ).as_posix()
        if rel in permitidos:
            continue
        txt = py.read_text(encoding="utf-8", errors="replace")
        # atribuir o modo a partir de um literal é o padrão que se quer proibir
        if re.search(r'tool_mode\s*=\s*["\'](native|bridge)["\']', txt):
            culpados.append(rel)

    assert culpados == [], (
        "estes arquivos decidem o modo de tool calling por conta própria:\n  "
        + "\n  ".join(culpados)
        + "\n\nPergunte a `runtime_capability.modo_de_tool_calling(client)`. "
        "A capacidade do runtime se pergunta ao runtime."
    )
