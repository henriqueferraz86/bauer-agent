# BACKLOG — Inicialização confiável do Gateway

## Tarefas

### 1. Validar identidade do PID salvo

Status: concluído

Critérios:
- Confirma o comando `bauer.cli gateway start`.
- Rejeita PID existente de outros comandos.

### 2. Aplicar validação aos inícios em background

Status: concluído

Critérios:
- CLI e gerenciador de serviço compartilham o helper.
- PID obsoleto é removido antes de iniciar.

### 3. Validar inicialização após restart

Status: concluído

Critérios:
- Testes unitários passam.
- 31 testes focados passaram; Ruff crítico e `git diff --check` passaram.
- Após reconstruir/reiniciar o container `bauer-agent`, o runtime iniciou
  automaticamente e o Telegram apareceu online.
- UI e API responderam HTTP 200.
- Tokens Telegram foram removidos dos logs existentes e URLs HTTPX suprimidas.
