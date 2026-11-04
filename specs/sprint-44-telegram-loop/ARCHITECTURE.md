# ARCHITECTURE — Sprint 44: `/loop` autônomo no Telegram

## Pontos de partida confirmados na base real (master, commit `be66d1a`)

- `bauer/serve_loop.py`: `resolve_loop_limits(loop_section, overrides,
  clamp_to_config=True)`, `run_loop_rounds(goal, ctx, turn_fn, budget,
  should_stop, on_round, ...)`, `LoopState` (dataclass, `to_dict()`),
  `loop_registry()` (singleton `LoopRegistry`, processo-local). Puro, sem
  threads nem I/O — o caller decide tudo isso. É o módulo que
  `bauer/server.py:_start_loop_impl` usa; vamos importar DIRETO, não via
  HTTP (o gateway Telegram é outro processo).
- `bauer/channel_base.py`:
  - `AgentBackend._resolver_kernel()` (linha ~868): lazy, uma instância de
    `BauerKernel` por workspace (cache `self._workspace_kernels`,
    `self._kernel_lock`), via `build_channel_kernel(cfg, workspace=...,
    sessions_dir=...)` (`channel_governance.py`). Depende de
    `self._effective_router().workspace` — contexto de projeto ATIVO, que só
    existe dentro do fluxo normal de mensagem (`capture_project_context`).
  - `AgentBackend._execute_turn()` (linha ~900): padrão de turno único via
    `run_governed(kernel, _executar, ..., runtime_adapter="bauer_native")`.
    NÃO é o padrão certo para `/loop` — `/loop` precisa do padrão
    admit+continue_governed de duas fases que `_start_loop_impl` usa (deixa o
    run em `queued`, o Kernel assume custódia via `continue_governed`).
  - `cancel_channel_execution()` (linha ~837): só sabe cancelar entradas de
    `self._executions` (`ChannelExecutionRegistry`), que `/loop` não usa.
- `bauer/channel_execution.py`: registro de entrega (`register_delivery_
  channel`, `deliver_to_session`, `deliver_with_retry`, `claim_delivery`,
  sprint 43/plano 063) — reusar DIRETO, sem modificar.
- `bauer/telegram_bridge.py`:
  - `session_id` do Telegram é sempre `f"tg:{chat_id}"` (ver `register_
    delivery_channel("tg:", self._deliver_background_result)` no `__init__`).
  - `start()` (linha ~1664): `while not self.stopped: updates = self._api(
    "getUpdates", offset=self._offset+1, ...); for update in updates:
    self._offset = max(...); self._handle_update(update)
    if updates: self._save_offset()`. CONFIRMADO: o offset avança depois do
    LOTE inteiro ter sido *despachado* (não processado) — `_handle_update`
    só bloqueia para `/cancelar`/`/atividade`/`/projetos`; texto livre e
    qualquer comando novo caem em `self._executor.submit(...)` (não
    bloqueia). É por isso que o item 2 da spec exige que `/loop` persista
    SEU registro antes de retornar de `_handle_update`, mesmo que a RODADA em
    si rode numa thread separada depois.
  - `_handle_update()` (linha ~1705): comandos síncronos (`/cancelar`,
    `/atividade`, `/projetos`) são tratados ANTES do `self._executor.submit(
    self._safe_process, msg, message)` final. `/loop` entra no mesmo lugar.

## Decisão: onde mora o código novo

Módulo novo `bauer/telegram_loop.py` (não dentro de `telegram_bridge.py`,
que já tem >1200 linhas e está sob edição concorrente pesada por outra
sessão nesta mesma janela de trabalho — menos superfície de merge). Mantém
toda a lógica de admissão/persistência/reconciliação/cancelamento num só
lugar, testável sem subir um bot de verdade. `telegram_bridge.py` ganha
só: entrada no `BOT_COMMANDS`, um `if command == "/loop":` em
`_handle_update`, uma chamada em `start()` antes do loop de polling, e uma
extensão pequena no branch de `/cancelar` (tentar `cancel_channel_execution`
primeiro; se vier a mensagem-sentinela de "nenhuma execução", tentar
`telegram_loop.cancel_loop(...)`).

## Resolução de Kernel fora do contexto de projeto ativo

`_resolver_kernel()` depende de `self._effective_router()`, que lê uma
ContextVar de projeto ativo — válida durante o processamento de UMA mensagem,
não no startup do bridge (reconciliação) nem reutilizável fora desse fluxo.
Mudança mínima e não-invasiva: adicionar um parâmetro opcional
`workspace: Path | None = None` em `_resolver_kernel` — quando passado, usa
esse workspace direto (mesmo corpo, só pula a leitura de
`self._effective_router()`); quando omitido, comportamento 100% igual ao
atual. `telegram_loop.py` guarda o `workspace` resolvido no momento da
admissão (dentro do registro persistido) e o reusa depois, tanto para
cancelamento quanto para reconciliação pós-restart, sem depender de contexto
de projeto ativo.

## Fluxo de admissão (`/loop <objetivo>`)

1. `_handle_update` reconhece `/loop`, exige conversa privada autorizada
   (mesma guarda de `/cancelar`), extrai `goal` (resto do texto).
