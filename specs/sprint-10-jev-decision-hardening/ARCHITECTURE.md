# ARCHITECTURE — Sprint 10: Decisão Jev

## Visão geral

O Bauer mantém o `classify_task()` como estratégia determinística. Uma nova
camada `decision_router` decide qual estratégia usar e normaliza o resultado.
O Jev só produz dados de decisão; o chamador continua responsável por resolver
client/model, aplicar policy e executar pelo caminho existente.

```text
mensagem
   ↓
DecisionRouter
   ├─ JevDecisionClient (opt-in, HTTP, timeout curto)
   └─ heuristic classify_task (fallback)
   ↓
RouteDecision normalizada
   ↓
agent / serve / run
   ↓
ModelRouter, Team/Kernel, policy e execução existentes
```

## Contrato de decisão

O resultado contém `task_type`, `complexity`, `profile`, `reason`, `confidence`,
`source` e `error`. O conjunto de perfis é fechado em
`fast|balanced|coding|heavy`; qualquer saída externa inválida cai no fallback.

## Segurança e confiabilidade

- Jev fica desligado por padrão.
- API key somente em ambiente/configuração e nunca em logs ou eventos.
- Timeout separado da chamada do modelo.
- Sem fallback para provider diferente: fallback é apenas de decisão.
- Perfil escolhido nunca autoriza uma tool nem altera aprovação.

## Observabilidade

O evento existente `model.route.selected` recebe `source` e `confidence`,
sem incluir estado sensível nem a chave. Falhas Jev são logadas em DEBUG com
mensagem sanitizada e viram decisão heurística.

## Compatibilidade

Nenhuma configuração existente é alterada. A seção nova usa `extra="forbid"`
como as demais seções. Sem `decision:` o Bauer se comporta como antes.
