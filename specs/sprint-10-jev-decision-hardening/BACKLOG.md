# BACKLOG — Sprint 10: Decisão Jev

## Tarefas

### 1. Contrato e configuração

Status: concluído

- Criar `DecisionSection` estrita.
- Aplicar `TYPESAFE_API_KEY`.
- Atualizar lock e documentação de configuração.

### 2. Cliente e fallback

Status: concluído

- Implementar cliente HTTP sem dependência nova.
- Validar resposta e confiança.
- Garantir fallback sem rede.

### 3. Integração nos caminhos

Status: concluído

- Integrar em `agent`, `serve` e `run`.
- Publicar origem/confiança no evento de rota.

### 4. Correções de qualidade

Status: concluído

- Corrigir erros mypy bloqueantes do runtime/plugin.
- Adicionar testes unitários herméticos.

### 5. Validação

Status: em validação

- Rodar suíte completa, Ruff, mypy e `uv lock --check`.
- Revisar diff e confirmar ausência de secrets.
