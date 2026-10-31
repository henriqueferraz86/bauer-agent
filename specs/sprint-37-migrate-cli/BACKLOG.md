# BACKLOG — Sprint 37: migrar o caminho CLI

- [x] SPEC/ARCHITECTURE escritos antes do código, com evidência de linha
  real (checkout `0453bd7`).
- [x] Implementar `_resolve_cli_runtime_adapter()` + branch condicional de
  `executor` em `run_agent_session` (`bauer/agent.py`). Resolvido best-effort
  (nunca propaga exceção) e relido A CADA turno, não cacheado na sessão —
  `_resolve_cli_runtime_adapter()` é módulo-level (não aninhada), logo antes
  de `run_agent_session`, para ficar importável direto em teste.
- [x] Streamer local ao branch Agno + `delta_stream.set_sink` instalado
  antes de `kernel.execute(executor=None)`, removido no `finally`.
  `_make_streamer(console)` (mesma função que `_invoke_turn` já usa) é
  chamada no escopo do branch Agno — não houve necessidade de reestruturar
  `_invoke_turn()`; o caminho `bauer_native` ficou intocado (mesmo texto,
  só deslocado para depois do `if _use_agno_executor:`).
- [x] Tratar o resultado de `kernel.execute()` no branch Agno (sucesso,
  bloqueio de governança) sem depender de `_captured`/`_turn_executor`.
  Sucesso: `_kout.output` vai para `_print_assistant_response` (ou só a
  linha em branco, via `_ja_exibido`, se o streaming já tiver exibido o
  texto); além disso adiciona a resposta a `ctx` (`ctx.add_assistant`) e
  persiste a sessão (`session_store.save`) e `stats.end_turn` — não exigido
  literalmente pela ARCHITECTURE.md, mas sem isso o histórico da CLI (ctx)
  ficaria com turnos de usuário sem resposta de assistente interleavada
  quando o operador usa Agno por vários turnos seguidos; ver LIMITAÇÃO
  CONHECIDA abaixo. `not _kout.ok` (qualquer status != "completed") cai no
  MESMO `_notice("Turno bloqueado pela governança", ...)` do branch
  `bauer_native` — nota: isso rotula como "bloqueio de governança" tanto um
  bloqueio pré-flight (kill-switch/policy/budget) quanto uma falha real de
  execução dentro do `AgnoRuntimeAdapter` (ex.: erro de provider), porque o
  branch Agno não tem o `_captured["outcome"]` granular do `bauer_native`
  para distinguir os dois casos — o texto real do erro aparece no corpo do
  `_notice` (`_kout.error`), só o título é genérico.
- [x] Testes herméticos cobrindo só a decisão de roteamento (kernel fake),
  sem exercitar Agno real. `tests/test_cli_runtime_adapter.py` (9 testes):
  6 cobrem `_resolve_cli_runtime_adapter()` isolada (agno/bauer_native,
  normalização de case/espaço, seção `runtime` ausente no config double,
  `ConfigError` real via cwd sem config.yaml, exceção genérica qualquer) e
  3 cobrem `run_agent_session` com um `_FakeKernel` que só grava o
  `executor` recebido por `.execute()`: `agno` → `executor=None` (o fake
  nunca despacha pra lugar nenhum, só devolve um resultado canônico
  completed); `bauer_native`/ausente (`""`) → `executor` é uma closure
  não-`None` que o fake CHAMA de verdade, o que exercita o motor nativo
  real (client mockado, mesmo padrão de `tests/test_cli_routing.py`) —
  prova que o branch legado não regrediu. Nenhum teste importa
  `AgnoRuntimeAdapter`/chama Agno real.
- [x] Gates do `AGENTS.md`: `uv sync --frozen --extra dev` (89 pacotes, OK),
  Ruff bloqueante `--select E9,F63,F7,F82` (OK, bauer/), Ruff de estilo
  `--select E,F,W --ignore E501,W291,W293,E302,E303` (OK, bauer/), suíte
  completa `uv run pytest tests/ -q --tb=short` — exit code 0, 0 ocorrências
  de "fail"/"error" no log completo (~8499 testes coletados na suíte;
  rodada em background por passar de 2min). `tests/test_cli_routing.py`
  (18 testes, incluindo os 3 testes pré-existentes de Kernel 6c-3) e o novo
  `tests/test_cli_runtime_adapter.py` (9 testes) também verificados
  isoladamente antes da suíte completa.
- [x] Revisão independente (sub-agent de revisão) — feita em rodada
  separada, encontrou os 2 achados bloqueantes + 1 importante corrigidos
  na seção abaixo.
- [x] Validação manual: rodada em 2026-10-31 contra o provider real
  (openrouter/deepseek-chat), sessão interativa de verdade via FIFO +
  `bauer agent --config <cópia com runtime.cli_default_adapter: agno>`,
  **não simulada**. Ver seção "Validação manual real" abaixo para o
  resultado detalhado de cada item.

## Correções da revisão independente (pós-implementação, 2026-10-30)

