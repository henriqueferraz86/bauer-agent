# ARCHITECTURE — Sprint 45: `/atividade` no Telegram

## Pontos de partida confirmados na base real (master, commit `3121bfa`)

- `bauer/server_activity.py:build_chat_activity(run_manager, event_bus,
  session_id, *, run_id=None) -> dict` — já é o "motor compartilhado" do item
  1 da Etapa 3. Retorna `{"session_id": ..., "runs": [...]}`, cada run com
  `run_id, task, status, agent_id, agent_name, runtime, started_at,
  updated_at, agents: [{agent_id, name, task, status, activity, tool_name,
  updated_at}, ...]`. Sem custo (`cost_usd`) no envelope — ver decisão abaixo.
- `bauer/channel_base.py:AgentBackend._resolver_kernel(workspace=None)`
  (sprint 44) — reusar igual ao `telegram_loop.py`: resolve o Kernel do
  workspace ativo da conversa.
- `bauer/telegram_bridge.py`:
  - `_StreamingDraft` (linha ~240): padrão de edição throttled de UMA
    mensagem (`STREAM_EDIT_INTERVAL_S = 2.5`). NÃO reusar a classe diretamente
    (ela é específica de streaming de resposta de turno, com estado de
    buffer/silent_round que não se aplica aqui) — só o PADRÃO (throttle +
    edit em vez de reenviar) e a constante de intervalo.
  - `_api()` (linha ~461) já trata 429 com `retry_after` e dá retry/backoff
    em erro de rede para QUALQUER método, incluindo `editMessageText` — a
    thread observadora herda isso de graça por chamar `self._bridge._api(...)`.
  - `self._project_callbacks` / `_send_project_picker` / `_project_callback`
    (linha ~857-914): o padrão de token opaco (`secrets.token_urlsafe(18)`),
    dict com `expires_at`/`chat_id`/`owner`/`message_id`, limite de 500
    entradas, consumido atomicamente, dispatch por prefixo em
    `_handle_callback` (`data.startswith("proj:")`). Seguir o MESMO padrão
    para o prefixo `"act:"`.
  - `_handle_callback` (linha ~916): autorização (`_user_id_authorized`) já
    roda ANTES do dispatch por prefixo — o handler de `"act:"` não precisa
    reconferir isso.
  - O `/cancelar` existente (dentro de `_handle_update`) já tenta
    `cancel_channel_execution` e cai para `telegram_loop.cancel_loop`
    (sprint 44) — EXTRAIR esse bloco combinado para um método
    `_cancel_run_or_loop(self, msg, run_id) -> str` em `TelegramBridge`,
    comportamento idêntico, reusado pelo comando de texto e pelo botão novo.
- `bauer/core/events/bus.py:EventBus` tem `subscribe(event_type, handler)` —
  DELIBERADAMENTE não usado aqui. `publish()` chama os handlers inline, na
  mesma thread de quem publicou (o worker do turno/Kernel) — se um handler
  travar numa chamada HTTP ao Telegram, atrasaria a governança. A thread
  observadora faz POLLING (`list_events`/`build_chat_activity` de novo a cada
  tick), desacoplado, no seu próprio ritmo, igual ao padrão que a própria web
  já usa (SSE fazendo poll de `build_chat_activity`).
- `tests/test_chat_activity.py`: fixture `runtime(tmp_path)` cria
  `EventBus(root=tmp_path/"runtime")` e `RunManager(store=bus.store,
  event_bus=bus)` REAIS — usar esse padrão para os testes novos (gera
  eventos reais via `runs.create_run`/`bus.publish`, só a camada HTTP do
  Telegram é fake).

## Decisão: sem custo em USD no cartão (mesmo desvio documentado do sprint 44)

`build_chat_activity` não expõe `cost_usd` por run — isso vive em
`run.cost_estimate` no próprio `Run` (`RunManager`), fora do envelope
projetado. Para não reabrir o acoplamento de custo que o sprint 44 já decidiu
não extrair (`_TurnCostRecorder` é privado a `server.py:create_app`), o
cartão lê `run.cost_estimate` DIRETO do `Run` (`kernel.runs.get_run(run_id)`,
que o comando já tem em mãos) — é um campo público e simples do dataclass
`Run`, não exige extrair nada do `server.py`. Mostrar "custo desconhecido"
quando `cost_estimate` for `None`/0 e `usage_known` for falso — nunca mostrar
"US$ 0,00" como se fosse um valor real medido (mesma regra que a SPEC do
plano 061 já exige: "custo zero quando desconhecido" é proibido).

## Onde mora o código novo

Módulo novo `bauer/telegram_activity.py` (mesmo motivo do sprint 44: isolar a
lógica nova de um arquivo já grande e sob edição concorrente por outra
sessão). Funções:

