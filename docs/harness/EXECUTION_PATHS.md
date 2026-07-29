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

## 1.5. Estado depois do S8 (2026-07-29, mesma sessão)

A tabela da §2 é a **baseline** medida antes da migração. Depois do S8:

| Caminho | Antes | Depois |
|---|---|---|
| `bauer run` / `/loop` CLI | `admit_only` | **`full`** — laço como executor de `execute()` |
| `bauer agent` interativo | `full` | `full` |
| `bauer agent run-one` | **`none`** | **`full`** |
| `bauer kernel run` | `full` | `full` |
| serve `/chat` | `full` | `full` |
| serve `/loop` (web) | `admit_only` | **`full`** — `admit()` p/ o id + `continue_run()` p/ custódia |
| `core/runtime/scheduler` | **`none`** | **`full`, sem flag** — ver §4.1 |
| canais (Telegram/Slack/Discord) | **`none`** | **`full`** |
| `/v1/chat/completions` (batch) | **`none`** | **`full`** — `admit()` + `continue_run()` |
| `/v1/chat/completions` (stream) | **`none`** | **`admit_only`** — o gerador SSE é dono do run |
| serve `/stream` (SSE) | `admit_only` | `admit_only` — §9.3(b), garantido por teste |
| `orchestrate run` | **`none`** | **`admit_only`** — ver §4.4 |
| `bauer benchmark`, `runtime test` | — | **fora do escopo** — diagnóstico (§4.2) |
| `task_dispatcher`, `daemon`, `swarm`, `app_factory` | listados como `none` | **não são pontos de execução** (§4.3) |

### Números finais

| | antes do S8 | depois |
|---|---|---|
| **contato** com o Kernel | 6/14 (43%) | **14/14 (100%)** |
| **custódia** (o Kernel decide `completed`) | 3/14 (21%) | **11/14 (79%)** |

Os três sem custódia são os que **não podem** tê-la, cada um por um motivo
estrutural, não por falta de trabalho: `/stream` e `/v1` streaming (o gerador SSE
é dono do run, e envolvê-lo disputaria a posse com a thread órfã) e
`orchestrate run` (§4.4). Forçar custódia neles produziria falso sucesso — que é
o defeito que o harness existe para impedir.

A garantia contra regressão é `tests/test_arquitetura_custodia_kernel.py`, que
trava **por arquivo** quem pode fechar run por fora, com contagem fixa e
justificativa escrita. Caminho novo não entra em silêncio; e quando uma dívida é
paga, o teste falha pedindo para travar o ganho.

### 4.4 Por que `orchestrate run` fica em `admit_only`

Três modos, e `--background` / `--mode durable` **submetem e retornam**: o
trabalho segue depois, em outro processo, sob o `OrchestrationRun` — que é a
unidade de ciclo de vida real ali. Um run síncrono do Kernel reportaria
`completed` para trabalho que ainda não aconteceu.

O que `admit()` entrega e vale: kill-switch central, policy e budget avaliados
**antes de qualquer LLM**, com Run auditável. O desfecho substantivo fica com o
`OrchestrationRun`.

### 4.1 Por que o scheduler perdeu a flag

Ele já fazia à mão tudo o que o Kernel faz — criava o Run, rodava laço de retry
com backoff, chamava `complete_run`/`fail_run`. Não havia "caminho legado
intocado" a preservar; havia uma **segunda implementação do mesmo ciclo de
vida**. Deixar a flag escolher entre as duas manteria os dois trilhos que o
Kernel existe para eliminar. É o HARNESS-020 aplicado a um caminho só.

### 4.2 O que fica fora do denominador, de propósito

`bauer benchmark` e `bauer runtime test` são **diagnóstico**, não execução
autônoma. Governá-los acoplaria a ferramenta de medição à policy que ela deveria
ajudar a testar — e um smoke test de adapter que a policy pode negar deixa de ser
smoke test. Registrado em `PERMITIDOS` no teste arquitetural.

### 4.3 Correção ao inventário original

Quatro entradas da tabela §2 **não são pontos de execução**:

- `task_dispatcher.py` roda workers como **subprocesso** (`subprocess.Popen`,
  `subprocess.run`) — a governança dele vem do processo filho, que agora é
  governado. O dispatcher não executa turno de LLM.
- `daemon.py` delega ao TaskDispatcher (`daemon.py:442`).
- `swarm.py` e `app_factory.py` não chamam `run_agent` nem `run_one_turn`.
- `serve_loop.py` é biblioteca: o turno é **injetado** (`turn_fn`). A entrada é
  o `_loop_worker` do `server.py`, esse sim migrado.

E `orchestrator.py:487` executa **passos** dentro de uma orquestração que já tem
run próprio (`OrchestrationRun`). Governar cada passo criaria N runs por tarefa,
contra a recomendação de §9.5 do plano (o run é a unidade de TAREFA). O certo é
governar a entrada — `orchestrate run` —, que é a dívida registrada acima.

---

## 2. Tabela dos caminhos (baseline, antes do S8)

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
