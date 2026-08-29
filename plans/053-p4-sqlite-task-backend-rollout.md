# Plan 053: Rollout seguro e opt-in do backend SQLite de tarefas

## Status

- **Priority**: P4
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: 052 (DONE)
- **Category**: data migration, reliability, developer experience
- **Planned at**: commit `f4677a8`, 2026-08-25
- **Completed**: merge `9c94778` on `master`, commit `ba236a5`, 2026-08-25

## Objective

Transformar a infraestrutura pronta de task backend SQLite em uma migração
operacional segura por workspace: diagnosticar, migrar de forma realmente
idempotente, preservar backup e ativar somente após validação explícita.

## Evidence

- `agent.task_backend` ainda é `markdown` por default, embora a factory já
  encaminhe todos os consumidores quando configurada como `sqlite`.
- `bauer kanban-migrate` já preserva ids e tarefas, mas
  `bauer/kanban_migration.py` declara que reexecuções podem duplicar
  comentários append-only — incompatível com o contrato de migração
  idempotente necessário ao rollout.
- O board derivado de workspace já é compartilhado pela factory e pelo comando
  de migração, evitando escrever num board diferente daquele que será lido.

## Scope

- `bauer/cli.py` (`kanban-migrate`)
- `bauer/kanban_migration.py`
- documentação mínima do comando/config se necessária
- testes de migração e CLI (`tests/test_kanban_migration.py`,
  `tests/test_cli_boards.py` ou vizinhos)

Fora de escopo: trocar o default global para SQLite, apagar `TASKS.md`, criar
espelhamento bidirecional SQLite→Markdown, migrar boards arbitrários sem um
workspace, ou alterar a API pública do `WorkspaceManager`.

## Required behavior

1. Reexecutar a migração sobre a mesma origem não duplica tarefas, links,
   metadados nem comentários. O relatório deve deixar claro inseridos versus
   já presentes.
2. `bauer kanban-migrate --activate` deve ser opt-in e aceitar `--config`.
   Deve: criar backups recuperáveis de `TASKS.md` e do config antes de escrever;
   migrar para o board do workspace; validar que todos os IDs de origem estão
   presentes no destino e que não houve erros; somente então gravar
   `agent.task_backend: sqlite`.
3. `--dry-run --activate` não pode criar board, backup ou alterar config; deve
   exibir o que seria migrado, validado, copiado e ativado.
4. Se a origem não existir, a migração tiver erro, a validação falhar ou backup
   não puder ser criado, a flag não muda. Não substitua backup existente.
5. A saída deve informar que rollback da flag preserva o `TASKS.md`, mas que
   tarefas criadas após a ativação permanecem no SQLite; não alegar reversão
   bidirecional inexistente.
6. O default de instalações e configs existentes permanece `markdown`; a
   mudança é por comando explícito e por config escolhido pelo usuário.

## Verification

- testes de migração em repetição com comentários e links
- testes de CLI para dry-run+activate sem efeitos, activation com backups e
  config, e falha que não ativa
- `uv sync --frozen --extra dev`
- `uv run pytest tests/test_kanban_migration.py tests/test_cli_boards.py tests/test_workspace_manager_factory.py -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
- `uv run ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303`
- `uv run pytest tests/ -q --tb=short`
- `git diff --check`

## Done criteria

- A adoção de SQLite é segura, explícita e reversível no nível de configuração
  sem destruir os dados Markdown.
- A migração é de fato idempotente, inclusive comentários.
- Nenhuma configuração é ativada se a migração/validação/backup não completar.
- Gates passam; baseline de lint amplo só pode ser aceito se idêntico à master.

## STOP conditions

- A ativação segura exige modificar a configuração de outro diretório sem o
  usuário informar o caminho, ou não há modo de criar backup sem sobrescrever
  dado existente.
- Garantir idempotência de comentários requer remodelar ou apagar histórico
  SQLite existente sem rota de compatibilidade.

## Commit

`feat(tasks): add safe SQLite backend activation`
