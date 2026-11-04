# BACKLOG — Sprint 43: entrega de resultado de tarefas em background

- [x] SPEC/ARCHITECTURE escritos com evidência real reconferida (checkout
  `28d5fcb`), consolidando os itens 1-6/sprints 41-44 do plano 063 numa
  sprint só.
- [x] Decidir e implementar o método de `TelegramBridge` que dá sinal de
  sucesso/falha (`send_text_result` ou equivalente). **Decisão**: opção (a)
  da ARCHITECTURE.md — `TelegramBridge.send_text_result(chat_id, text) ->
  bool` (`bauer/telegram_bridge.py`), método novo ao lado de `send_text`
  (não reescrito — outros chamadores dependem do seu contrato "nunca
  lança"). Mesmo fallback HTML→texto puro de `send_text`, mas deixa a
  última falha subir como exceção em vez de engolir, pra
  `deliver_with_retry` poder classificar transitório vs. permanente.
- [x] `register_delivery_channel`/`deliver_to_session` em
  `channel_execution.py`.
- [x] `TelegramBridge` registra `"tg:"` no ponto de construção/inicialização
  (`__init__`, via `_deliver_background_result`).
- [x] Captura de `origin_session_id` via `get_runtime_ids()` em
  `run_command(background=true)` (`execution.py`).
- [x] Watcher (thread) por processo background, chama o ponto de entrega
  ao terminar. Junta os readers de stdout/stderr (timeout 5s) antes de
  montar o texto, pra reduzir a janela onde `proc.wait()` retorna antes do
  EOF do pipe ser lido por completo.
- [x] Idempotência (dict/set em memória, chave pid+started_at) —
  `claim_delivery`/`_delivered_keys` em `channel_execution.py`.
- [x] Retry curto (2s/5s/10s) distinguindo erro transitório/permanente —
  `deliver_with_retry`/`_is_permanent_delivery_error` em
  `channel_execution.py`. Permanente: substrings reconhecíveis no erro da
  API do Telegram (`chat not found`, `bot was blocked`, `bot was kicked`,
  `user is deactivated`, `unauthorized`, `forbidden`, `peer_id_invalid`).
  Qualquer outra exceção (timeout de rede, 5xx, etc.) é tratada como
  transitória e tentada de novo até esgotar o schedule.
- [x] Testes herméticos: registro, captura de origem, idempotência,
  retry, ponta a ponta mínimo — `tests/test_background_delivery.py` (14
  testes novos). Achado durante a implementação: os dois testes que
  disparam um `run_command(background=true)` REAL precisam de um prefixo
  de `session_id` único por teste (`uuid4` curto) — sem isso, um watcher
  daemon (não joinado, de propósito) de um teste anterior pode entregar
  usando o canal fake registrado por um teste posterior quando a suíte
  inteira roda sob `pytest-xdist` e os dois testes caem no mesmo worker;
  visto reproduzir uma vez na suíte completa, corrigido e confirmado
  estável em 3 reruns consecutivos do arquivo isolado + 1 rerun da suíte
  completa.
- [x] Gates: Ruff bloqueante/estilo, suíte completa, nenhuma regressão em
  `test_controlled_processes.py`/`test_telegram_bridge.py`/
  `test_channel_base.py`/`test_channel_execution.py`. Todos verdes — ver
  plano 063 para os números exatos.
- [x] Atualizar `plans/063-telegram-entrega-tarefas-background.md`:
  status, registro do que foi entregue.
- [x] Atualizar `plans/README.md` — linha do plano 063 marcada `ENTREGUE`
  (antes `TODO`).

## Decisões e limitações registradas (fora do que já está no SPEC/ARCHITECTURE)

- **Sem edição de mensagem "iniciado..." em lugar** — comportamento é
  sempre mensagem nova com cabeçalho explícito, como o SPEC já previa
  (STOP não disparado: `send_text`/`send_text_result` existem sem precisar
  reestruturar o bridge).
- **Durabilidade**: tudo em memória de processo (`_delivery_channels`,
  `_delivered_keys`). Reinício do processo do gateway perde o registro de
  entrega pendente e o watcher junto — documentado no SPEC.md como
  limitação conhecida e aceita, não resolvida aqui.
- **Sem alteração em `AgnoRuntimeAdapter`/`channel_governance.py`/
  `TurnMetrics`** — a captura de `origin_session_id` depende só de
  `get_runtime_ids()`, que já funciona corretamente para os dois
  executores (`bauer_native` e Agno) sem qualquer mudança nesses módulos.
