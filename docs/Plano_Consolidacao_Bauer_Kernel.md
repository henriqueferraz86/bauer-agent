# Plano de Consolidação do Bauer Kernel (v2 — revisado sobre o código real)

> **Status:** revisado em 2026-07-11 após auditoria do código.
> **Mudança de premissa:** o plano original foi escrito como se o Kernel fosse
> construído do zero. **Não é.** ~80% dos componentes já existem e funcionam.
> O trabalho real é de **consolidação** (compor o que existe atrás de um
> coordenador único), **não de construção**. Este documento corrige o rumo para
> evitar o maior risco do plano original: **duplicar** `run_manager`,
> `scheduler`, `policy` e `adapters` num pacote `bauer/kernel` novo.

---

## 1. Diagnóstico — o plano original está certo no "porquê", errado no "de onde partir"

O objetivo ("consolidar num Kernel de orquestração que coordene todo o ciclo de
vida, sem reescrever os módulos atuais") está **correto**. O problema real do
Bauer hoje **não** é a ausência de peças — é a **fragmentação**: as peças
existem, mas rodam em trilhos paralelos que nunca se encontram.

### Evidência da fragmentação (o gap de verdade)

| Caminho de execução | Cria `Run`? | Passa pelo adapter? | Passa pela Policy? |
|---|---|---|---|
| `bauer serve` (chat/stream/v1) | ✅ `RunManager.create_run` (server.py:406,1179,1275,1620) | ❌ executa direto em `run_one_turn_with_fallback` | ⚠️ parcial (policy no tool_router) |
| `bauer agent` (interativo, `agent.py`) | ❌ **nenhum Run** | ❌ | ⚠️ parcial |
| `bauer agent` (one-shot, `agent_cmd.py:1005`) | via adapter | ✅ `adapter.run_agent` | ❌ |
| `bauer runtime run` | ✅ | ✅ `adapter.run_agent` | ❌ |
| `scheduler.run_task` | ✅ | ✅ `adapter.run_agent` (scheduler.py:197) | ❌ |

Ou seja: **cinco caminhos, cinco combinações diferentes** de "cria run / usa
adapter / aplica policy". O serve rastreia runs mas ignora o contrato de
executor; os adapters executam mas o agente interativo nem cria run; a Policy é
aplicada em um ponto (tool_router) e não no ciclo de vida. **É isto que o Kernel
precisa unificar** — um único `execute()` pelo qual todos os caminhos passam.

---

## 2. Inventário real — o que já existe (NÃO reimplementar)

| Componente do plano | Já existe? | Onde | Ação |
|---|---|---|---|
| **Event Bus** | ✅ completo | `bauer/core/events/bus.py` + `schema.py` | reusar |
| **Policy Engine** | ✅ completo | `bauer/core/policy/engine.py` (`evaluate → allow/ask/deny`), `approvals.py`, `risk.py` | reusar |
| **State Manager** | ✅ (Run + persistência) | `core/runtime/run_manager.py` (`Run`, estados), `state_store.py` (`JsonlStateStore`) | **estender** estados |
| **Scheduler** | ✅ completo | `core/runtime/scheduler.py` (tick/worker/cron/pause/resume) | reusar |
| **Runtime Registry** | ✅ completo | `core/runtime/adapters/factory.py` (`register`/`get`), `agent_registry.py`, `team_registry.py` | reusar |
| **Adaptadores** | ✅ 2 prontos | `adapters/bauer_native.py`, `adapters/agno_adapter.py`, contrato em `base.py` | **reconciliar** contrato |
| **Memory Manager** | ✅ | `memory_manager.py`, `core/runtime/memory.py` | reusar |
| **Planner** | ✅ | `autonomous_planner.py`, `orchestrator.py` | reusar |
| **Resiliência** (retry/kill-switch/recovery) | ✅ | `core/runtime/resilience.py` (`WorkerRegistry`, `RuntimeControl`, `RuntimeRecovery`), `retry_utils.py`, `circuit_breaker.py` | reusar |
| **Evaluator / Quality Gates** | ❌ **GAP REAL** | não existe (há `benchmark.py`, `self_tuner.py`, `learning_engine.py`, `core/audit` — mas todos *post-hoc*, não in-loop) | **construir** |
| **Coordenador único (Kernel)** | ❌ **GAP REAL** | não existe | **construir (fachada fina)** |

**Conclusão:** só **duas** coisas são genuinamente novas — o **coordenador
(fachada)** e o **Evaluator in-loop**. Todo o resto é composição.

---

## 3. Correções de rumo ao plano original

### 3.1 Kernel = fachada que compõe, não pacote que reimplementa
O plano dizia "Criar pacote `bauer/kernel`". **Risco:** virar um segundo
`core/runtime` e duplicar tudo. **Correção:** o `BauerKernel` nasce como uma
**fachada fina** que recebe (injeta) as instâncias já existentes:

```python
# bauer/core/kernel/kernel.py  (~150 linhas, zero lógica duplicada)
class BauerKernel:
    def __init__(self, *, runs, policy, scheduler, registry, bus, evaluator=None):
        self.runs = runs            # RunManager EXISTENTE
        self.policy = policy        # PolicyEngine EXISTENTE
        self.scheduler = scheduler  # Scheduler EXISTENTE
        self.registry = registry    # adapters.factory EXISTENTE
        self.bus = bus              # EventBus EXISTENTE
        self.evaluator = evaluator  # ← única peça nova

    def execute(self, request: KernelRequest) -> KernelRun:
        # orquestra a máquina de estados chamando os componentes existentes
        ...
```
Fica em `bauer/core/kernel/` (ao lado de `core/runtime`, `core/policy`), não em
`bauer/kernel` na raiz — mantém a coesão de `core/`.

### 3.2 Máquina de estados: ESTENDER os `RunStatus`, não substituir
Hoje: `queued · running · waiting_approval · completed · failed · cancelled`
(run_manager.py:14 — repare que **`waiting_approval` já existe**, o plano nem
sabia). O plano propõe `CREATED→PLANNING→POLICY_CHECK→QUEUED→RUNNING→EVALUATING→
COMPLETED` + `RETRYING/PAUSED`. **Correção — adicionar aditivamente**, sem quebrar
os terminais nem os eventos já publicados:

```
Novos estados intermediários (opcionais, só quando o Kernel os usa):
  planning · policy_check · evaluating · retrying · paused
Estados terminais permanecem: completed · failed · cancelled
Retrocompat: todo caminho legado continua indo queued→running→completed.
```
`waiting_approval` (já existente) cobre o "PAUSED aguardando aprovação humana" do
plano — reusar, não criar um `PAUSED` concorrente.

### 3.3 Contrato do executor: reconciliar com o que os adapters já implementam
O plano pede `execute/pause/resume/cancel/healthcheck`. O contrato real
(`adapters/base.py`) é `create_agent/run_agent/stream_agent/stop_run/get_run/
list_sessions` — **já implementado por 2 adapters**. Reescrever o contrato
quebraria ambos. **Correção:** manter o contrato atual e **adicionar métodos
opcionais** ao `Protocol` (`healthcheck()`, `pause_run()`, `resume_run()`) com
*default* no-op, para os adapters evoluírem sem big-bang. `cancel` já existe como
`stop_run`; `execute` já existe como `run_agent`.

### 3.4 Persistência: JSONL já funciona — SQLite é migração opcional, não pré-requisito
O plano diz "Persistência inicial em SQLite". Mas `JsonlStateStore` já persiste
runs/sessions/eventos e é lido por audit/perf/desktop. **Correção:** **não
reescrever a persistência para "bater com o doc".** SQLite entra só se/quando
houver dor real de concorrência/consulta (há `sqlite_session_store.py` e
`kanban_db.py` como precedente) — como um `StateStore` alternativo atrás da mesma
interface, num sprint próprio, não no Sprint 1.

### 3.5 O caminho crítico (que o plano subestimou) é a migração do serve/agent
O plano põe "Migrar CLI/API" só no Sprint 6, como se fosse mecânico. **É o item
mais valioso e mais arriscado.** Fazer `server.py` e `agent.py` chamarem
`kernel.execute()` (em vez de `run_one_turn_with_fallback` direto) é o que
efetivamente unifica os cinco trilhos. Deve começar cedo, atrás de **feature
flag**, com o motor antigo intacto como fallback.

---

## 4. Roadmap revisado

Princípios: **cada sprint entrega valor sozinho**, **nada quebra a suíte
(≈4645 testes)**, **Kernel é opt-in por flag até provar paridade**.

### Sprint 1 — Fachada + máquina de estados (fundação real)
- `bauer/core/kernel/` com `BauerKernel`, `KernelRequest`, `KernelRun`.
- `BauerKernel.execute()` compõe `RunManager` + `PolicyEngine` + `EventBus`
  **já existentes** (injeção de dependência, zero reimplementação).
- Estender `RUN_STATUSES` aditivamente (§3.2); publicar eventos de transição.
- **Sem SQLite** — usar `JsonlStateStore`.
- **Entregável:** `kernel.execute(request)` roda um turno *bauer_native* de
  ponta a ponta com estados persistidos, atrás da flag `kernel.enabled=false`.

### Sprint 2 — Unificar a execução pelo contrato de adapter
- `BauerKernel.execute()` roteia via `get_runtime_adapter().run_agent()` em vez
  de chamar o motor direto — os 5 trilhos passam a ter **um** ponto de execução.
- Adicionar `healthcheck/pause_run/resume_run` opcionais ao `Protocol` (§3.3).
- **Entregável:** `bauer runtime run` e o scheduler já rodam via Kernel; paridade
  de saída verificada contra o caminho atual.

### Sprint 3 — Governança no ciclo de vida (não só no tool_router)
- Estado `policy_check` chama `PolicyEngine.evaluate` **antes** de executar.
- `ask` → `waiting_approval` (reusa fluxo existente); `deny` → `failed(policy)`.
- Kill-switch central via `RuntimeControl` (já existe) consultado pelo Kernel.
- Limites de custo: Kernel lê `autonomous_budget.py`/`iteration_budget.py` e
  aborta o run ao estourar o teto.
- **Entregável:** toda execução via Kernel é governada e auditável ponta a ponta.

### Sprint 4 — Resiliência in-loop
- Retry com backoff (`retry_utils.py`) + `circuit_breaker.py` no laço do Kernel;
  estado `retrying`.
- Recuperação pós-restart via `RuntimeRecovery.recover_stuck_runs` (já existe).
- Fallback de modelo (já há em `chat_with_tools`) e de **executor** (adapter
  alternativo) coordenados pelo Kernel.
- **Entregável:** run interrompido por crash/restart é recuperado e retomado.

### Sprint 5 — Evaluator (a única peça nova de verdade)
- `bauer/core/kernel/evaluator.py`: estado `evaluating` antes de `completed`.
- Quality Gates plugáveis (ex.: "saiu sem erro?", "testes passaram?", "output
  casa com o objetivo?"), reusando sinais de `core/audit` e `learning_engine`.
- Replan: gate reprovado → volta a `planning` (loop limitado por budget).
- **Entregável:** execuções só concluem após avaliação; gates configuráveis.

### Sprint 6 — Migração dos front-ends (crítico, com flag)
- `server.py` (chat/stream/v1) e `agent.py` interativo chamam `kernel.execute()`
  atrás de `kernel.enabled`; motor antigo permanece como fallback.
- Migrar App Factory / Dashboard para lerem o estado do Kernel.
- Rollout: flag off → smoke em paralelo → paridade → flag on por padrão.
- **Entregável:** um único caminho de execução; `run_one_turn_with_fallback` vira
  detalhe interno do adapter `bauer_native`, não uma segunda porta de entrada.

---

## 5. Máquina de estados (revisada, retrocompatível)

```
                 ┌─────────────────────────────────────────────┐
CREATED → PLANNING → POLICY_CHECK → QUEUED → RUNNING → EVALUATING → COMPLETED
                 └───────────────┘        │      │  │
   deny → FAILED(policy)   ask → WAITING_APPROVAL │  │
                                    (reusa estado existente)    │
   RUNNING → RETRYING → QUEUED   RUNNING → FAILED   RUNNING → PAUSED → QUEUED
   EVALUATING → PLANNING (replan, limitado por budget)

Legado (sem flag): QUEUED → RUNNING → COMPLETED  (intocado)
Terminais: COMPLETED · FAILED · CANCELLED
```

## 6. Contrato do executor (revisado — aditivo)

Mantido: `create_agent · run_agent · stream_agent · stop_run · get_run ·
list_sessions`.
Adicionados como **opcionais** (default no-op): `healthcheck · pause_run ·
resume_run`.

## 7. Critérios de conclusão

- Toda execução (serve, agent, runtime, scheduler) passa por `kernel.execute()`.
- Estados persistidos e **retrocompatíveis** (nenhum evento/consumidor legado
  quebra).
- Governança (policy + budget + kill-switch) aplicada **no ciclo de vida**.
- Recuperação após falha/restart.
- Avaliação (quality gate) antes de concluir.
- **Zero reimplementação** de run_manager/scheduler/policy/events/adapters.
- Suíte (~4645 testes) verde; Kernel opt-in por flag até paridade comprovada.

## 8. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| Duplicar `core/runtime` num `bauer/kernel` paralelo | Kernel é **fachada por injeção**; proibido reimplementar (revisar em PR) |
| Big-bang quebrar os 5 trilhos de execução | Migração atrás de `kernel.enabled`, motor antigo como fallback |
| Reescrever persistência p/ SQLite sem necessidade | JSONL permanece; SQLite só em sprint próprio, sob a mesma interface |
| Quebrar o contrato dos 2 adapters existentes | Contrato só cresce (métodos opcionais), nunca muda assinatura |
| Regressão silenciosa na suíte | Cada sprint fecha com CI verde; smoke em paralelo antes de ligar a flag |

## 9. Resultado esperado

O Bauer passa a ser um **Kernel Adaptativo de Orquestração** — mas por
**consolidação do que já existe**, não por reconstrução. Um único `execute()`
governado, resiliente e avaliado, com Bauer Native e Agno como adapters, e os
frameworks de agentes desacoplados atrás do Runtime Registry.
