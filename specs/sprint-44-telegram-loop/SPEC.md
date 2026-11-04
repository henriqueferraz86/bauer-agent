# SPEC — Sprint 44: `/loop` autônomo no Telegram (plano 061, Etapa 5)

Plano 061, Etapa 5 ("Trabalho autônomo e recuperação"). Implementa o comando
`/loop <objetivo>` no bridge do Telegram, reusando o motor governado que a web
já usa (`bauer/serve_loop.py` + `core.kernel`), com admissão comprovada,
recuperação honesta após restart e entrega best-effort do relatório final.

## Problema

O Telegram hoje só executa turnos síncronos (`/chat`-like, via
`BaseBridge._execute_turn` → `run_governed`, um turno por vez). Não há como
pedir "trabalhe sozinho até terminar" como a web já permite via `POST /loop`
(`bauer/server.py:_start_loop_impl`, que admite o run com `kernel.admit(
KernelRequest(autonomous=True, ...))`, mantém o run em custódia do Kernel via
`continue_governed`, e roda rodadas com `serve_loop.run_loop_rounds`).

O processo do gateway Telegram (`bauer/gateway_runtime.py` → `AgentBackend`)
é um processo Python separado do `bauer/server.py` (não compartilha memória) —
"motor compartilhado com a web" significa reusar o mesmo MÓDULO
(`serve_loop.py`, `core.kernel`), não chamar a função do outro processo.

Gaps adicionais que a Etapa 5 pede explicitamente:
- Hoje, no loop principal de `TelegramBridge.start()`, o offset do
  `getUpdates` avança (`_save_offset()`) depois de SUBMETER o processamento a
  um `ThreadPoolExecutor` (não depois dele terminar) — um crash nessa janela
  perde o update para sempre (offset já avançou). Para `/loop` especificamente
  isso é inaceitável: perderia o vínculo update→run. Fora do escopo desta
  sprint: corrigir esse gap para as OUTRAS mensagens/comandos (texto livre,
  mídia) — mudar a ordem de todo o `_handle_update` tem superfície de colisão
  grande com o trabalho simultâneo de outra sessão em arquivos do mesmo canal
  e não foi pedido. Only `/loop`'s own admission is brought onto the safe side
  of that ordering (ver Arquitetura, item 2).
- Um restart do gateway mata a thread do loop (ela não sobrevive ao processo).
  Hoje nada detecta isso: um run fica "running" para sempre no `RunManager` e
  o usuário nunca sabe se terminou. Precisa de reconciliação explícita, nunca
  reprocessar o `/loop` sozinho.

## Objetivo e escopo

1. Comando `/loop <objetivo>` em conversa privada autorizada, usando o mesmo
   motor (`serve_loop.resolve_loop_limits` + `run_loop_rounds` +
   `core.kernel.admit`/`continue_governed`) que `POST /loop` da web usa.
2. Persistir, de forma síncrona e ANTES de responder "iniciado", um registro
   local (chave única = `update_id` do Telegram, vínculo a `run_id`/
   `session_id`/`chat_id`/`workspace`) — closes the ordering gap for `/loop`
   specifically, sem tocar a ordem de dispatch de outros comandos/mensagens.
3. Diferenciar estados: `received` → `admitted` → `running` → `interrupted`
   (detectado só na reconciliação) → terminal (`completed`/`failed`/
   `stopped`/`limit`). Reconciliar pendências no `TelegramBridge.start()`
   contra o status real do `RunManager` do Kernel — nunca reemitir o `/loop`
   sozinho.
4. Entregar o relatório final (e o aviso de interrupção, se for o caso) via
   `bauer/channel_execution.py` (`deliver_with_retry` + `claim_delivery`,
   reusando a infraestrutura do plano 063) — sem prometer entrega exatamente
   uma vez.
5. Persistir só o necessário para retomar: `update_id, chat_id, session_id,
   run_id, workspace, goal (truncado), state, limits, started_at,
   finished_at, delivered`. Sem conteúdo de conversa (isso já vive no store de
   sessão, com sua própria retenção). Arquivo com permissão restrita (0600) e
   expurgo de registros terminais com mais de 7 dias.