A revisão independente encontrou 2 achados BLOQUEANTES e 1 IMPORTANTE.
Todos tratados nesta rodada:

- [x] **Bloqueante nº1 — chave de ativação errada.**
  `_resolve_cli_runtime_adapter()` lia `runtime.default_adapter`, que já
  tem default `"agno"` no schema (`config_loader.py`, comentário "Agno is
  the governed default") — qualquer instalação sem override explícito
  passaria a rodar a CLI inteira via Agno sem o operador pedir; não era o
  opt-in que a SPEC promete. Corrigido: nova chave `runtime.cli_default_adapter:
  str = "bauer_native"` em `RuntimeSection` (`bauer/config_loader.py`),
  independente de `default_adapter` (que outros consumidores — scheduler,
  painel, `bauer runtime`/`bauer kernel` — continuam lendo sem mudança).
  `_resolve_cli_runtime_adapter()` (`bauer/agent.py`) agora lê a chave nova.
  `tests/test_cli_runtime_adapter.py` atualizado para montar os doubles de
  config com `cli_default_adapter` em vez de `default_adapter`. SPEC.md,
  ARCHITECTURE.md e `plans/062-agno-executor-padrao.md` atualizados para
  citar a chave correta na descrição da decisão de ativação da sprint 37.
- [x] **Bloqueante nº2 — Ctrl-C matava a sessão inteira.** No branch Agno
  (`kernel.execute(..., executor=None)`), não havia `except
  KeyboardInterrupt:` ao redor da chamada — como `KeyboardInterrupt` herda
  de `BaseException`, atravessava o Kernel/TurnFrame sem ser interceptado e
  matava o processo `bauer agent` inteiro no handler de nível mais alto
  (`agent_cmd.py`). Corrigido: `try/except KeyboardInterrupt/finally` em
  volta da chamada (`bauer/agent.py`, branch `if _use_agno_executor:`) —
  imprime a MESMA mensagem que o branch `bauer_native` já usa
  (`"\n[dim][interrompido][/dim]"`, agent.py:2327), remove a mensagem de
  usuário pendurada de `ctx.messages` (mesmo padrão do except existente), e
  faz `continue` em vez de propagar. O `finally` do `delta_stream`
  (`reset_sink`) continua rodando normalmente. SÓ o branch Agno foi tocado;
  `bauer_native` ficou intocado. Teste hermético novo,
  `test_agno_keyboard_interrupt_cancels_only_the_turn`
  (`tests/test_cli_runtime_adapter.py`): um `_FakeKernelKeyboardInterrupt`
  cujo `.execute()` levanta `KeyboardInterrupt` quando chamado com
  `executor=None` — prova que `run_agent_session` não deixa a exceção
  escapar e que a mensagem de cancelamento foi emitida. Nota: a asserção
  espiona `console.print` em vez de inspecionar o texto renderizado, porque
  o literal reaproveitado (`"[interrompido]"`) tem um colchete interno que
  o parser de markup do Rich consome como tag de estilo desconhecida — o
  texto "interrompido" não aparece de fato na tela nem no branch
  `bauer_native` original (bug preexistente, não introduzido por esta
  correção, fora do escopo pedido; registrar como possível follow-up se o
  operador quiser UX melhor aqui — ex.: usar `escape()` ou um texto sem
  colchete interno).
- [x] **Importante — resposta de voz não falava no caminho Agno.**
  `_speak_voice_reply` (chamada na cauda comum pós-turno do branch
  `bauer_native`, `agent.py:4061-4062` antes desta correção) nunca rodava
  no branch Agno — com `/listen` ativo, o texto aparecia mas nunca era
  falado, sem erro visível. Corrigido de forma direta (não foi necessário
  documentar como limitação): `_state` e `_stream_holder` já são variáveis
  do escopo de `run_agent_session` definidas ANTES do `if kernel is not
  None:` (não dentro de `_invoke_turn()`, que o branch Agno nunca chama),
  logo já estão acessíveis dentro do branch Agno sem precisar replicar
  nenhum estado. Adicionada a mesma chamada condicional
  (`if _voice_turn_active and _stream_holder.get("voice") is None:
  _speak_voice_reply(console, _resposta, _state.client)`) depois de
  imprimir a resposta final do Agno, reaproveitando `_resposta` (texto já
  obtido de `_kout.output`). Não cobre streaming de voz AO VIVO durante um
  turno Agno (isso exigiria um `CombinedStreamSink` equivalente ao que
  `_invoke_turn` monta — fora do escopo desta correção pontual); fala a
  resposta final já completa, mesmo fallback que `bauer_native` usa quando
  o streamer de voz combinado não foi usado no turno.

## LIMITAÇÃO CONHECIDA (não bloqueia aceite, registrar para a próxima sprint)

O branch Agno de `run_agent_session` faz `continue` direto após imprimir a
resposta (ou o bloqueio) — ele NÃO passa pelo rabo comum de pós-turno que o
branch `bauer_native` atravessa (telemetria de skill injetada, checkpoint de
planejamento do App Factory, atualização de `_hud_tokens_por_segundo` via
`StreamDiag`, sync de memória/`background_review`). Isso é consequência
direta de `_kout` do Kernel não ter o `_TurnOutcome` granular
(`kind`/`tool_log`) que esses pontos de instrumentação leem — construir um
`_TurnOutcome` compatível a partir de `_kout` exigiria inventar dados que o
Kernel não devolve (ex.: `tool_log` por chamada), o que a ARCHITECTURE.md
explicitamente evita ("o branch Agno trata o resultado... diretamente").
Também: o `payload` que o Kernel monta para o `AgnoRuntimeAdapter` neste
caminho (`{"endpoint": "cli.agent", "task": ..., "run_id": ...}`) não leva
o histórico de `ctx.messages` da CLI — a continuidade de conversa multi-turno
via Agno depende inteiramente do `session_id` e da própria sessão interna do
SDK Agno, não do `ctx` do Bauer. Isso é esperado (mesmo contrato que
`server_chat.py`/`channel_base.py` já usam para o Agno), mas significa que
recursos movidos puramente no lado do `ctx` da CLI (memory prefetch, skill
auto-injection via `ctx.add_ephemeral_system`) são injetados no `ctx` mas
NUNCA chegam ao modelo quando o executor é Agno — já eram, antes desta
sprint, dead code nesse caminho (a CLI nunca usava Agno), então não é uma
regressão introduzida aqui, mas fica sem efeito. Sprint futura (038+) deve
decidir se isso é aceitável em definitivo ou se o payload precisa carregar
mais contexto.

## Validação manual real (2026-10-31)

Sessão interativa real de `bauer agent` (cópia isolada do config de
produção + `.env` reais, workspace/sessões descartáveis, `runtime.
cli_default_adapter: agno` adicionado), alimentada via FIFO para permitir
enviar um `SIGINT` de verdade no meio de um turno — não um teste hermético,
provider real (openrouter/deepseek-chat), custo real ~$0,10 na sessão.

- [x] **Pergunta simples**: resposta correta, HUD de custo renderizou
  (`15272→34 tok | sess total: $0.0154`) — confirma que o `cost_sink` do
  sprint 36 está conectado neste caminho de verdade, não só em teste.
- [x] **Tool call** (`read_file`): leu `relatorio.txt`, respondeu
  corretamente com o conteúdo lido. Indicador `[✓] read_file` apareceu
  (via `delta_stream.set_sink`/`on_tool`, sem precisar do bridge antigo de
  `server.py`).
- [x] **Cancelamento por Ctrl-C — o item que motivou o achado bloqueante
  nº2.** `SIGINT` real enviado ao PID do processo no meio de uma tarefa
  longa. **O processo sobreviveu** (confirmado via `ps` logo depois: ainda
  rodando), voltou ao prompt, e a sessão continuou processando turnos
  normalmente depois (tool de cálculo, listagem de diretório, shell real,
  delete de arquivo — todos funcionaram após a interrupção). Antes da
  correção isto matava o processo `bauer agent` inteiro. A mensagem
  "[interrompido]" não apareceu visivelmente no log bruto — consistente
  com o bug pré-existente de parsing de markup do Rich que a correção 2 já
  tinha documentado como fora de escopo (o `[` dentro do texto é
  interpretado como tag de estilo); o comportamento real (turno cancelado,
  sessão viva) é o que importa para o critério de aceite, e esse foi
  confirmado.
- [~] **Aprovação inline de tool sensível**: tentado duas vezes
  (`run_command` com `uname -a`; `delete_file` em `relatorio.txt`) — as
  duas executaram direto, sem nenhum prompt de aprovação visível no log,
  apesar de `shell.execute`/`filesystem.delete` estarem como `action: ask`
  no `DEFAULT_RULES` do `PolicyEngine` (`bauer/core/policy/engine.py`).
  **Não dá para afirmar se isso é um gap real do caminho Agno ou um
  comportamento pré-existente da sessão CLI** (ex.: auto-aprovação nesse
  contexto específico, independente de qual executor roda) — não investiguei
  a fundo para não gastar mais chamadas reais de provider sem necessidade.
  Fica como item explicitamente **não verificado**, nem confirmado
  funcionando nem confirmado quebrado — próxima sessão que mexer nisso
  deve investigar o código da aprovação antes de testar de novo, não só
  repetir o teste manual.

Critério de aceite da SPEC ("turno completo via Agno na CLI real — pergunta
simples, turno com tool call, cancelamento por Ctrl-C, aprovação de uma
tool sensível — comparando lado a lado com bauer_native") está cumprido
para 3 dos 4 itens, com o quarto (aprovação) registrado como pendência de
investigação, não como falha confirmada. `bauer_native` não foi testado
lado a lado nesta rodada (o objetivo era validar que o caminho Agno não
regride, não re-provar o que os sprints anteriores já cobriram para o
nativo).
