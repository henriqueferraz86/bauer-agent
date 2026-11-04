# SPEC — Sprint 43: entrega de resultado de tarefas em background no Telegram

Implementa o plano 063 (`plans/063-telegram-entrega-tarefas-background.md`)
inteiro numa sprint só — os itens originais (sprints 41-44 do documento,
renumerados aqui pra 43 porque outra sessão concorrente já reivindicou os
números 41/42 localmente para o plano 061) são fortemente acoplados: captura
de `chat_id` sem ponto de entrega é inútil, idempotência sem entrega não
existe, e testes de ponta a ponta precisam dos três pedaços juntos pra
provar algo. Consolidar evita o custo de 4 ciclos separados de
SPEC→implementação→revisão para peças que não funcionam isoladas.

## Problema

Hoje, `run_command(..., background=true)` (`bauer/tools/execution.py`)
inicia um processo desacoplado e devolve só "PID X, acompanhe com
`process(poll)`" — nada avisa quando termina. Ver `plans/063-...md` seção
"Problema e objetivo" para o enquadramento completo; não repetir aqui.

## Evidência (checkout `28d5fcb`, branch
`codex/sprint-43-telegram-background-delivery`, reconferida em 2026-11-04 —
o plano original é de 2026-10-26, código mudou desde então por causa do
plano 062)

- `bauer/tools/execution.py:242-282` (`_run_command`, branch `background`):
  comportamento idêntico ao descrito no plano original — sem drift.
  `self._processes[pid_str] = {"proc": proc, "owner": ..., "label": ...,
  "command": ..., "stdout_buf": ..., "stderr_buf": ...}`.
- **Achado novo, não estava no plano original**: `bauer/channel_governance.py:98`
  (`TurnMetrics.context()`, o mesmo context manager que já envolve TODO
  turno de canal — `with metrics.context(key, run_id):` em
  `channel_base.py:937`, intocado pelos sprints 35-40) já chama
  `set_runtime_ids(session_id, run_id)`. Ou seja, **`get_runtime_ids()`
  (`bauer/runtime_ids.py:35`) já devolve `(session_id, run_id)` corretos
  no exato momento em que `run_command(background=true)` roda dentro de
  um turno do Telegram** — não precisa inventar nenhum mecanismo novo de
  propagação de contexto, já existe e já está ativo pros dois executores
  (`bauer_native` e Agno, plano 062 sprint 40 confirmou que `TurnMetrics.
  context()` não foi alterado por nenhum dos dois caminhos).
- `session_id` pro Telegram segue o formato `f"tg:{chat_id}"`
  (`telegram_bridge.py:728`, `:920`) — dá pra extrair `chat_id` direto do
  `session_id` sem precisar de um registro adicional de "qual canal é
  este" nem resolver nada depois (requisito do desenho original, item 1).
- `bauer/tools/agent_misc.py` (`process`, ações `poll`/`kill`/etc.) usa
  `proc.poll()`/`proc.returncode` puro — sem mudança desde o plano
  original.
- Nenhum watcher existe hoje — confirmado, sem mudança desde o plano
  original.
- `ToolRouter`/`ExecToolsMixin` não tem acesso direto a uma instância de
  `TelegramBridge` (é deliberadamente agnóstico de canal, compartilhado
  entre CLI/web/Telegram) — a entrega não pode chamar `send_text`
  diretamente. Precisa de um registro de entrega desacoplado (ver Desenho).

## Desenho (consolidação dos itens 1-6 do plano 063)

1. **Registro de canais de entrega**, novo módulo pequeno ou seção em
   `bauer/channel_execution.py` (já existe, já é o módulo que o plano
   063 sugere): `register_delivery_channel(prefix: str, deliver_fn:
   Callable[[str, str], DeliveryResult])` — `prefix` é o prefixo do
   `session_id` (`"tg:"` para Telegram), `deliver_fn(session_id, text) ->
   bool` sabe resolver `chat_id` a partir do `session_id` e mandar a
   mensagem (ou editar, se tiver uma mensagem "iniciado..." viva — exige
   `TelegramBridge` expor um jeito de perguntar "tenho uma mensagem viva
   pra esta sessão?"; se não tiver isso pronto, comece só com "mensagem
   nova sempre" — é o fallback que o plano já descreve como válido, não
   invente heurística de "mensagem viva" sem um jeito real de checar).
   `TelegramBridge.__init__` (ou onde o bridge é montado) chama
   `register_delivery_channel("tg:", self._deliver_background_result)`.
