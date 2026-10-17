# SPEC — Aprovação no chat e retomada da execução

## Objetivo

Correção de allowlist (2026-10-17): um comando como `playwright` fora da lista
deve gerar aprovação estruturada também em audit. Uma decisão once autoriza
somente a chamada; run fica na run/sessão e always é a única opção persistente.
Policy deny, denylist e safe_mode continuam bloqueantes.

Quando uma execução do `bauer serve` exigir aprovação humana, o pedido deve
aparecer no próprio Chat em tempo real. Ao aprovar pelo cartão do Chat ou pela
aba `Approvals`, a mesma run deve sair de `waiting_approval`/`queued` e
continuar sua execução, sem exigir que o usuário repita a mensagem.

## Escopo

- Emitir um evento SSE público e sanitizado para uma aprovação pendente.
- Renderizar cartão de aprovação inline no `desktop` com Aprovar/Negar.
- Fazer o `/stream` aguardar a decisão e iniciar o worker após aprovação.
- Manter uma tool já em execução aguardando a aprovação criada por sua policy,
  sem converter a solicitação em uma resposta final de bloqueio.
- Preservar a rota REST da aba `Approvals` e tornar a retomada idempotente.
- Cobrir o contrato com testes de backend e validação de build TypeScript.

## Fora de escopo

- Desativar regras de risco ou aprovar automaticamente outras sessões.
- Criar um novo canal de notificações fora do SSE do Chat.
- Reprocessar runs antigas sem os dados necessários para reconstruir o turno.

## Skills obrigatórias

- `spec-driven-project-setup`: rastreabilidade SPEC → arquitetura → backlog.
- `computer-use`: somente se for necessária validação visual no desktop.

## Sub-agents recomendados

- `backend-implementer`: revisar o ciclo de vida da run e o SSE.
- `test-engineer`: revisar regressões de aprovação e concorrência.
- `code-reviewer`: revisar segurança, idempotência e exposição de dados.

## Critérios de aceite

1. Um `/stream` bloqueado por aprovação envia `event: approval` com id, run,
   sessão, operação, ferramenta, risco e motivo, sem payload sensível.
2. O Chat mostra o pedido sem transformar metadados internos em texto comum.
3. Aprovar ou negar no cartão chama a mesma API REST já usada pela aba.
4. Aprovar retoma a run e permite que o turno termine sem nova mensagem.
5. Negar finaliza a run como falha controlada e informa o Chat.
6. Aprovação já resolvida não cria um segundo worker.
7. Uma aprovação criada durante `run_command`/tool calling aparece no Chat e a
   própria chamada continua depois da decisão.
8. Os testes existentes continuam passando e o build do desktop não apresenta
   erros TypeScript.

## Plano de validação

- Testes unitários do kernel/endpoint para transição e retomada.
- Teste SSE para o evento `approval` e a decisão.
- `uv run pytest tests/ -q --tb=short`.
- `uv run ruff check bauer/ --select E9,F63,F7,F82`.
- `npm run build` no diretório `desktop`.

## Revisão após investigação do fluxo completo

Evidência: mais de vinte comandos aprovados na mesma run, Compose ausente,
comandos com `&&` passados como argumentos de um único processo, healthcheck
no localhost do container e porta Windows ocupada por outro serviço.

- Manter aprovação de uma chamada como default; oferecer escolha explícita de
  aprovar `run_command` durante a run atual. Não persiste para outro turno.
- Negação por policy/denylist continua vencendo qualquer autorização.
- Rejeitar sintaxe de shell não suportada antes de pedir aprovação.
- Descrever no Agno o contrato de execução, workspace e rede Docker.
- Instalar e verificar Compose na imagem.
- Testar duas tools reais consecutivas no Agno com uma decisão por tarefa,
  isolamento entre runs, negação, repetição de decisão e cancelamento.
- Diferenciar execução de comando bem-sucedida de aplicação validada.
