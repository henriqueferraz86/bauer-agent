# Plan 052: Corrigir a paridade entre documentação, CLI e ambiente de desenvolvimento

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 051 (DONE)
- **Category**: documentation, developer experience
- **Planned at**: commit `334827c`, 2026-08-25
- **Completed**: merge `c1f75d2` on `master`, 2026-08-25

## Objective

Eliminar instruções oficiais que não podem ser executadas na CLI atual ou que
criam ambiente de desenvolvimento diferente do CI.

## Evidence

- `docs/BETA_CLOSED.md` instrui `bauer schedule` e `bauer worker`, enquanto a
  CLI registra `cron` e os comandos antigos não existem.
- `AGENTS.md` e CI exigem `uv sync --frozen --extra dev`, mas trechos de
  `README.md` e comentários de desenvolvimento em `pyproject.toml` ainda
  instruem `pip install -e` ou omitem `--frozen`.
- A documentação deve ser verificável por `--help`; não deve sugerir comandos
  removidos nem uma resolução de dependências diferente do lock versionado.

## Scope

- `README.md`
- `docs/BETA_CLOSED.md`
- `pyproject.toml` (somente comentários/documentação de desenvolvimento)
- testes de documentação/CLI somente se já houver infraestrutura leve adequada

Fora de escopo: alterar a superfície da CLI, dependências, lockfile, CI,
funcionalidade de cron/runtime ou documentação de usuário final que não seja
ambígua como instrução de desenvolvimento.

## Required behavior

1. O roteiro beta deve usar somente comandos existentes e mostrar a sequência
   atual equivalente para criar, listar, executar e remover cron, além do
   runtime/daemon quando aplicável.
2. Toda instrução destinada a contribuidores deve usar `uv sync --frozen
   --extra dev` e comandos subsequentes por `uv run`. Instalação por pip, se
   mantida para usuários finais, deve ser claramente distinguida de setup de
   desenvolvimento e não aparecer como forma de reproduzir CI.
3. Links, exemplos e opções devem ser checados contra `uv run bauer --help` e
   os subcomandos relevantes, sem rede/provider real.

## Verification

- `uv run bauer --help`
- `uv run bauer cron --help`
- `uv run bauer runtime --help`
- busca por comandos removidos/instruções de dev contraditórias nos arquivos
  modificados
- `uv run pytest tests/ -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
- `git diff --check`

## Done criteria

- Nenhum tutorial operacional oficial chama comando inexistente.
- O caminho de contribuição reproduz o lock/CI sem ambiguidade.
- Mudança é apenas documental, validada contra a CLI atual e pronta para merge.

## STOP conditions

- O equivalente atual de um comando removido não cobre a promessa do tutorial;
  nesse caso documente a lacuna, não invente comportamento.
- A correção requer alterar CLI, CI ou dependências fora do escopo.

## Commit

`docs: align beta runbook and development setup`

## Execution record

- O roteiro beta usa agora `uv run bauer` e os comandos atuais de `cron`,
  `runtime` e `dispatch`; os exemplos foram verificados por `--help`.
- O setup de contribuição e a orientação para regenerar a dívida de mypy usam
  `uv sync --frozen --extra dev`, igual ao CI.
- A suíte completa, o Ruff crítico e a verificação de diff passaram. O Ruff
  amplo mantém o baseline preexistente de 87 ocorrências.
