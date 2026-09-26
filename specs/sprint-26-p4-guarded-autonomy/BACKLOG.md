# BACKLOG — Sprint 26: autonomia P4 com freios verificáveis

## Tarefas

### 1. Gate determinístico do caminho autopilot → task concluída

Status: concluída

Critérios:
- Mapear precisamente a conclusão de `bauer run` e o gate disponível.
- Estabelecer prova estruturada do resultado; não parsear texto livre.
- Falha/inconclusividade nunca conclui task nem goal como sucesso.
- Guardar modo manual/supervisionado conforme política atual.

### 2. Budget de missão persistente e efetivo

Status: concluída

Critérios:
- Contabilizar uso por task/run de forma idempotente e atômica.
- Budget permanece após restart; nenhum worker novo após exaustão.
- Aplicar tempo/custo/tools no limite do worker, além de pré-admissão.
- Definir fail-closed para custo desconhecido com teto financeiro ativo.

### 3. Dependências de marcos em todos os caminhos de despacho

Status: concluída

Critérios:
- O planner/materializador grava dependências idempotentes.
- SQLite honra todos os pais; Markdown honra pai único.
- Dispatcher verifica precedentes em claim e dry-run, com fail/blocked/missing.
- Testes de ciclo, múltiplos pais, restart e replan.

### 4. Revisão, documentação e validação final

Status: concluída

Critérios:
- Revisão independente de segurança e arquitetura.
- Runbook e roadmap refletem apenas capacidades provadas.
- Suíte completa, Ruff, gates de arquitetura e diff-check passam.
