# SPEC — Sprint 37: migrar o caminho CLI para Agno (plano 062)

Sprints 35 (cancelamento) e 36 (streaming/custo/contexto) entregues e
fechados. Esta sprint torna `bauer` CLI capaz de rodar turnos via Agno,
condicionado a config/flag, mantendo `bauer_native` como default.

## Problema

A CLI (`bauer/agent.py`, REPL interativo) sempre passa um `executor`
explícito (`_turn_executor`, linha ~3886) para `kernel.execute()`
(linha ~3916) — nunca deixa `executor=None`, então o Kernel nunca consulta
`adapter_factory`/Agno para o caminho interativo.

CORREÇÃO (revisão independente, achado bloqueante nº1): a chave de ativação
usada originalmente aqui era `runtime.default_adapter`, que já tem default
`"agno"` no schema (`config_loader.py`, "Agno is the governed default").
Ler essa chave direto faria qualquer instalação SEM override explícito no
`config.yaml` rodar a CLI inteira via Agno sem o operador pedir — não é o
opt-in que esta SPEC promete, e `default_adapter` já é lido por outros
consumidores (scheduler, painel, `bauer runtime`/`bauer kernel`) com seu
próprio contrato. A decisão corrigida usa uma chave PRÓPRIA da CLI,
`runtime.cli_default_adapter`, com default `"bauer_native"` — ver "Decisão
de ativação" abaixo.

## Evidência (checkout `0453bd7`, branch `codex/sprint-37-migrate-cli`)

- `_turn_executor` é uma closure que chama `_invoke_turn()` — que por sua vez
  chama `_run_tool_loop_body(...)`, o motor real do turno `bauer_native`
  (`agent.py:3793-3820`).
- O HUD vivo (`_quadro_do_turno`/`TurnFrame`, `agent.py:1177-1205`) abre
  **ao redor de `kernel.execute()` inteiro**, não dentro do executor — isso
  já é agnóstico de qual executor roda dentro.
- O streaming ao vivo usa `ConsoleStreamRenderer`
  (`bauer/ui_stream.py:57`), criado por `_make_streamer(console)`
  (`agent.py:1151`) e passado por parâmetro `streamer=` a
  `_run_tool_loop_body` — **não** via o `ContextVar` de
  `bauer/delta_stream.py`.
- `ConsoleStreamRenderer` já implementa `on_round`, `on_delta`, `on_tool`
  (`ui_stream.py:93,103,130`) — **o mesmo protocolo de sink que
  `delta_stream.py` documenta e que `AgnoRuntimeAdapter.stream_agent` já
  emite desde o sprint 36** (`emit_delta`, `agno_adapter.py`). Isso significa
  que a ponte para paridade de streaming é instalar
  `delta_stream.set_sink(_streamer)` antes de despachar pelo Agno — não
  criar um renderer novo.
- Cancelamento por Ctrl-C hoje **não** usa `ExecutionControl`/
  `checkpoint_execution()` (o mecanismo cooperativo dos sprints 34/35) — usa
  o `KeyboardInterrupt` padrão do Python, capturado por um `except
  KeyboardInterrupt:` solto no corpo do turno (`agent.py:2326`,
  `agent.py:3227`, `agent.py:3242`). Como `KeyboardInterrupt` herda de
  `BaseException`, não de `Exception`, o `except Exception as exc:` que
  `AgnoRuntimeAdapter.stream_agent` usa para normalizar cancelamento
  (`agno_adapter.py`, bloco try/except em volta do loop de eventos) **não
  intercepta** um Ctrl-C — ele deve propagar intacto até o mesmo `except
  KeyboardInterrupt:` que já existe na CLI hoje. Isto precisa de
  confirmação empírica (teste manual registrado), não é garantido só por
  leitura de código — SDKs de terceiros às vezes engolem `BaseException`
  em loops internos.
