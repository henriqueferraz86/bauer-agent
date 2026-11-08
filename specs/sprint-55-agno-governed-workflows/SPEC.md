# SPEC — Sprint 55: workflows Agno governados pelo Kernel (plano 057, sprint "23")

Depende da sprint 50 (ponte) e 52 (`bauer.data`). Executada como 55.

## Problema

O `agno.workflow.Workflow` oferece etapas, paralelismo, condições e revisão
humana, mas com semântica própria perigosa para o Bauer, medida no SDK
instalado (3.0.10):

- etapa que falha é re-tentada 3× e depois **pulada**; o run termina
  `COMPLETED` com "Step skipped due to error" — exatamente o
  `run.completed` falso que o plano proíbe;
- `HumanReview` do Agno pausa o workflow e depende do db/sessão do Agno para
  retomar — duplicaria persistência/agenda do Bauer (condição STOP).

## Processo escolhido (real e limitado)

**Relatório de dados** (`data_report_workflow`): perfil do arquivo →
(totais ∥ qualidade) em paralelo → condição "há nulos?" → sinalização →
**revisão humana** → publicação do relatório em `.bauer/artifacts/data/`.
Beneficia-se de etapas, paralelismo, condição e revisão; a única escrita
acontece estritamente depois de uma aprovação vinculada ao run.

## Escopo

1. `GovernedWorkflow` (`bauer/core/runtime/agno_workflows.py`): nós
   declarativos (`StepSpec`, `ParallelSpec`, `ConditionSpec`) convertidos em
   `Workflow` Agno **sem db**, executado dentro de `run_governed` — o Kernel
   admite (policy/budget/kill-switch), avalia (gates) e declara o terminal.
2. Retry/skip do Agno desligados (`max_retries=0`) e livro de falhas Bauer:
   qualquer falha ⇒ etapas seguintes não rodam ⇒ resultado `failed`.
3. Antes de cada etapa: run relido (terminal/cancelado ⇒ para),
   `checkpoint_execution`, prazo do workflow.
4. Revisão humana = `ApprovalManager.request(suspend_run=True)` vinculada ao
   run/sessão/payload: run `waiting_approval` → `queued` (Kernel.approve) →
   `running` (authorize com `resume_run`); negação ⇒ `failed`; cancelamento ⇒
   `cancelled`; espera expirada ⇒ aprovação `expired` e `failed`.
5. Eventos por etapa (`agent.started/finished/failed/paused/resumed/cancelled`,
   `agent_id=workflow:<nome>/<etapa>`) com nome, status e duração — sem
   entradas/saídas das etapas.

## Comparação com o fluxo atual

| | Turno de agente com tools (hoje) | `data_report_workflow` |
|---|---|---|
| Custo de LLM | 1+ chamadas por tool (descrever, agregar, salvar) | **zero** (etapas determinísticas) |
| Paralelismo | sequencial (uma tool por vez) | totais ∥ qualidade |
| Revisão humana | aprovação por tool de escrita (se a policy pedir) | revisão explícita antes da publicação, sempre |
| Custódia | Kernel | Kernel (idêntica: `run_governed`) |
| Falha de etapa | erro da tool volta ao modelo | run `failed`, sem publicação |

Conclusão registrada: Agno workflow vale para processos **determinísticos e
multi-etapa** com revisão; turnos abertos continuam no executor de agente
(plano 062). Nenhum outro fluxo foi migrado.

## Fora de escopo

- Converter o orquestrador (`orchestrator.py`) ou outros fluxos.
- `HumanReview`/db/sessões do Agno; AgentOS.

## Critérios de aceite

- [x] Sucesso, falha, cancelamento, timeout (prazo e revisão), rejeição de
  aprovação e retomada testados com Kernel/RunManager/ApprovalManager reais.
- [x] Todo terminal passa pelo Kernel (gate reprovando ⇒ não `completed`).
- [x] Eventos reconstroem a sequência sem conteúdo das etapas.
- [x] Sem `admit()`, `complete_run`/`fail_run`, db ou AgentOS no módulo.
- [x] Suíte e Ruff verdes.
