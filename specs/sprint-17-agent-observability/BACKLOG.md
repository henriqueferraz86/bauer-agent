# BACKLOG — Sprint 17: rastreabilidade e painel de agentes

## Tarefas

### 1. Formalizar contrato e segurança da telemetria

Status: concluída

- [x] Definir eventos de ciclo de vida e metadados seguros.
- [x] Reutilizar EventBus e execução governada pelo Kernel.

### 2. Normalizar eventos Agno no adapter e persistir no Kernel

Status: concluída

- [x] Ativar stream completo de eventos e registrar atividade de membros.
- [x] Preservar conteúdo final sem registrar texto ou payloads de tool.
- [x] Cobrir normalização e publicação com testes.

### 3. Disponibilizar atividade agregada pela API

Status: concluída

- [x] Retornar estado, contagem e última execução por agente.
- [x] Resolver runs terminais sem depender de eventos terminais de cada membro.

### 4. Exibir agentes trabalhando e timeline ao vivo

Status: concluída

- [x] Atualizar status do catálogo Agents periodicamente.
- [x] Atualizar eventos da execução selecionada enquanto estiver ativa.
- [x] Apresentar evento, agente e hora em rótulos legíveis.

### 5. Revisar e validar

Status: concluída

- [x] Revisão de segurança da telemetria.
- [x] Build TypeScript e testes relevantes.
- [x] Executar gates indicados no `AGENTS.md`.

### 6. Registrar oportunidade de expansão das tools Agno

Status: concluída

- [x] Comparar o catálogo oficial com o mapeamento atual do adapter Bauer.
- [x] Priorizar integrações por utilidade e risco, sem conceder todas por padrão.
- [x] Documentar a sequência de integração e os requisitos de policy/telemetria.
