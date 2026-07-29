# EXECUTION_PATHS — inventário dos caminhos de execução

**Medido em 2026-07-29** no branch `feat/harness-s7-baseline`. Dados de duas
fontes: leitura de código (quem chama o quê) e **execução real no Beelink**
(`qwen3-coder:30b` via Ollama, `BAUER_HOME` isolado com `kernel.enabled: true`
e `evaluator_enabled: true`).

Entrega do S7 do [plano de harness](PLANO_HARNESS_90.md).

---

## 1. O achado principal: custódia ≠ governança

O Kernel expõe **duas** formas de entrar, com consequências muito diferentes:

| | `execute()` / `stream()` | `admit()` |
|---|---|---|
| preflight (kill-switch, policy, budget) | ✅ | ✅ |
| **quem declara `completed`** | **o Kernel** | **o caller** |
| Evaluator / quality gates | ✅ rodam | ❌ **nunca rodam** |
| retry / fallback de executor | ✅ | ❌ |
| replan por veredito de gate | ✅ | ❌ |

`admit()` é admissão **sem custódia**: existe para motores que o Kernel não
pode envolver (o `/stream` roda o turno em thread órfã com persistência própria
após timeout/desconexão). É uma escolha deliberada e documentada — o problema é
o que ela implica para o critério 4 do plano ("o agente não pode declarar
sucesso sem validação").

### Prova empírica

Dois runs no **mesmo state store**, mesma config, mesmo `evaluator_enabled: true`:

```
run-71bd5a17-9  created -> planning -> policy_check -> queued -> running -> completed
                                                                  ^ evaluating AUSENTE
run-7083ac82-c  created -> planning -> policy_check -> queued -> running -> evaluating -> completed
```

O primeiro é `bauer run` (autônomo, via `admit()`). O segundo é `/chat` (via
`execute()`). **O gate rodou só no segundo.**

`bauer run` fecha o run chamando `kernel.runs.complete_run()` direto
(`commands/run_cmd.py:271`, em `_finalize_run`) — o caller declara sucesso, que
é exatamente o que o critério 4 proíbe.

---

## 2. Tabela dos caminhos

`custody`: `full` = `execute()`/`stream()` · `admit_only` = `admit()` · `none` = fora do Kernel.

| # | Caminho | Kernel | Custódia | Onde | Gate roda? |
|---|---|---|---|---|---|
| 1 | `bauer run` / `/loop` | flag | **`admit_only`** | `commands/run_cmd.py:155` | ❌ |
| 2 | `bauer agent` interativo | flag | `full` | `agent.py:5186` | ✅ |
| 3 | `bauer kernel run` | sempre | `full` | `commands/kernel_cmd.py:68` | ✅ |
| 4 | serve `/chat` | flag | `full` | `server.py:1558` | ✅ |
| 5 | serve `/stream` (SSE) | flag | **`admit_only`** | `server.py:1756` | ❌ |
| 6 | serve (2º endpoint) | flag | **`admit_only`** | `server.py:2148` | ❌ |
| 7 | `serve_loop.py` (loop na UI web) | **não** | `none` | 0 refs | ❌ |
| 8 | `automation_scheduler.py` | **não** | `none` | 0 refs | ❌ |
| 9 | `core/runtime/scheduler.py` | **não** | `none` | `:216` chama `adapter.run_agent` direto | ❌ |
| 10 | `task_dispatcher.py` (993 l.) | **não** | `none` | 0 refs | ❌ |
| 11 | `swarm.py` | **não** | `none` | 0 refs | ❌ |
| 12 | `orchestrator.py` (805 l.) | **não** | `none` | 0 refs | ❌ |
| 13 | `execution_engine.py` | **não** | `none` | 0 refs | ❌ |
| 14 | `app_factory.py` | **não** | `none` | 0 refs | ❌ |
| 15 | `daemon.py` (782 l.) | **não** | `none` | 0 refs | ❌ |

### Números

- `kernel_coverage` (algum contato com o Kernel): **6/15 = 40%** com a flag
  ligada; **0%** na config default (`KernelSection.enabled = False`).
- `kernel_full_custody_coverage` (o Kernel decide `completed`): **3/15 = 20%**.
- Caminhos autônomos **sem nenhuma governança**: **9**.

O documento original estimava 40% de cobertura. O número está certo **por
coincidência** — ele mede contato, não custódia, e é 0% no default. A métrica
que importa para o Validator é a segunda: **20%**.

---

## 3. Ficha por caminho

Formato do plano, com o campo `kernel_custody` adicionado no S7.

```yaml
execution_path: bauer_run          # o /loop autônomo
uses_kernel: true                  # atrás de kernel.enabled
kernel_custody: admit_only         # <-- Evaluator NAO roda
uses_task_contract: false
uses_context_builder: false        # ContextManager instanciado localmente (run_cmd.py:121)
uses_validator: false              # gate nunca executa (medido)
uses_sandbox: false                # roda na pasta atual, sem worktree
supports_cancel: true              # kill-switch entre rodadas via RuntimeControl
supports_recovery: true            # RuntimeRecovery.recover_stuck_runs
declara_completed: caller          # run_cmd.py:271 -> runs.complete_run()
anti_loop: true                    # _detect_loop + ToolCallGuardrailController
observado: 3 rodadas, 7 tools, ~US$ 0.068, tarefa concluída
```

```yaml
execution_path: serve_chat
uses_kernel: true
kernel_custody: full               # <-- Evaluator roda (medido)
uses_task_contract: false
uses_context_builder: false        # server.py:900
uses_validator: partial            # só NonEmptyOutputGate + NoTracebackGate
uses_sandbox: false
supports_cancel: true
supports_recovery: true
declara_completed: kernel
```

```yaml
execution_path: serve_stream_sse
uses_kernel: true
kernel_custody: admit_only         # thread órfã; ver PLANO §9.3
uses_task_contract: false
uses_context_builder: false
uses_validator: false
uses_sandbox: false
supports_cancel: true              # GeneratorExit -> cancelled
supports_recovery: true
declara_completed: caller
ressalva: nao emite token a token no modo native (regressao conhecida)
```

```yaml
execution_path: scheduler          # + automation_scheduler, dispatcher, swarm,
                                   #   orchestrator, execution_engine,
                                   #   app_factory, daemon, serve_loop
uses_kernel: false
kernel_custody: none
uses_task_contract: false
uses_context_builder: false
uses_validator: false
uses_sandbox: partial              # só o dispatcher usa task_worktree
supports_cancel: unknown
supports_recovery: false
declara_completed: caller
```

---

## 4. Consequência para o plano

**Correção ao PLANO §9.3.** A versão anterior tratava a falta de custódia como
questão do `/stream`, e afirmava que o `bauer run` tinha custódia via
`execute()`, logo o Validator poderia ser entregue ali sem depender da decisão
arquitetural. **Está errado, e a medição mostra.** O `bauer run` usa `admit()`.

Isso muda a ordem de trabalho: o gate de testes (PR 3) **não tem onde rodar**
no caminho que mais importa até a custódia do `bauer run` ser resolvida. §9.3
não é rodapé do SSE — é o bloqueador da frente de validação inteira.

**Achado adicional que o plano não previu.** `bauer run` cria **um** run para a
sessão autônoma toda: 3 rodadas e 7 tool calls sob um único run, com
`policy.evaluated` publicado **uma vez**, na admissão. Governança por rodada não
existe. Se a rodada 2 decidir algo arriscado, o Kernel não reavalia — só os
guardrails do laço de turno (`_detect_loop`, `ToolCallGuardrailController`) e o
gate G4 do `tool_router`, que ficam fora da auditoria do run.

Duas leituras possíveis, e é decisão de projeto:

- **um run por sessão** (hoje): auditoria enxuta, governança só na entrada;
- **um run por rodada**: cada rodada reavaliada e validável, ao custo de N runs
  por tarefa e de decidir o que significa "a tarefa" na auditoria.

Recomendação: **run por sessão com reavaliação por rodada** — manter um run
como unidade de tarefa, e o Kernel expor um hook de reavaliação entre rodadas
(`policy_check` + gates) sem abrir run novo. Preserva a auditoria e fecha o
buraco. Entra no S8.

---

## 5. Eventos observados

No run autônomo real, 5 eventos para 3 rodadas e 7 tool calls:

```
  2  run.state.changed        (planning, policy_check — KERNEL_ONLY_STATES)
  1  run.created
  1  policy.evaluated
  1  run.started
  1  run.completed
```

Ausentes e relevantes: `run.context.built`, `run.progress.warning`,
`run.validation.*`, `run.replanning`. Os `tool.call.*` foram publicados no bus
do `ToolRouter`, não neste — o que confirma o `_wire_router_to_serve` como
único ponto onde os dois bus se encontram, e só no `serve`.
