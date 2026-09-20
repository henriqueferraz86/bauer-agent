# Sprint 09 — Isolamento de Plugins

## Problema

O runtime de plugins valida manifesto e AST, mas os hooks legados ainda são
importados dentro do processo principal do Bauer. Um plugin com loop infinito,
crash, bloqueio de I/O ou efeito colateral durante o import pode degradar a
sessão inteira.

## Objetivo

Executar plugins gerenciados em processos filhos controlados, com protocolo
IPC mínimo, timeout por chamada e desligamento seguro. O processo principal
deve continuar funcional quando um plugin falha.

## Escopo

- Runner de plugin em processo separado, iniciado somente para plugin habilitado.
- Protocolo JSONL versionado para healthcheck, registro de hooks e dispatch.
- Timeout, encerramento e reinício limitado do processo filho.
- Broker explícito para eventos permitidos pelo manifesto.
- Migração gradual: plugins legados continuam disponíveis apenas por modo
  compatibilidade explicitamente configurado.
- Testes herméticos com plugins temporários, sem rede ou provider real.

## Fora de escopo

- Sandbox de kernel/container multiplataforma.
- Permitir que o plugin execute ferramentas Bauer diretamente no processo filho.
- Expor secrets, objetos Python ou callbacks arbitrários pelo IPC.
- Migrar automaticamente plugins legados sem manifesto.

## Skills obrigatórias

- spec-driven-project-setup
- security-review
- test-strategy

## Sub-agents recomendados

- backend-implementer
- security-reviewer
- test-engineer
- code-reviewer

## Requisitos funcionais

1. Cada plugin gerenciado deve ter um processo filho identificável e um estado
   operacional (`starting`, `ready`, `unhealthy`, `stopped`).
2. O processo principal deve impor timeout e tamanho máximo de mensagem.
3. Falha, saída inesperada ou protocolo inválido deve desabilitar o plugin na
   chamada atual e registrar motivo sem derrubar o agente.
4. Eventos enviados ao plugin devem ser filtrados pela capability e pelas
   permissões do manifesto.
5. O filho não pode declarar conclusão de uma operação Bauer; ele somente
   recebe evento e devolve resultado serializável.
6. O modo legado deve ser opt-in e emitir aviso operacional claro.

## Critérios de aceite

- Um plugin que trava ou excede o timeout não bloqueia uma segunda chamada do
  agente.
- Um plugin que escreve resposta IPC inválida é marcado como `unhealthy`.
- Eventos sem capability/permissão são recusados antes de atravessar o broker.
- Restart é limitado e não cria processos duplicados.
- O estado não persiste secrets nem payloads completos de prompt/resposta.
- Testes específicos, suíte completa e gates do `AGENTS.md` passam.

## Plano de validação

- Testar handshake, healthcheck, dispatch permitido e dispatch negado.
- Testar timeout, processo encerrado, JSON inválido e limite de payload.
- Testar shutdown e restart idempotentes em Windows e Linux quando possível.
- Rodar `uv sync --frozen --extra dev`, suíte completa e os dois comandos Ruff
  definidos no `AGENTS.md`.
