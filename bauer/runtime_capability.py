"""HARNESS-029 — o que o harness REPORTA tem de ser o que o runtime USA.

O critério 11 do plano: o harness conhece as capacidades do runtime — modo de
tool calling, janela real, residência em GPU — e isso é **invariante testada**,
não inferência espalhada.

A justificativa é um bug real, e caro. `_build_system_prompt` tinha `tool_mode`
com default literal `"bridge"`: com cliente Ollama nativo, o agente executava
function calling NATIVO enquanto recebia o protocolo do bridge por prompt.
Instrução em conflito direto — o modelo ora chamava a tool, ora devolvia o JSON
como texto, ora prosa. O comportamento foi atribuído ao MODELO três vezes
(modelo travado, mismatch de renderer, Ollama velho, temperatura) até um A/B dar
5/5 tool calls a 0.7 e 5/5 a 0. Todas as hipóteses de modelo morreram sob
medição. Era o harness.

Este módulo existe porque a decisão estava **duplicada em três lugares** com
fontes de verdade diferentes:

    agent._client_supports_native_tools   classe concreta do cliente VIVO
    preflight.run_doctor                  catálogo `models.yaml` (supports_tools)
    serve_cmd                             `.runtime_state.json` gravado em DISCO

Três respostas para a mesma pergunta é uma garantia de divergência, não um
risco. E divergiram: `OllamaClient.supports_native_tools` é True
incondicionalmente (o `/api/chat` aceita `tools=` desde a 0.3), então para um
modelo fora do `models.yaml` o doctor imprimia "bridge" e o runtime rodava
native — e o `bauer serve` construía o prompt a partir desse "bridge" de disco,
reproduzindo o bug de julho inteiro.

Aqui a pergunta tem **uma** resposta, derivada do cliente vivo. O sinal do
catálogo continua útil e continua sendo dado — mas como AVISO separado
(:func:`aviso_de_capacidade`), não como o modo. A diferença: um avisa que o
modelo provavelmente vai cair para o bridge na primeira chamada; o outro diz o
que o runtime vai fazer. Fundir os dois foi o erro.
"""

from __future__ import annotations

from typing import Any

#: O que o `/api/chat` e o `/v1/chat/completions` fazem com `tools=`.
NATIVO = "native"
#: Tool calling ensinado por prompt, com o JSON parseado da resposta.
BRIDGE = "bridge"


def modo_de_tool_calling(client: Any) -> str:
    """O modo que o runtime VAI usar com este cliente. Única fonte de verdade.

    Delega para o mesmo gate que o laço de turno consulta antes de decidir entre
    ``chat_with_tools`` e o bridge por prompt — não reimplementa a regra. Sem
    cliente, ``bridge``: é o que o ``_build_system_prompt`` faz há sempre, e
    mudar isso aqui alteraria o comportamento de quem não tem cliente em vez de
    apenas descrevê-lo.
    """
    if client is None:
        return BRIDGE
    from .agent import _client_supports_native_tools

    return NATIVO if _client_supports_native_tools(client) else BRIDGE


def modo_por_provider(provider: str) -> str:
    """O modo que o cliente DESTE provider vai produzir, sem construí-lo.

    Existe para o `doctor` responder quando ainda não há cliente vivo — provider
    cloud (não consulta o Ollama) ou Ollama offline. É PREVISÃO, e por isso a
    invariante que a cobre é diferente: o teste constrói o cliente real de cada
    provider e exige que ``modo_de_tool_calling`` dele bata com o previsto aqui.
    Sem essa amarração, este seria o quarto lugar a responder a mesma pergunta —
    a duplicação que este módulo existe para acabar.

    Ollama offline reporta ``native``: quando voltar, é o que ele vai fazer. O
    que impede o run naquele momento é o Ollama estar fora, e isso já tem campo
    próprio no ``RuntimeState`` (``ollama_alive``).
    """
    return NATIVO if (provider or "").strip() else BRIDGE


def aviso_de_capacidade(modo: str, info: Any) -> str:
    """Aviso quando o modo REAL é native mas o modelo não anuncia a capability.

    "" quando não há o que avisar. Isto é o que o doctor tinha de valioso na
    versão antiga e que não podia ser perdido junto com a correção: um modelo
    sem ``tools`` no catálogo devolve HTTP 400 no primeiro ``tools=``, o agente
    faz downgrade definitivo para o bridge, e o usuário merece saber disso ANTES
    de rodar uma tarefa longa.

    O que muda é o STATUS da informação: previsão sobre o modelo, não descrição
    do runtime. Escrita como previsão, ela não pode mais contradizer a execução.
    """
    if modo != NATIVO:
        return ""
    if info is None:
        return ("O modelo não está no models.yaml e não foi possível auto-detectar: "
                "não dá para saber se ele aceita tools nativas. O runtime vai TENTAR "
                "native e cair para o bridge se vier HTTP 400.")
    if getattr(info, "supports_tools", None) is False:
        return ("O modelo não declara a capability 'tools' no models.yaml. O runtime "
                "vai TENTAR native e cair para o bridge no primeiro HTTP 400 — "
                "modelos menores seguem mal o bridge por prompt em tarefas "
                "multi-passo; prefira um modelo com a capability.")
    return ""
