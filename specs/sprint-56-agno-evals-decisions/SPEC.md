# SPEC — Sprint 56: evals e memória de decisões (plano 057, sprint "24")

Depende da sprint 50 (eventos de capability). Executada como 56.

## Avaliação dos evals do Agno 3.0.10

| Eval | Uso no Bauer | Motivo |
|---|---|---|
| `ReliabilityEval` (tool calls executadas vs. esperadas, argumentos) | **adotado** | determinístico, sem modelo; roda offline sobre a trilha do EventBus |
| `AccuracyEval` / `AgentAsJudge` | não adotado | exige LLM juiz (custo, não hermético, falsos positivos do juiz) |
| `PerformanceEval` | não adotado | mede latência/memória do processo; o harness já mede custo/tempo por run |

Todo eval do Agno tem `telemetry=True` por padrão (envia dados à API da
Agno) e grava em `db` quando fornecido: no Bauer, `telemetry=False`,
`db=None`, sem spinner.

## Escopo

1. `bauer/core/runtime/agno_evals.py`:
   - `trace_from_events` reconstrói a evidência de tools de um run a partir do
     EventBus (`completed` = execução limpa; `failed`/`denied` = erro). Para
     tools de capability só há nomes de argumentos (invariante 6), logo
     checagem de argumentos vale apenas para tools que já publicam args.
   - `evaluate_tool_use` roda o `ReliabilityEval` real sobre essa trilha.
   - Veredito é consultivo: nunca conclui, falha ou re-tenta run.
2. Harness: cenários `56.1`–`56.3` (uso correto de tool; tool inesperada ou
   com erro reprovada — mede falso sucesso; eval aprovado não substitui gate
   do Kernel — crítico). Scorecard do `PLANO_HARNESS_90.md` regenerado.
3. `CapabilityDecisionLog` sobre o `DecisionMemory` existente (SQLite, sem
   migração): tipo (`agent_selection`, `tool_use`, `delegation`,
   `final_answer`), selecionado, alternativas, resultado, evidência curta,
   feedback, **versão do schema, escopo, expiração**; listar por escopo,
   apagar por id, expurgar expirados e escopo inteiro.
4. Privacidade: sem chain-of-thought — chaves com cara de raciocínio privado
   são recusadas; textos ≤300 caracteres, redigidos (scanner de segredos +
   `redact_text`).
5. Gravação automática **opt-in**: `bauer.decisions.auto_record` em
   `agno_capabilities.yaml` (default desligado; escopo e TTL configuráveis).

## Fora de escopo

- Database Agno / migração de memória (exigiria ADR — plano 057).
- Evals com LLM juiz na suíte.

## Comparação com o harness atual

O harness mede **governança** (o Kernel barra/reprova/cancela certo); o
`ReliabilityEval` mede **uso de ferramenta** de um run. São complementares:
os três cenários novos entram no harness sem alterar os existentes, a taxa de
falso sucesso continua ≤2% e nenhum gate obrigatório foi substituído.

## Critérios de aceite

- [x] Testes medem uso correto/incorreto de tools sobre trilha governada real.
- [x] Nenhum raciocínio privado ou segredo é persistido.
- [x] Memória inspecionável, apagável e isolada por escopo; expiração.
- [x] Eval offline (sem rede/db) e consultivo.
- [x] Harness sem regressão; scorecard regenerado; suíte e Ruff verdes.
