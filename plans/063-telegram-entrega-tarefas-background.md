# Plano 063 — Entrega de resultado de tarefas em background no Telegram

**Status:** Sprint 43 entregue, testada e verificada (gates completos
verdes). Ver "Entregue em" ao final do documento.

**Prioridade:** P1

**Esforço total estimado:** L, dividido em sprints independentes

**Dependência inicial:** nenhuma bloqueante — reutiliza contratos do plano
061 (projeto ativo, atividade dos agentes, cancelamento) e não conflita com
o plano 062 (Agno como executor padrão); se 062 avançar primeiro, os hooks
de entrega aqui descritos devem valer para os dois executores (`bauer_native`
e `agno`).
**Referências:** achados da sessão de 2026-10-26 (ver "Evidência e
baseline"); arquitetura de entrega assíncrona do
[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)
(`gateway/wake.py`, `gateway/run_notifications.py`,
`cron/scheduler_delivery.py`, `tools/async_delegation.py`), usada como
referência de design, não copiada literalmente — Bauer não tem (e este plano
não introduz) cron/delegação distribuída multi-processo.

## Problema e objetivo

No Telegram, o usuário consegue **iniciar** uma tarefa que roda fora do
turno corrente (hoje, na prática, só via `run_command(..., background=true)`),
mas o Bauer nunca avisa quando ela termina. O bot responde "iniciado, PID X,
acompanhe com `process(action='poll')`" e para aí — a única forma de saber o
resultado é o humano lembrar de perguntar de novo, e mesmo assim só funciona
se o LLM decidir chamar `process(poll)` naquele turno seguinte. Não existe
nenhum callback, watcher ou push: o resultado fica soterrado no buffer do
`_processes` registry até alguém pedir.

Objetivo: quando uma tarefa iniciada em background terminar, o Bauer manda
uma mensagem nova (ou edita a mensagem "iniciado...", quando ainda fizer
sentido) na MESMA conversa do Telegram que a disparou — sem o usuário
precisar perguntar, com idempotência (nunca entrega duas vezes) e retry
curto em caso de falha transitória de rede com a API do Telegram.

Fora de escopo deste plano:
- `/loop` / autonomia contínua para Telegram (etapa 5 do plano 061, ainda não
  iniciada — este plano não a implementa, mas o hook de entrega aqui criado
  deve ser reutilizável por ela quando for feita).
- `autonomy_delegate` (`/autonomy/delegate` da Desktop API): é uma feature
  só-web hoje, sem noção de `chat_id` de origem; ligá-la ao Telegram é
  trabalho futuro, não coberto aqui.
- Delegação distribuída multi-processo/multi-gateway (Hermes resolve isso
  porque tem múltiplos workers de cron concorrentes; Bauer roda um único
  processo de gateway por instalação — não precisamos da fila durável nem do
  claim distribuído que o Hermes usa para esse caso).

## Evidência e baseline

Verificado no checkout atual (branch `codex/sprint-34-telegram-cancellation`,
plano 061 com sprints 33-34 entregues):

- `bauer/tools/execution.py` (`_run_command`, modo `background=true`, G17.3):
  cria um `controlled_popen` detached, registra em `self._processes[pid_str]`
  com `proc`, `owner`, `label`, `command`, buffers de stdout/stderr lidos por
  threads daemon, e retorna ao usuário só o texto
  `"[run_command background] PID {pid}: ... Acompanhe com process(action=
  'poll'|'log', pid=...)"`. Não existe nenhum callback de conclusão.
- `bauer/tools/agent_misc.py` (`process`, ações `start|list|poll|log|kill|
  write`): é puramente poll-based — o chamador (o LLM, dentro de um turno)
  tem que decidir ativamente perguntar. Não há watcher nem hook de "quando
  terminar, avise".
- `bauer/tools/execution.py` (`_delegate_task`): é **síncrono** — bloqueia
  dentro da própria chamada de tool (até 600s), e o resultado volta pelo
  caminho normal do turno/tool-loop. Não é o mecanismo com o gap; não precisa
  de mudança.
- `bauer/desktop_api.py` (`autonomy_delegate`, rota `/autonomy/delegate`):
  cria uma `DelegationRecord` com `run_id`/workspace isolado via
  `create_worktree_for_delegation`, sem qualquer campo de `chat_id`/origem de
  canal. É feature só da Desktop API/painel web hoje; não tem fio nenhum até
  o Telegram.
- `bauer/channel_base.py`: o mecanismo de `send_fn`/fila existente
  (`queue.append((msg, text, client, model, on_delta, send_fn))`, entrega
  tardia quando `batch.timed_out and send_fn is not None`) resolve um
  problema diferente — mensagens que chegaram enquanto o bot processava o
  turno anterior NA MESMA sessão síncrona. Não cobre um processo desacoplado
  que termina minutos depois, sem nenhum turno em andamento te esperando.
- `bauer/telegram_bridge.py`: não existe comando `/loop` nem equivalente
  (grep por `"/loop"`, `/delegate`, `command ==` só encontra `/cancelar` e
  `/projetos`). Consistente com a etapa 5 do plano 061 ainda não iniciada.
- Pesquisa de arquitetura em `NousResearch/hermes-agent` (sessão de
  2026-10-26, via `gh api search/code` + leitura de arquivo real — WebFetch
  deu resultados inconsistentes na árvore grande do repo e foi descartado
  como fonte):
  - Entrega é **push**, via `deliver_wake()` (`gateway/wake.py`), que injeta
    um `MessageEvent(internal=True)` sintético no mesmo caminho de uma
    mensagem real (`adapter.handle_message`) — reaproveita toda a máquina
    normal de turno/histórico em vez de ter um caminho de entrega paralelo.
  - Origem (`chat_id`/plataforma/thread) é capturada **no momento em que a
    tarefa é criada**, não resolvida depois (`_resolve_origin`,
    `cron/scheduler_delivery.py:70-84`).
  - Retry com backoff fixo curto (`2.0, 5.0, 10.0` segundos,
    `gateway/wake.py:25`); só desiste em erro permanente (sessão não existe,
    payload inválido) — tudo o resto (fila cheia, sessão ocupada) é tratado
    como transitório e tenta de novo.
  - Idempotência via "claim" antes de entregar (`claim_completion_delivery`/
    `release`/`drop`/`complete`, `tools/async_delegation.py` +
    `gateway/run_notifications.py:1046-1207`) — sobrevive a restart do
    processo.
  - UX: edita a mensagem "digitando..."/anterior em lugar quando ela ainda
    está "viva" (`adapter.edit_message(..., finalize=True)`); manda mensagem
    **nova com cabeçalho explícito** de contexto quando a entrega é tardia e
    não há mensagem para editar (`cron/scheduler_delivery.py:1814-1822` —
    template com nome da tarefa, job_id e instrução de como cancelar).
- Os testes focados abaixo ainda não existem para este plano; serão
  adicionados por sprint, hermeticamente (sem rede real, sem Telegram real).

```bash
uv sync --frozen --extra dev
uv run pytest tests/test_channel_execution.py tests/test_execution_control.py tests/test_controlled_processes.py tests/test_telegram_bridge.py tests/test_channel_base.py -q --tb=short
```

Antes da implementação, reconferir este baseline — o código muda rápido
neste repositório.

## Desenho proposto

Por analogia direta ao caso mais simples do Hermes (resultado chegando na
MESMA sessão/chat que disparou a tarefa, sem precisar resolver canal "home"):

1. **Callback de conclusão com origem capturada na criação.** Quando
   `run_command(background=true)` registra o processo em `self._processes`,
   gravar também `chat_id`/`send_fn` (ou um identificador equivalente que o
   `telegram_bridge.py` saiba resolver de volta para `send_text`) no momento
   do disparo — nunca resolvido depois. Um watcher (thread ou tarefa
   agendada, reaproveitando o padrão de thread já usado pelos `_reader` de
   stdout/stderr) detecta o fim do processo e chama um ponto único de
   entrega.
2. **Ponto único de entrega** (equivalente ao `deliver_wake`/`_deliver_result`
   do Hermes): uma função em `bauer/channel_execution.py` (ou novo módulo
   dedicado) que recebe `chat_id` + resultado e decide:
   - Se ainda há uma mensagem "iniciado..." viva e editável nesta
     conversa → editar em lugar (evita duplicar mensagem).
   - Senão → mandar mensagem nova, com cabeçalho explícito ("Resultado da
     tarefa em background: PID {pid} — {comando truncado}") para o usuário
     entender de onde veio algo que chega sem contexto imediato de turno.
3. **Idempotência local.** Um claim simples (arquivo/registro em memória
   com lock, chave = pid + hash do comando + timestamp de início) marcado
   "entregue" antes de mandar a mensagem — sem precisar de fila distribuída
   (Bauer é processo único por instalação; não precisamos do nível de
   durabilidade multi-worker do Hermes).
4. **Retry curto.** 2-3 tentativas com backoff curto (ex.: 2s/5s/10s, mesmo
   padrão do Hermes) se o `send_text` do Telegram falhar por erro
   transitório (timeout de rede, 429); desistir e logar se o chat/token
   ficou inválido (erro permanente).
5. **Reaproveitar para o futuro `/loop`.** O ponto único de entrega do item 2
   deve ser escrito de forma que a etapa 5 do plano 061 (autonomia/`/loop`)
   possa chamá-lo também, em vez de inventar um segundo caminho de entrega.
6. **Independente de executor (coordenação com o plano 062).** O registro
   `self._processes` de `run_command(background=true)` vive em
   `ExecToolsMixin`, chamado pelo `ToolRouter` — o mesmo caminho que o
   adapter Agno usa via `_bauer_tool_callable` (plano 062). O watcher e o
   ponto único de entrega deste plano não devem assumir `bauer_native`
   implicitamente: capturar `chat_id`/callback de entrega a partir do
   contexto da chamada de tool (que já é comum aos dois executores), não a
   partir de algo exclusivo do loop `_turno_cru`. Assim, se o plano 062
   migrar o Telegram para Agno (sprint 40) depois deste plano, a entrega de
   background continua funcionando sem reescrever.

## Sprints propostos

**Renumerado em 2026-11-04**: os números originais (41-44) colidiam com
sprints já em andamento de outra frente concorrente (plano 061, atividade
e aprovação remota). Consolidado numa sprint só, porque os itens são
fortemente acoplados — ver `specs/sprint-43-telegram-background-delivery/
SPEC.md` para o raciocínio completo.

| Sprint | Entrega | Esforço |
|---|---|---|
| 43 | Callback + captura de origem + ponto único de entrega + idempotência + retry + testes de ponta a ponta (consolida os itens 1-6 do desenho acima, antes planejados como sprints 41-44 separados) | L |

Segue o mesmo processo de evidência → implementação → revisão cruzada
independente → correção já usado nos sprints do plano 062.

## Critérios de aceite

- Uma tarefa iniciada com `run_command(background=true)` no Telegram gera,
  ao terminar, uma mensagem na mesma conversa — sem o usuário perguntar de
  novo.
- Nenhuma duplicata de entrega, mesmo se o processo do gateway reiniciar
  entre o fim da tarefa e a confirmação de envio.
- Falha transitória de rede não derruba a entrega (retry curto); falha
  permanente (chat/token inválido) não fica tentando para sempre.
- Suíte hermética (`tests/conftest.py`) preservada — nenhum teste novo bate
  em rede real ou Telegram real.

## Entregue em 2026-11-04 — sprint 43 completa, STOP não disparado

Implementado inteiro numa sprint só, conforme consolidação descrita acima.
Evidência de código real reconferida em `specs/sprint-43-telegram-
background-delivery/SPEC.md`/`ARCHITECTURE.md` antes de implementar
(checkout `28d5fcb`); o STOP da SPEC.md ("`TelegramBridge` sem jeito de
expor sucesso/falha sem reestruturar") foi confirmado como NÃO disparado
— `send_text` já existia como método normal, só faltava um sinal de
retorno.

- **`bauer/telegram_bridge.py`**: método novo
  `send_text_result(chat_id, text) -> bool` ao lado de `send_text`
  (opção (a) da ARCHITECTURE.md) — mesmo fallback HTML→texto puro, mas
  deixa a última falha subir como exceção em vez de engolir, pra quem
  chama (o retry da entrega de background) poder classificar transitório
  vs. permanente. `send_text` não foi tocado — outros chamadores já
  dependem do seu contrato "nunca lança". `TelegramBridge.__init__` passou
  a chamar `register_delivery_channel("tg:", self._deliver_background_
  result)`, onde `_deliver_background_result` extrai `chat_id` de
  `session_id.removeprefix("tg:")` (formato já confirmado em
  `telegram_bridge.py:728`/`:920`, sem precisar de registro adicional).
- **`bauer/channel_execution.py`**: novo "mail slot" channel-agnostic —
  `register_delivery_channel`/`deliver_to_session` (resolve por prefixo de
  `session_id`), `deliver_with_retry` (backoff `2.0/5.0/10.0`s,
  distinguindo erro permanente via substrings reconhecíveis do erro da
  API do Telegram — `chat not found`, `bot was blocked`, `unauthorized`,
  etc. — de erro transitório, tratado como default), `claim_delivery`
  (idempotência em memória, chave `pid:started_at`, marcada ANTES de
  tentar entregar) e `deliver_background_completion`/
  `format_background_result` (o template de cabeçalho explícito "Resultado
  da tarefa em background (PID {pid})...").
- **`bauer/tools/execution.py`**: no branch `background` de `_run_command`,
  logo após montar `self._processes[pid_str]`, captura
  `get_runtime_ids()` (confirmado: já devolve o `session_id` correto
  dentro de um turno de canal, via `TurnMetrics.context()` em
  `channel_governance.py:98` — não precisou de nenhuma mudança nesse
  módulo nem no adapter Agno) e, se houver `origin_session_id`, sobe uma
  thread daemon que faz `proc.wait()` (com um join curto dos readers de
  stdout/stderr antes, pra reduzir a janela de corrida com o EOF do pipe)
  e chama `deliver_background_completion`. Sem canal (CLI/sem turno),
  nenhum watcher é criado.
- **Sem heurística de "editar mensagem viva"** — mensagem nova sempre,
  como o SPEC já previa; não há hoje um jeito de rastrear no bridge se
  uma mensagem anterior ainda está "viva"/editável.
- **Testes**: `tests/test_background_delivery.py` (14 testes novos,
  hermético) cobrindo registro/`deliver_to_session`, captura de
  `origin_session_id` (com e sem canal), watcher entregando num processo
  real (`python -c "print(1)"`), idempotência, retry
  transitório/permanente/esgotamento, e um teste de ponta a ponta mínimo
  (`backend.process()` real disparando `run_command(background=true)`
  dentro de um turno simulado, entregando num `deliver_fn` fake registrado
  sob `"tg:"`, sem o usuário perguntar de novo).
- **Achado durante a implementação, corrigido**: os dois testes que
  disparam um `run_command(background=true)` REAL usavam o mesmo
  `session_id` literal (`"fake:777"`) — sob a suíte completa com
  `pytest-xdist`, um watcher daemon (deliberadamente não joinado) de um
  teste anterior conseguiu, uma vez, entregar usando o canal fake
  registrado por um teste posterior que caiu no mesmo worker. Corrigido
  com prefixo de `session_id` único por teste (`uuid4` curto); confirmado
  estável em 3 reruns consecutivos do arquivo isolado + 1 rerun da suíte
  completa depois da correção.
- **Limitação conhecida, documentada e aceita (não resolvida aqui)**: tudo
  em memória de processo — um restart do processo do gateway entre o fim
  da tarefa e a confirmação de entrega perde o registro pendente e mata o
  watcher (uma thread do processo antigo) junto; o processo background em
  si (não é filho do gateway) continua rodando órfão. Consistente com o
  plano explicitamente descartar fila/claim distribuído multi-processo.
- **Reaproveitável para o futuro `/loop`** (etapa 5 do plano 061, não
  iniciada): `deliver_to_session`/`deliver_with_retry` não assumem nada
  específico de `run_command` — qualquer caller com `session_id` + texto
  pode reusar o mesmo caminho.

**Gates executados (checkout `codex/sprint-43-telegram-background-
delivery`, worktree isolado), resultado real por código de saída — não
estimado**:

```
uv sync --frozen --extra dev
  → 13 pacotes instalados (pytest-xdist, pytest-asyncio, mypy, ...), sem erro

uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
  → "All checks passed!" — exit 0

uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
  → "All checks passed!" — exit 0

uv run pytest tests/test_controlled_processes.py tests/test_telegram_bridge.py \
  tests/test_channel_base.py tests/test_channel_execution.py tests/test_background_delivery.py -q --tb=short
  → exit 0, sem FAILED/ERROR

uv run pytest tests/ -q --tb=short
  → exit 0 confirmado via código de saída do próprio pytest (não do pipe
    com `tail`, que mascara o código real — armadilha notada durante a
    execução: a primeira rodada em background usava `| tail -150`, cujo
    exit code é o do `tail` e não do `pytest`; a rodada de confirmação
    final gravou a saída num arquivo e checou `$?` do `pytest` direto).
    Nenhum FAILED/ERROR no log completo.
```

Nenhum commit novo criado nesta sessão — trabalho fica no working tree da
branch `codex/sprint-43-telegram-background-delivery`, conforme
instrução.
