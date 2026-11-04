# BACKLOG — Sprint 44: `/loop` autônomo no Telegram

- [ ] `bauer/channel_base.py`: `_resolver_kernel(self, workspace: Path | None
      = None)` — parâmetro opcional, comportamento atual preservado quando
      omitido (ver ARCHITECTURE.md).
- [ ] Novo `bauer/telegram_loop.py`:
  - [ ] `TelegramLoopRecord` (dataclass) + `TelegramLoopStore` (load/save
        JSON, `os.chmod(0o600)`, expurgo >7 dias, lookup por `update_id` e
        por `run_id`/`chat_id`).
  - [ ] `start_loop(backend, msg, update_id, goal) -> str` (texto de resposta
        pronto para `send_text`; persiste e inicia a thread conforme o fluxo
        de admissão da ARCHITECTURE.md).
  - [ ] `reconcile_pending(bridge) -> None` (chamado em `TelegramBridge.
        start()`, antes do polling).
  - [ ] `cancel_loop(backend, chat_id, run_id) -> str | None` (`None` = nenhum
        loop correspondente, deixa o caller cair no texto genérico).
- [ ] `bauer/telegram_bridge.py`:
  - [ ] `BOT_COMMANDS`: adicionar `/loop`.
  - [ ] `_handle_update`: branch `/loop` (privado + autorizado; sem goal →
        uso), chamando `telegram_loop.start_loop` sincronamente.
  - [ ] `_handle_update`: estender branch `/cancelar` com fallback para
        `telegram_loop.cancel_loop`.
  - [ ] `start()`: chamar `telegram_loop.reconcile_pending(self)` antes do
        `while not self.stopped`.
- [ ] `tests/test_telegram_loop.py` (ver cenários na ARCHITECTURE.md).
- [ ] Atualizar `plans/061-telegram-project-control.md` (status da Etapa 5)
      e `plans/README.md` após merge.
- [ ] `uv run pytest tests/ -q` e
      `uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82`
      verdes antes de considerar concluído.

## Riscos conhecidos (documentar, não resolver aqui)

- Gap de ordenação offset-antes-de-concluir para mensagens/comandos que NÃO
  são `/loop` continua existindo (pré-existente, fora de escopo — ver SPEC).
- Reconciliação depende do `RunManager` estar acessível no mesmo processo do
  gateway Telegram logo no startup; se `kernel.enabled` estiver desligado
  (`_resolver_kernel` devolve `None`), `/loop` deve recusar com uma mensagem
  clara em vez de estourar exceção.
- Outra sessão concorrente está editando `channel_base.py`,
  `telegram_bridge.py` e `channel_execution.py` em paralelo (trabalho de
  aprovação remota, Etapa 4). Este sprint roda em worktree isolado a partir
  de `origin/master` e NÃO deve importar/depender de nada não commitado por
  essa outra sessão. Ao mesclar, revisar diffs de `channel_base.py`/
  `telegram_bridge.py` linha a linha contra o `master` real (não confiar em
  números de linha antigos).
