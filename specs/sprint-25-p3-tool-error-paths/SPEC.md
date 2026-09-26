# SPEC — Sprint 25: caminhos de erro de filesystem nas tools

## Problema

O plano P3 de cobertura de error-paths das tools cita arquivo ausente,
timeout e permissão. A inspeção inicial encontrou testes existentes para
arquivo ausente e timeout, mas as operações de filesystem não têm contrato
testado para `PermissionError`/falhas de I/O. Algumas exceções podem escapar
sem conversão para `ToolError`, dificultando a recuperação pelo agente.

## Objetivo

Garantir que falhas de permissão e I/O nas tools de filesystem sejam reportadas
como erros de tool compreensíveis, preservando a causa e sem mascarar falhas de
programação ou alterar a semântica de sucesso.

## Escopo

- Auditar as operações de leitura, escrita, append, patch, listagem, criação de
  diretório e remoção expostas por `bauer/tools/fs.py`.
- Adicionar testes determinísticos para falhas de leitura, escrita e operações
  de diretório/arquivo, usando monkeypatch em vez de ACL/`chmod` dependente do SO.
- Converter `OSError` de I/O em `ToolError` no limite das tools afetadas.
- Manter os testes existentes para arquivo ausente, timeout e sandbox como
  regressão; não duplicar os casos já cobertos.

## Fora de escopo

- Mudanças em política de sandbox, autorização ou escopo de paths.
- Alterar tools de rede, subprocessos, canais ou providers.
- Criar suporte a novos tipos de arquivo ou mudar mensagens de sucesso.
- Tentar simular ACL real em CI cross-platform.

## Skills obrigatórias

- spec-driven-project-setup
- test-strategy
- python-service-pattern
- security-review

## Sub-agents recomendados

- backend-implementer: limite de conversão dos erros de filesystem;
- test-engineer: cenários determinísticos e regressões;
- security-reviewer: garantir que mensagens não revelem paths/segredos indevidos;
- code-reviewer: aderência ao contrato `ToolError` e ausência de duplicação.

## Requisitos funcionais

1. Falha de permissão durante operação de filesystem não escapa como exceção
   crua de biblioteca; retorna `ToolError` com operação e path contextualizados.
2. Erros de I/O são encadeados (`raise ... from exc`) para diagnóstico interno.
3. `FileNotFoundError` já tratado pelo código mantém comportamento compatível.
4. Exceções de programação não relacionadas a `OSError` não são engolidas.
5. Testes não dependem de privilégios, plataforma, rede ou filesystem real com
   permissões especiais.

## Critérios de aceite

- [x] Casos representativos de leitura, escrita, append, patch, diretório e
  remoção exercitam `PermissionError` e verificam `ToolError`.
- [x] Os testes preexistentes de filesystem, sandbox, arquivo ausente e router
  continuam passando.
- [x] Ruff bloqueante e suíte completa passam no ambiente prescrito pelo repo.
- [x] Revisão confirma que o tratamento não mascara erros inesperados.

## Plano de validação

1. `uv sync --frozen --extra dev`
2. Testes focados da superfície filesystem/tool router.
3. `uv run pytest tests/ -q --tb=short`
4. Os dois comandos Ruff prescritos em `AGENTS.md`.
5. `git diff --check`.
