# Backlog — Sprint 09

- [x] Criar contrato JSONL versionado e `PluginProcess` com handshake,
  timeout, limite de mensagem e shutdown idempotente.
- [x] Criar `plugin_worker` mínimo para carregar somente entrypoints validados
  e responder `hello`, `health`, `event` e `shutdown`.
- [x] Criar `PluginBroker` com política de capability/permissão, estado,
  restart limitado e prevenção de processos duplicados.
- [x] Integrar hooks gerenciados ao broker, preservando compatibilidade apenas
  com flag explícita.
- [x] Adicionar testes de falha, timeout, payload inválido, autorização,
  restart e shutdown em `tmp_path`.
- [x] Atualizar documentação operacional e executar suíte/gates completos.

Status: concluído na branch `codex/sprint-09-plugin-isolation`.
