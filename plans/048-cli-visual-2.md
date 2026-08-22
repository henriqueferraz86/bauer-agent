# 048 — Bauer CLI Visual 2.0

**Status:** DONE
**Objetivo:** consolidar a experiência textual da CLI sem perder os contratos de
governança, os dados reais e a degradação segura para ASCII/sem cor.

## Escopo

1. Estender `bauer/ui.py` com componentes semânticos para cabeçalho, avisos,
   progresso e resumo final.
2. Migrar `bauer run`, `bauer agent`, onboarding e o help raiz para estes
   componentes e para a paleta única de `bauer/theme.py`.
3. Introduzir preferências `ui.mode` e `ui.emojis`, preservando `BAUER_UI=plain`.
4. Cobrir os novos renderizadores e os três modos em testes; executar a suíte
   indicada por `AGENTS.md` antes de concluir.

## Fora de escopo

- Alterar protocolos, governança, orçamento, conteúdo de respostas do modelo ou
  contratos de saída JSON.
- Migrar comandos administrativos de baixa frequência nesta fatia.

## Critérios de pronto

- `agent`, `run` e onboarding compartilham tokens, glifos e semântica.
- Nenhum resumo declara arquivos/testes/métricas que não foram fornecidos.
- Rich, compact, plain, `NO_COLOR`, pipe e cp1252 continuam legíveis.
- Suíte completa e lint bloqueante definidos em `AGENTS.md` passam; o lint
  amplo não introduz achados nesta fatia.

## Verificação concluída

- `uv sync --frozen --extra dev`
- `uv run pytest tests/ -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
- `git diff --check`

O lint amplo `E,F,W` mantém falhas pré-existentes fora desta fatia, sobretudo
em `agent.py`, `cli.py`, `indicators.py`, `kanban_db.py` e `tool_router.py`.
