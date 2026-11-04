# BACKLOG — Sprint 45: `/atividade` no Telegram

- [ ] Novo `bauer/telegram_activity.py`:
  - [ ] `render_activity_card(envelope_run, run) -> str` (pura, custo via
        `run.cost_estimate`/`run.usage_known`, nunca "US$ 0,00" quando
        desconhecido).
  - [ ] `_activity_keyboard(token, cancelable) -> dict`.
  - [ ] `send_activity_card(bridge, msg) -> None`.
  - [ ] `_watch_activity(...)` — poll a cada 2,5s, edita só se mudou, teto de
        2h, edição final incondicional em terminal/teto.
  - [ ] `handle_activity_callback(bridge, cq) -> bool` (`act:refresh:<token>`,
        `act:cancel:<token>`).
- [ ] `bauer/telegram_bridge.py`:
  - [ ] `BOT_COMMANDS`: adicionar `/atividade`.
  - [ ] `__init__`: `_activity_callbacks` + lock.
  - [ ] `_handle_update`: branch `/atividade`.
  - [ ] `_handle_callback`: dispatch `"act:"`.
  - [ ] Extrair `_cancel_run_or_loop(self, msg, run_id) -> str` do branch
        `/cancelar` existente; reusar no botão novo; `/cancelar` sem mudança
        de comportamento (testes existentes continuam passando sem edição).
- [ ] `tests/test_telegram_activity.py` (ver cenários na ARCHITECTURE.md),
      usando `RunManager`/`EventBus` reais sobre `tmp_path` (padrão de
      `tests/test_chat_activity.py`).
- [ ] Rodar a suíte completa de `/cancelar` existente sem modificação de
      asserts, para comprovar que a extração do helper não mudou
      comportamento.
- [ ] Atualizar `plans/061-telegram-project-control.md` (status da Etapa 3,
      checklist) e `plans/README.md` após merge.
- [ ] `uv run pytest tests/ -q` e
      `uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82`
      verdes antes de considerar concluído.

## Riscos conhecidos (documentar, não resolver aqui)

- Outra sessão concorrente tem mudanças grandes e não commitadas em
  `bauer/telegram_bridge.py` e `bauer/channel_base.py` (trabalho da Etapa 4,
  aprovação remota). Este sprint parte de `origin/master` limpo (commit
  `3121bfa`) em worktree isolado e não deve depender de nada que essa sessão
  ainda não tenha mergeado. Ao integrar essa outra sessão depois, revisar
  `_handle_callback`'s dispatch por prefixo com cuidado — os dois trabalhos
  adicionam prefixos de callback (`"act:"` aqui, provavelmente algo como
  `"appr:"` na Etapa 4) que precisam coexistir sem colidir.
- A thread observadora é só UX (igual ao outbox do plano 063): não garante
  entrega da edição final em caso de crash do processo do gateway — mesma
  limitação documentada no plano 063 e no sprint 44, não é um regresso novo.
- `/atividade` sem Kernel habilitado (`_resolver_kernel() is None`) deve
  recusar com mensagem clara, igual ao `/loop` do sprint 44 — não crashar.
