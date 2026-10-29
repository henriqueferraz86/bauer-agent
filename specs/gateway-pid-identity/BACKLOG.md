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

Status: em andamento

Critérios:
- Testes unitários passam.
- Gateway e Telegram ativos depois de reiniciar o container.
