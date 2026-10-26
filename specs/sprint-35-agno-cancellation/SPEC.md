# SPEC — Sprint 35: cancelamento no AgnoRuntimeAdapter

Plano 062. O spike é a primeira tarefa desta sprint; a entrega também adiciona
checkpoints e preserva o resultado terminal quando o Agno encerra por
cancelamento.

## Problema

O adapter atual instala contexto de sessão ao iterar `Agent.run()` e
`Team.run()`, mas a paridade de cancelamento depende de o `ExecutionControl`
continuar visível durante eventos e chamadas às tools do Bauer. Uma mudança
interna de thread/task do SDK poderia quebrar essa garantia sem erro evidente.

## Objetivo e escopo

- Reproduzir com um probe mínimo a visibilidade de um `ContextVar` no caminho
  síncrono do Agno usado por `AgnoRuntimeAdapter`.
- Inspecionar e registrar a fronteira relevante de `Agent.run()` e `Team.run()`
  na versão exata fixada pelo `uv.lock`, incluindo dispatch, stream e tools.
- Distinguir evidência sobre o caminho síncrono atual de qualquer garantia para
  `arun()`, execução concorrente de membros ou futuras versões do SDK.
- Adicionar checkpoints antes e depois de avançar cada stream event do Agno,
  impedindo novos passos após cancelamento e cobrindo conclusão silenciosa do
  stream.
- Mapear cancelamento da tool router para o sinal de cancelamento que o Agno
  reconhece, preservando `run.cancelled` até `run_agent`/`run_team`.

Fora de escopo: mudar o executor padrão, migrar canais, alterar o SDK,
implementar retry/fallback, interromper à força uma chamada LLM/tool já em voo,
ou criar monkeypatches de produção.

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy` (seguir o padrão hermético do repositório; não chamar provider)

## Sub-agents recomendados

- `agno-spike-reviewer`: revisar a fronteira sync/async/thread no SDK instalado.
- `cancellation-reviewer`: conferir se a conclusão preserva as garantias do
  `ExecutionControl` e não trata cancelamento como resposta normal de tool.

## Requisitos

1. Probe não acessa rede, provider real, Telegram, nem configuração do operador.
2. O probe verifica o mesmo contexto que `_bauer_tool_callable` lê no adapter.
3. O resultado identifica a versão exata do Agno e caminhos examinados.
4. Uma interrupção durante tool call não pode virar resposta normal de
   ferramenta que permita ao Agno continuar.
5. Cancelamento após evento, durante tool e em Team não pode resultar em
   `run.completed`.
6. Não descartar métricas que o adapter já reportar. Agno usage/cost wiring é
   Sprint 36 e precisa passar antes da primeira migração de canal.
7. Se qualquer trecho necessário executar em thread sem propagação demonstrada,
   ou depender de API privada/monkeypatch, registrar STOP; não contornar por
   suposição.
8. Registrar limitações da evidência e a decisão de avanço no backlog.

## Critérios de aceite

- Há evidência reproduzível de que o contexto do adapter chega a uma chamada
  de tool do Agent síncrono; teste controlado cobre cancelamento do stream do
  Team.
- A inspeção do SDK confirma que os streams sync de Agent e Team não movem a
  execução de tools para outra thread/task.
- Cancelamento em fronteira de stream, chamada de tool e stream de Team produz
  estado `cancelled`, sem um novo LLM/tool após o sinal.
- Async (`arun`) e outras execuções concorrentes ficam explicitamente fora da
  garantia se não forem os caminhos chamados pelo adapter.
- Backlog registra uma decisão inequívoca: avançar para implementação suportada
  ou interromper a Sprint 35 segundo o STOP do plano 062.

## Riscos

- A versão do Agno pode mudar e alterar a fronteira observada; repetir o spike
  ao atualizar a dependência.
- Um fake inadequado pode provar apenas o wrapper do Bauer, não o comportamento
  do SDK. Combinar probe dinâmico sem rede com leitura do código instalado.
- Context propagation não interrompe uma chamada LLM ou tool já bloqueada; o
  contrato de cancelamento só bloqueia novos passos após o checkpoint.

## Plano de validação

1. Confirmar versão com `uv run python` no ambiente frozen e consultar
   `uv.lock`.
2. Executar probe isolado com Agent/Team ou o menor seam público suportado,
   sem modelo real e sem rede.
3. Inspecionar `run_dispatch`, iteração dos streams e despacho de tools na
   versão instalada; citar caminhos e linhas no resultado.
4. Rodar a validação focada da sprint após implementação; executar os gates
   completos do `AGENTS.md` antes de avançar para a Sprint 36.
