# ARCHITECTURE — Sprint 56

```text
EventBus (tool.call.completed / failed / denied)
   └─ trace_from_events(run_id) → [TraceCall(name, ok, args?)]
        └─ evaluate_tool_use → RunOutput(tools=[ToolExecution…]) sintético
             └─ agno.eval.ReliabilityEval(telemetry=False, db=None).run()
                  → ReliabilityVerdict (consultivo)

CapabilityDecisionLog(DecisionMemory)
   record(kind, agent_id, selected, alternatives, outcome, evidence, feedback, scope, ttl)
     → DecisionMemory.record(context="<kind>:<agent>", decision=JSON v1,
                             tags=[agno-decision, scope:<s>, kind:<k>, v:1])
   list(scope) / delete(id) / purge_expired() / purge_scope(scope)
```

- Escopo, versão e expiração vivem nas tags/JSON — o schema SQLite do
  `DecisionMemory` não muda (sem migração, sem ADR).
- `DecisionSettings` registrado como seção `bauer.decisions` de
  `agno_capabilities.yaml` (estrito).
- Cenários do harness em `evals/harness/scenarios/agno_evals.py`, carregados
  por `carregar_tudo()`.
