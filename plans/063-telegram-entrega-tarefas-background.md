# Plano 063 — Entrega de resultado de tarefas em background no Telegram

**Status:** TODO

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

| Sprint | Entrega | Esforço |
|---|---|---|
| 41 | Callback de conclusão + captura de `chat_id` na criação do processo background (`run_command`) | M |
| 42 | Ponto único de entrega em `channel_execution.py` (editar em lugar vs. mensagem nova com cabeçalho) | M |
| 43 | Idempotência (claim local) + retry curto com backoff | S |
| 44 | Testes de ponta a ponta com Telegram simulado (bot/API falsos) + atualização de `plans/061` etapa 5 referenciando este mecanismo | M |

Cada sprint segue o mesmo processo de evidência → implementação → testes
hermeticos → revisão cruzada já usado nos sprints 33-34 do plano 061.

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
