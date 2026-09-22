# ARCHITECTURE — Sprint 17: rastreabilidade e painel de agentes

## Visão geral

O adapter Agno solicita eventos detalhados ao SDK e converte os eventos de
membros em um contrato pequeno do Bauer. O Kernel publica esse contrato no
EventBus já associado ao RunManager; EventBus persiste e redige dados conforme
o caminho normal. A API de observabilidade agrega os eventos recentes por
agente e estado do run. O frontend consulta a agregação em intervalos curtos.

## Componentes

- `AgnoRuntimeAdapter`: identifica eventos do time e de seus membros, preserva
  somente metadados necessários e mantém deltas apenas para a resposta final.
- `BauerKernel.stream`: publica eventos tipados de agentes e tools no EventBus
  sem mudar a custódia nem os gates do Kernel.
- `desktop_api`: fornece atividade agregada de agentes a partir dos runs e
  eventos persistidos.
- `desktop/src/screens/Agents.tsx`: mostra catálogo e status atualizado.
- `desktop/src/screens/Runs.tsx`: atualiza o timeline enquanto o run estiver
  ativo.

## Fluxo

```text
Agno Team stream_events
  → AgnoRuntimeAdapter normaliza eventos (sem conteúdo sensível)
  → BauerKernel publica no EventBus
  → EventBus persiste /api/obs/agent-activity
  → Agents e Runs consultam e mostram trabalho recente/em curso
```

## Decisões técnicas

- Usar o EventBus e RunManager existentes como fonte única de rastreabilidade.
- Associar cada evento ao `agent_id` do membro, `run_id` Bauer e `team_id`;
  guardar o ID de run Agno como metadado secundário.
- `RunContent` de membro indica atividade, mas seu texto não será persistido
  nem misturado à resposta final do coordenador.
- Falha ao publicar telemetria continua sendo best-effort, conforme as regras
  do Kernel.
- O painel consulta resumo agregado, não todo o histórico ilimitado.

## Riscos técnicos

- Eventos do SDK podem variar entre tipos de Team e Agent; normalização deve
  tolerar campos ausentes e ignorar eventos desconhecidos.
- Streams de membros podem intercalar; status é correlacionado por run e ID do
  agente, sem assumir ordem global entre membros.
- Falhas ou cancelamentos do run pai encerram a atividade aparente de seus
  membros, mesmo se um evento terminal do membro não tiver chegado.

## Observabilidade

- Eventos `agent.started`, `agent.message.sent`, `agent.completed`,
  `agent.failed`, `task.delegated` e eventos de tool existentes.
- API retorna status atual, contagem de execuções e última utilização, sem
  expor conteúdo de usuário ou do modelo.