2. Chama `telegram_loop.start_loop(backend, msg, update_id, goal)`,
   SINCRONAMENTE (não via `self._executor.submit`):
   a. Resolve `session_key`/`workspace` via `backend.capture_project_
      context(msg)` + `backend.session_key(msg)` (mesmo caminho que
      `_execute_turn` já usa), monta `session_id = f"tg:{chat_id}"`.
   b. Checa idempotência: já existe registro para este `update_id`? Se sim,
      devolve o estado atual dele (sem readmitir).
   c. Resolve `kernel = backend._resolver_kernel()` (contexto de projeto já
      ativo nesse ponto, igual turno normal) — guarda `workspace` resolvido.
   d. `limits = resolve_loop_limits(loop_section_do_config, {}, clamp_to_
      config=True)` (overrides vazios — Telegram não expõe flags de limite
      na v1, consistente com "sem complexidade além do pedido"; a config do
      operador já é o teto real).
   e. `run, early = kernel.admit(KernelRequest(task=goal, session_id=
      session_id, agent_id="channel.telegram.loop", input={...,
      "limits": limits.to_dict()}, autonomous=True))`.
   f. Se `early is not None`: `waiting_approval` → responde pedindo
      aprovação (texto com `approval_id`), persiste registro em estado
      `received` com esse desfecho, NÃO inicia thread. `deny`/erro → responde
      o motivo, não persiste (nada para recuperar).
   g. Caso contrário: persiste registro em estado `admitted` (ESCRITA EM
      DISCO ANTES DE QUALQUER RESPOSTA); SÓ ENTÃO responde "iniciado,
      run_id=...". Depois disso, inicia a thread (`daemon=True`) que roda
      `run_loop_rounds` sob `continue_governed`, mesmo padrão de
      `_start_loop_impl` (budget, `_should_stop`, `_on_round`), e marca o
      registro `running` assim que a thread de fato começa.
3. Ao terminar (qualquer `stop_reason`), a thread: atualiza o registro para o
   estado terminal certo (mesma tabela de mapeamento que `_start_loop_impl`
   usa: `completed`/`budget_exhausted`→`limit`/`max_rounds`→`limit`/
   `cancelled`/`kill_switch`→`stopped`/outro→`failed`), monta o texto do
   relatório (resultado, stop_reason, custo conhecido, nº de rodadas/tools,
   pendências) e chama `deliver_with_retry(session_id, texto)` após
   `claim_delivery(f"loop:{run_id}:done")`.

## Reconciliação no startup (`TelegramBridge.start()`)

Antes do `while not self.stopped`, chamar
`telegram_loop.reconcile_pending(self)` (recebe o BRIDGE, não só o backend,
porque precisa enviar mensagens). Para cada registro não-terminal:

1. Resolve o Kernel do `workspace` guardado no registro (via o novo parâmetro
   de `_resolver_kernel`), consulta `kernel.runs.get_run(run_id)`.
2. Se o run real já é terminal (outro processo/reconciliação anterior
   resolveu, ou o próprio `RuntimeRecovery` do Kernel já marcou `failed` por
   idade) → só sincroniza o registro local para bater; entrega o relatório
   SE `claim_delivery(f"loop:{run_id}:done")` ainda não foi reivindicado.
3. Se o run real NÃO é terminal → a thread que o executava morreu com o
   processo (threads não sobrevivem a restart). Chama
   `kernel.runs.fail_run_se_nao_terminal(run_id, "gateway Telegram reiniciou
   durante a execução autônoma")` (mesma primitiva idempotente que
   `RuntimeRecovery.recover_stuck_runs` usa — corrida segura contra qualquer
   outro escritor). Marca o registro local `interrupted`→`stopped` e entrega
   um aviso (`claim_delivery(f"loop:{run_id}:interrupted")`) — nunca reemite
   o `/loop`.
4. Expurga registros terminais com `finished_at` há mais de 7 dias.

## Formato do registro persistido

Um arquivo JSON por processo (lista de registros), ao lado do offset:
`state_dir / "telegram_loops.json"`, permissão 0600 ao salvar
(`os.chmod`). Campos: `update_id, chat_id, session_id, run_id, workspace,
goal (truncado a 400 chars), state, limits (dict), started_at, finished_at,
delivered (bool)`. Sem texto de tool call, sem transcript — isso já vive no
store de sessão existente.

## Cancelamento (`/cancelar <run_id>`)

`cancel_channel_execution` continua intocado (usado por Slack/Discord
também). `telegram_bridge.py`'s `/cancelar` branch: chama
`cancel_channel_execution` primeiro; se a resposta for literalmente a
sentinela "Nenhuma execução ativa correspondente nesta conversa e projeto."
E houver um loop ativo desta chat (com ou sem `run_id` explícito — mesma regra
de ambiguidade: 0 → mensagem genérica, 1 → cancela, 2+ → lista escolhas),
chama `telegram_loop.cancel_loop(backend, chat_id, run_id)`, que resolve o
Kernel do workspace do registro e chama `kernel.runs.cancel_run(run_id)`
(mesma primitiva que `POST /loop/{run_id}/stop` da web usa) — o
`_should_stop()` da própria thread do loop (que já checa `run.status ==
"cancelled"`, mesmo padrão de `_start_loop_impl`) faz o resto.

## Testes (hermético, sem rede/provider real)

`tests/test_telegram_loop.py`: stub `AgentBackend`/Kernel mínimo (fake
`admit`/`continue_governed`/`runs.get_run`/`runs.cancel_run`/
`runs.fail_run_se_nao_terminal`), fake `send_text_result` capturando texto
enviado. Cobre: admissão feliz, `waiting_approval` não dá falso "iniciado",
`deny` não persiste nada, replay do mesmo `update_id` não duplica,
cancelamento via `/cancelar <run_id>`, reconciliação com run real terminal
(entrega só uma vez) e com run real não-terminal (marca interrompido,
nunca reemite), expurgo de registros antigos, permissão 0600 do arquivo.