- Aprovação inline de tool: chamadas de ferramenta do Agno já passam pelo
  mesmo `ToolRouter`/`_bauer_tool_callable` (confirmado no sprint 35) — o
  fluxo de aprovação não deveria divergir por executor, mas precisa de
  confirmação manual (prompt interativo aparecendo igual).

## Objetivo e escopo

1. Tornar `executor` condicional: quando a config/flag resolver Agno como
   executor da CLI, chamar `kernel.execute(..., executor=None)` para que o
   Kernel dispache via `adapter_factory`; senão, manter `executor=_turn_executor`
   (comportamento atual, inalterado).
2. Quando o executor resolvido for Agno, instalar `delta_stream.set_sink`
   com o MESMO `_streamer`/`TurnFrame` que `_turn_executor` já usa — sem
   criar um segundo caminho de renderização.
3. Validação manual registrada (não hermética): turno completo via Agno na
   CLI real — pergunta simples, turno com tool call, cancelamento por
   Ctrl-C, aprovação de uma tool sensível — comparando lado a lado com
   `bauer_native`.
4. Suíte hermética cobrindo só o NOVO branch de decisão (qual valor de
   `executor` é passado a `kernel.execute` dado cada config/flag) — sem
   exercitar Agno real.

## Decisão de ativação (config vs flag)

Seguir o padrão já usado no sprint 36 para `/stream` (`_runtime_mode()`
independente de `runtime.default_adapter` estático) seria inconsistente —
aqui a decisão é por processo CLI, não por servidor de longa duração,
então **config é suficiente**: ler uma chave PRÓPRIA da CLI,
`runtime.cli_default_adapter` (já exposta via `cfg.runtime.cli_default_adapter`,
default `"bauer_native"` no schema). NÃO ler `runtime.default_adapter` para
esta decisão — essa chave já tem default `"agno"` e já é o contrato de
outros consumidores (scheduler, painel, `bauer runtime`/`bauer kernel`);
reaproveitá-la para a CLI faria qualquer instalação sem override explícito
rodar a CLI inteira via Agno sem o operador pedir, o que não é o opt-in que
esta SPEC promete (achado bloqueante nº1 da revisão independente, corrigido
nesta sprint). Não introduzir uma flag `--runtime` nesta sprint: escopo
mínimo, uma fonte de verdade. Se o operador quiser testar sem editar o
config, pode alternar o valor e reiniciar — não é um caminho de uso
frequente o bastante para justificar uma segunda superfície de configuração
agora.

## Fora de escopo

- `--runtime` como flag de linha de comando (pode virar sprint futura se o
  operador pedir).
- Migrar `server_chat.py`/web (sprint 38), OpenAI-compat (sprint 39),
  Telegram (sprint 40).
- Qualquer mudança em `AgnoRuntimeAdapter`/`delta_stream.py` em si — já
  prontos desde os sprints 35-36.

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy` (suíte hermética, sem provider real, sem Agno real na
  suíte padrão)

## Critérios de aceite

- `bauer` CLI com `runtime.cli_default_adapter: agno` no config completa um
  turno de ponta a ponta (pergunta simples, tool call, cancelamento por
  Ctrl-C, aprovação) em teste manual registrado — paridade observável com
  `bauer_native`.
- Suíte hermética cobre os dois branches de decisão (`executor=None` vs
  `executor=_turn_executor`) usando fake/stub, sem chamar Agno real.
- `bauer_native` continua sendo o comportamento quando
  `runtime.cli_default_adapter` não for `"agno"` (inclusive o default
  `"bauer_native"`, e independente do valor de `runtime.default_adapter`) —
  nenhuma regressão no caminho hoje em produção.
- Suíte completa + Ruff bloqueante/estilo verdes.

## STOP

Interromper e voltar ao operador se:
- Ctrl-C durante um turno Agno não for capturado pelo `except
  KeyboardInterrupt:` existente (ex.: o SDK engole `BaseException`
  internamente) — sem solução identificada na hora.
- O fluxo de aprovação inline divergir visivelmente entre os dois
  executores sem causa raiz clara.