- `render_activity_card(envelope_run: dict, run: Any) -> str` — pura,
  testável sem bridge nenhum. Recebe o dict de UM run (já projetado por
  `build_chat_activity`) e o `Run` real (para o custo). Formato (exemplo):

  ```text
  Projeto: <nome ou workspace>
  Tarefa: <task, truncada>
  Estado: <activity em português>

  <nome agente 1> — <activity>
  <nome agente 2> — <activity>

  Custo registrado: US$ 0,42   (ou "Custo: desconhecido")
  ```

  Mesmo espírito do exemplo já escrito no corpo do plano 061 ("Experiência
  desejada"). Trunca com `chunk_text`/corte seguro de HTML, nunca corta tag.

- `_activity_keyboard(token: str, cancelable: bool) -> dict` — inline
  keyboard com `Atualizar` (`act:refresh:<token>`) e, se `cancelable`,
  `Cancelar tarefa` (`act:cancel:<token>`).

- `send_activity_card(bridge, msg) -> None` — handler de `/atividade`:
  1. Guarda de conversa privada autorizada (mesmo texto de `/cancelar`).
  2. `backend.capture_project_context(msg)` + `backend.session_key(msg)` →
     `session_id`. `kernel = backend._resolver_kernel()`; `None` → mensagem
     clara, sem crash.
  3. `envelope = build_chat_activity(kernel.runs, kernel.bus, session_id)`.
     Sem runs → mensagem amigável, sem teclado, sem thread.
  4. `run_proj = envelope["runs"][0]`; `run = kernel.runs.get_run(run_proj["run_id"])`.
  5. Registra o(s) token(s) em `bridge._activity_callbacks` (mesmo padrão de
     `_project_callbacks`: lock, TTL, limite de 500, bound a
     `chat_id`/`owner`).
  6. Envia a mensagem (`render_activity_card` + teclado se não-terminal).
  7. Se `run_proj["status"]` não é terminal: `threading.Thread(target=
     _watch_activity, daemon=True, ...).start()`.

- `_watch_activity(bridge, *, chat_id, message_id, session_id, run_id,
  token, deadline_monotonic) -> None` — loop:
  ```
  last_rendered = <texto já enviado>
  while True:
      sleep(ACTIVITY_POLL_INTERVAL_S)
      run = kernel.runs.get_run(run_id)  # kernel resolvido de novo (workspace
                                          # gravado no token/registro)
      if run is None: break  # desapareceu — nada a fazer
      envelope = build_chat_activity(kernel.runs, kernel.bus, session_id, run_id=run_id)
      proj = envelope["runs"][0] if envelope["runs"] else None
      terminal = proj is None or proj["status"] in TERMINAL_RUN_STATUSES
      text = render_activity_card(proj, run) if proj else "<execução não encontrada>"
      if terminal or time.monotonic() >= deadline:
          # tentativa final INCONDICIONAL — try/except próprio, não herda
          # o estado de "falhou antes, não tenta mais"
          try:
              bridge._api("editMessageText", chat_id=..., message_id=...,
                         text=text, reply_markup={"inline_keyboard": []})
          except Exception:
              logger.warning(...)
          break
      if text != last_rendered:
          try:
              bridge._api("editMessageText", ...)
              last_rendered = text
          except Exception:
              logger.debug(...)  # best-effort: próximo tick tenta de novo
  ```
  O `except Exception` do tick intermediário NUNCA propaga e NUNCA seta uma
  flag "desisti para sempre" (diferente de `_StreamingDraft`) — é exatamente
  o requisito 7 do SPEC. O teto (`deadline_monotonic = start + 7200`) garante
  que a thread não vive para sempre.

- `handle_activity_callback(bridge, cq) -> bool` — dispatch de `"act:"`:
  `act:refresh:<token>` → re-renderiza na hora; `act:cancel:<token>` →
  `bridge._cancel_run_or_loop(msg_sintético, run_id)` e remove o teclado.
  Token inválido/expirado/de outro chat → no-op (retorna `True` mesmo assim,
  pois `_handle_callback` já respondeu o `answerCallbackQuery`).

## Mudanças em `bauer/telegram_bridge.py` (pequenas, aditivas)

- `BOT_COMMANDS`: adicionar `/atividade`.
- `__init__`: `self._activity_callbacks: dict[str, dict] = {}`,
  `self._activity_callback_lock = threading.Lock()`.
- `_handle_update`: branch `/atividade` (síncrono, como `/cancelar`),
  chamando `telegram_activity.send_activity_card(self, msg)`.
- `_handle_callback`: `if data.startswith("act:"):
  telegram_activity.handle_activity_callback(self, cq); return` — inserido
  junto do dispatch de `"proj:"`, depois da checagem de autorização.
- Extrair `_cancel_run_or_loop(self, msg, run_id) -> str`: corpo idêntico ao
  bloco hoje inline no branch `/cancelar` de `_handle_update` (que já tenta
  `cancel_channel_execution` e cai para `telegram_loop.cancel_loop`); o
  branch `/cancelar` passa a chamar esse método; comportamento e testes
  existentes de `/cancelar` não podem mudar.

## Testes (hermético, `RunManager`/`EventBus` reais sobre `tmp_path`)

`tests/test_telegram_activity.py`, seguindo o padrão de
`tests/test_chat_activity.py`:

- `render_activity_card`: formatação, truncamento, custo desconhecido vs.
  conhecido, duas delegações ao mesmo agente aparecem em linhas separadas.
- `send_activity_card`: sem run ativo → mensagem amigável sem teclado; chat
  não-privado/não-autorizado → recusa; cartão de uma sessão nunca mostra
  dados de outra (duas sessões, dois runs, cada `/atividade` só vê o seu).
- Observador: tick sem mudança não chama `editMessageText` (conta chamadas
  fake); mudança real dispara edição; falha simulada numa edição
  intermediária (fake `_api` levanta uma vez) não impede a edição final
  quando o run vira terminal; teto de 2h interrompe a observação mesmo com
  run ainda não-terminal (usar relógio injetável/monkeypatch de
  `time.monotonic`, não `sleep` real de 2h).
- Botão `Atualizar`: token válido reedita; token expirado/de outro chat/
  usuário é ignorado.
- Botão `Cancelar tarefa`: mesmo resultado observável do comando `/cancelar
  <run_id>` para os mesmos cenários (0/1/2+ candidatos) — reusar os testes já
  existentes de `/cancelar` como referência de comportamento esperado.