6. Estender `/cancelar <run_id>` para também localizar e cancelar um loop
   Telegram ativo (hoje só olha `ChannelExecutionRegistry`, que `/loop` não
   usa).

Fora de escopo: mudar a ordem de dispatch de mensagens/comandos que não sejam
`/loop`; `/atividade` mostrar progresso de loops (a resposta do `/loop` já diz
como consultar); fila distribuída/durável de entrega (plano 063 já decidiu
contra isso, mantido); Slack/Discord (plano 061 é Telegram); replay automático
de um loop interrompido.

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy` (suíte hermética; nenhum provider real, nenhum Telegram
  real — stub `Kernel`/`RunManager`/HTTP)

## Sub-agents recomendados

- `kernel-admission-reviewer`: confirmar que nenhuma resposta "iniciado" é
  enviada antes de `kernel.admit()` devolver sucesso (paridade com
  `_start_loop_impl`).
- `telegram-reliability-reviewer`: confirmar a ordem persist-antes-do-offset
  para `/loop` e a reconciliação honesta (sem replay cego) após restart.

## Requisitos

1. `/loop` sem objetivo → mensagem de uso, nenhuma admissão.
2. `/loop` fora de conversa privada autorizada → recusa, mesmo texto padrão
   de `/cancelar`/`/atividade`.
3. Resposta "iniciado" só é enviada depois de `kernel.admit()` devolver um run
   sem bloqueio (`waiting_approval`/`deny`/erro → resposta reflete isso, nunca
   "iniciado").
4. O registro do loop é persistido (disco) antes do handler de `/loop`
   retornar — garante que esteja lá mesmo que o processo caia exatamente
   depois (antes do próximo `_save_offset()` do lote).
5. Reenviar o mesmo `update_id` (replay do Telegram) não cria um segundo run
   — devolve o estado do run já admitido.
6. Limites (`max_minutes`/`max_tool_calls`/`max_cost_usd`) vêm de
   `LoopSection` da config do workspace ativo, mesma função
   `resolve_loop_limits` da web, mesmo `clamp_to_config=True` (cliente remoto
   nunca amplia o teto do servidor).
7. `/cancelar <run_id>` interrompe um loop ativo mesmo sem entrada em
   `ChannelExecutionRegistry`; sem quebrar o comportamento existente para
   execuções de turno único.
8. Ao concluir (qualquer `stop_reason`), o relatório final é entregue via
   `deliver_with_retry`, reivindicado uma única vez (`claim_delivery`), nunca
   reenviado em duplicidade pela mesma conclusão.
9. No startup do bridge, todo registro não-terminal é reconciliado contra
   `kernel.runs.get_run(run_id)`: se o run real já é terminal, o registro local
   é corrigido para bater (e o relatório é entregue se ainda não tiver sido);
   se o run real não é terminal (thread morreu com o processo), ele é marcado
   `failed`/`stopped` via a MESMA primitiva idempotente que `RuntimeRecovery`
   usa (`fail_run_se_nao_terminal`) e um aviso de interrupção é entregue — sem
   jamais reiniciar o `/loop` automaticamente.
10. Nenhum texto de objetivo/goal aparece em log não redigido; o arquivo de
    registro tem permissão 0600; registros terminais com mais de 7 dias são
    expurgados.
11. Suíte hermética: nenhum teste chama rede, Telegram real, ou provider LLM
    real.

## Critérios de aceite

- Restart do gateway após admitir um `/loop` não perde o vínculo
  chat↔run_id↔registro nem duplica o run (replay do mesmo update_id é no-op).
- Queda do processo Telegram durante um loop nunca é relatada como sucesso:
  a reconciliação marca `failed`/`stopped` e entrega um aviso honesto.
- O relatório terminal reflete o `stop_reason` real do Kernel/`run_loop_rounds`
  (`completed`, `budget_exhausted`, `max_rounds`, `cancelled`, `kill_switch`,
  `validacao_reprovou`, erro) — nunca um texto genérico de sucesso.
- `uv run pytest tests/ -q` e `uv run --with ruff==0.17.0 ruff check bauer/
  --select E9,F63,F7,F82` passam.