2. **Captura de origem na criação**: em `_run_command`'s branch
   `background` (`execution.py`), logo após criar `self._processes[pid_str]`,
   capturar `from ..runtime_ids import get_runtime_ids; session_id, run_id
   = get_runtime_ids() or (None, None)` e guardar no MESMO dict
   (`self._processes[pid_str]["origin_session_id"] = session_id`).
3. **Watcher**: uma thread daemon por processo background (mesmo padrão dos
   `_reader` de stdout/stderr já existentes) que faz `proc.wait()`, e ao
   terminar chama o ponto único de entrega com `session_id`, resultado
   (exit code + stdout/stderr truncado + comando truncado), e cabeçalho
   explícito tipo:
   ```
   Resultado da tarefa em background (PID {pid})
   $ {comando truncado}
   -------------
   exit: {code}
   {stdout/stderr truncado}
   ```
4. **Idempotência**: um `set`/dict em memória de "já entregue" (chave =
   `pid_str` + hash curto do comando + `started_at`) checado/marcado
   ANTES de chamar `deliver_fn` — suficiente para processo único (plano
   063 já descarta a necessidade de claim distribuído: "Bauer roda um
   único processo de gateway por instalação").
5. **Retry curto**: 2-3 tentativas com backoff `(2.0, 5.0, 10.0)` segundos
   se `deliver_fn` devolver `False`/levantar exceção classificável como
   transitória (timeout de rede, erro 5xx/429 da API do Telegram);
   desistir e logar (não re-tentar pra sempre) em erro permanente (chat
   não encontrado, token inválido, bot bloqueado pelo usuário).
6. **Reutilizável pro futuro `/loop`**: a assinatura de
   `register_delivery_channel`/o ponto único de entrega não deve assumir
   nada específico de `run_command` — qualquer coisa com `session_id` +
   texto deve conseguir usar o mesmo caminho.

## Fora de escopo

- `/loop`/autonomia contínua (plano 061 etapa 5, não iniciada).
- `autonomy_delegate`/Desktop API (sem noção de `chat_id`, trabalho
  futuro).
- Fila durável multi-processo (explicitamente descartada no plano 063).
- Qualquer mudança em `AgnoRuntimeAdapter`/`channel_governance.py`/
  `TurnMetrics` — já fornecem o que esta sprint precisa (`get_runtime_ids`
  correto), não precisam de alteração.

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy` (suíte hermética — nenhum teste novo bate em rede real
  ou API do Telegram real; usar fakes/mocks pro `deliver_fn`/bot).

## Critérios de aceite

(copiados do plano 063, sem alteração — ainda válidos)

- Uma tarefa iniciada com `run_command(background=true)` no Telegram gera,
  ao terminar, uma mensagem na mesma conversa — sem o usuário perguntar de
  novo.
- Nenhuma duplicata de entrega, mesmo se o processo do gateway reiniciar
  entre o fim da tarefa e a confirmação de envio. **Nota de escopo**: "o
  processo reiniciar" aqui significa o processo do GATEWAY/bauer serve,
  não o processo background em si — se o gateway reiniciar, o processo
  background original continua rodando órfão (não é filho do processo que
  reiniciou) e seu watcher (uma thread do processo antigo) morre junto;
  isso é uma limitação conhecida de um registro em memória, consistente
  com o plano 063 descartar durabilidade multi-processo — documentar, não
  resolver aqui.
- Falha transitória de rede não derruba a entrega (retry curto); falha
  permanente (chat/token inválido) não fica tentando para sempre.
- Suíte hermética preservada.

## STOP

Se `TelegramBridge` não tiver nenhum jeito de expor uma função
`send_text(chat_id, text) -> bool`-compatível para registrar como
`deliver_fn` sem reestruturar o bridge inteiro — parar e voltar com o
achado, em vez de forçar uma integração frágil. (Evidência preliminar: já
vimos em sessões anteriores desta conversa `send_fn = lambda text:
self.send_text(msg.chat_id, text)` em `telegram_bridge.py` — `send_text`
já existe como método; isso NÃO deveria disparar o STOP, mas confirme
antes de presumir.)
