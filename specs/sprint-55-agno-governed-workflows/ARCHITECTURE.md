# ARCHITECTURE — Sprint 55

```text
GovernedWorkflow.run(kernel, agent_id, input)
  └─ run_governed(kernel, executor, runtime_adapter="agno_workflow")
       Kernel: open run → preflight → running
       executor(payload{run_id}):
         ctx = WorkflowContext(run_id, session, deadline, kernel, approvals, input)
         Workflow(steps=[Step(executor=wrap(spec), max_retries=0)…], db=None).run()
           wrap(spec):
             ctx.check()  ── stopped? falhou antes? run terminal? cancel? prazo?
             publish agent.started
             spec.fn(ctx, ctx.input)   (review → ctx.request_review)
             publish agent.finished | agent.failed | agent.cancelled
         return {"output": …} | {"status": "failed"|"cancelled", "error": …}
       Kernel: gates → completed | failed | cancelled
```

`ctx.request_review`:

```text
approvals.request(operation="workflow.review", suspend_run=True)
  run: running → waiting_approval            (publish agent.paused)
loop (50 ms): checkpoint → status da aprovação → status do run → prazo
  approved  → authorize(resume_run=True): queued → running (agent.resumed)
  denied/expired → failed;  run cancelled → cancelled;  timeout → expire → failed
```

Decisões:

- `ctx.input` é a entrada original; o Agno pode entregar às etapas o
  conteúdo da etapa anterior, e as etapas não dependem disso.
- `ctx.values` (rascunho compartilhado) e `ctx.outputs` (retorno por etapa)
  ficam só em memória; nada vai para eventos.
- Condições só avaliam o predicado se não houve falha nem parada.
