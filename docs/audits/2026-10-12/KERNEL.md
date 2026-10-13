# Auditoria Kernel, runtime, budgets e providers — 2026-10-12

Reproduções preservadas: [repro/README.md](repro/README.md). Evidências consolidadas: [REPORT.md](REPORT.md). Os caminhos `/tmp` abaixo identificam a execução original.

Somente leitura no repositório. Li `AGENTS.md`, `tests/test_arquitetura_custodia_kernel.py`, `evals/harness/medir.py` e testes vizinhos antes dos achados. As reproduções abaixo rodaram via `uv run` depois de o root concluir `uv sync --frozen --extra dev`. Nenhum provider ou segredo real foi usado; cada runtime usou um diretório temporário e `BAUER_HOME/BAUER_CONFIG/BAUER_AGENTS_FILE` isolados. Nenhuma mudança de implementação.

Script reproduzível: `/tmp/bauer-audit-kernel-repro.py`; saída: `/tmp/bauer-audit-kernel-repro.jsonl`. São 12 cenários que demonstram 10 achados. P1 = impacto alto; P2 = impacto médio. Os valores de preços usados comparam o próprio algoritmo/tabela do repositório, sem afirmar preços atuais de fornecedor.

## K01 — P1 — `max_parallel_runs=1` bloqueia a primeira execução

- Local: `bauer/core/runtime/autonomy.py:136` e `:150`; interação com `bauer/core/kernel/kernel.py:142` e `:229`.
- Trigger: perfil com limite paralelo 1; `kernel.execute()` sem nenhum outro run ativo.
- Causa: `_open_run` persiste o candidato antes do policy check. `ensure_can_start(run_id=...)` conta esse mesmo candidato como ativo e exige `active < limit`. Comentário em autonomy:115 diz que o run ainda não existe, porém isso deixou de ser verdadeiro.
- Evidência: `parallel_self_count`: status failed, executor_calls 0, `policy deny: max parallel runs reached: 1/1`.
- Impacto: limite 1 inutiliza execução governada; limite N permite apenas N-1 execuções úteis e a contagem continua sendo um read/check separado da admissão transacional.
- Correção: excluir o próprio `run_id`; reservar vagas de execução atomicamente, considerando pré-admissões concorrentes.
- Teste faltante: executar Kernel+PolicyEngine+BudgetManager real com limites 1 e 2. Testes atuais em `tests/test_runtime_scheduler.py:192` testam o manager isolado antes de criar candidato.

## K02 — P1 — sucesso posterior ressuscita um run cancelado

- Local: `bauer/core/kernel/kernel.py:741`; `bauer/core/runtime/run_manager.py:152` e `:165`. Guardas em kernel.py:847 e :866 também são read/check/write, sem CAS.
- Trigger: cancelamento registrado enquanto executor está rodando, seguido de retorno de sucesso; caso típico de stop em execução que o adapter não consegue interromper imediatamente.
- Evidência: `success_overwrites_cancel`: executor chama `runs.cancel_run(payload['run_id'])` e devolve `{'output':'ok'}`. Kernel retorna completed; audit log contém `run.cancelled` seguido de `run.completed`. Esta reprodução elimina a dependência de escalonamento e demonstra a falha determinística com evaluator desligado. Com gates ligados, cancelamento pode resultar em `KernelStateError` durante transição para evaluating ou corrida durante conclusão do gate.
- Impacto: comando de parar perde efeito sobre o estado final, consumidores veem eventos terminais contraditórios, falso sucesso. `cancel_run` também faz a guarda terminal fora da transação.
- Correção: todos os terminais devem usar a primitiva CAS de `SqliteStateStore.upsert_unless_status`, já usada por `fail_run_se_nao_terminal`; transitions intermediárias precisam preservar terminais e conferir status atomicamente.
- Testes faltantes: cancelamento durante executor/gate; complete versus cancel em dois managers/processos; retry após cancelamento. Ratchet arquitetural conta call sites, sem garantir exclusividade de terminal.

## K03 — P1 — gasto real de failed/cancelled desaparece do budget

- Local: `bauer/core/kernel/kernel.py:613`, `:622`, `:712`, `:713`, `:748` e `:960`; `bauer/core/runtime/budget_ledger.py:174`.
- Trigger: executor consome $0.40 e retorna failed/cancelled, ou resultado com gasto é reprovado por gate.
- Evidência: `spent_cost_failed` e `spent_cost_cancelled`: run.cost_estimate=0.4, porém BudgetManager.status.daily.used_usd=0.0. Reserva de $0.10 é liberada. `_record_cost` só é chamado depois de completed.
- Impacto: falhas repetidas gastam provider sem reduzir daily/weekly/monthly/agent budgets; orçamento pode permitir gasto sem teto efetivo. Persistir métrica no Run não atualiza o ledger que decide admissão.
- Correção: separar liquidação de custo do desfecho; liquidar gasto conhecido também no cancelamento, falha, reprovação de gate e retry, liberando apenas parcela da reserva não gasta.
- Teste faltante: assertions no ledger para falha, cancelamento e reprovação de gate; testes `tests/test_kernel.py:231` apenas conferem campo do Run.

## K04 — P1 — retries/replans contabilizam somente a última tentativa

- Local: `bauer/core/kernel/kernel.py:613`, `:807` e `:740`.
- Trigger: duas tentativas que retornam métricas incrementais próprias, por exemplo um retry após falha de $0.40 e sucesso de $0.40.
- Evidência: `retry_cost_overwrite`: attempts=2, custo esperado=.8, Run e ledger=.4; 4 tools esperadas, persistidas 2. Contagem LLM também é sobrescrita.
- Impacto: orçamento e histórico subestimam retries/fallbacks/replans justamente durante degradação de provider. Replan pode repetir orçamento completo.
- Correção: contrato explícito de métricas por tentativa; acumular deltas por attempt_id ou agregar separadamente e liquidar total sem dupla contagem.
- Teste faltante: retry/fallback/replan com métricas diferentes por tentativa, inclusive falha parcial e consumo de gate.

## K05 — P1 — custo Anthropic bruto é zero no `cost_sink`

- Local: `bauer/cost_meter.py:68`; `bauer/usage_pricing.py:171`; fontes `bauer/agent_response.py:112`, `bauer/agent_native_execution.py:28`, `bauer/anthropic_client.py:69` e `:188`.
- Trigger: AnthropicClient retorna native usage `input_tokens/output_tokens`, sem campos cache.
- Causa: produtores passam client.last_usage bruto ao report_llm_cost, enquanto estimate_cost_usd lê apenas prompt_tokens/completion_tokens. `account_usage.normalize_usage` existe, porém não é chamado nessa ponte.
- Evidência: `anthropic_raw_cost_zero`: input_tokens=1000/output_tokens=500 para modelo da tabela retorna sink_cost=0.0; mesmos dados normalizados retornam .0105.
- Impacto: sink autônomo/daemon/serve recebe chamadas cloud aparentemente gratuitas; `--max-cost` pode deixar de parar inferência Anthropic. Com cache, calcula apenas parcelas cache e omite tokens nativos regulares/output.
- Correção: normalizar a entrada na ponte sem descartar o campo `cost` reportado; esclarecer semântica dos caches Anthropic (native input_tokens não inclui as parcelas cache) ao calcular total.
- Teste faltante: integração AnthropicClient.last_usage → report_native_cost/report_llm_cost → sink e budget; testes de normalização e pricing isolados não cobrem conexão.

## K06 — P1 — ScopeGate aprova arquivo expressamente proibido dentro de diretório novo

- Local: `bauer/core/kernel/gates/scope.py:31` e `:80`.
- Trigger: contrato `allowed: ['src/']`, `forbidden: ['src/private.py']`; agente cria src/private.py num diretório src ainda não versionado.
- Evidência: `scope_untracked_dir_hides_forbidden_file`: git_paths=['src/']; contrato avaliado diretamente no arquivo acusa forbidden, mas gate passed=true e reason='1 arquivo(s), todos no escopo'.
- Causa: `git status --porcelain` agrupa arquivos não versionados no diretório; gate compara apenas caminho do diretório.
- Impacto: violações de contrato passam pelos gates e são concluídas como sucesso. Caso inverso também pode causar falso bloqueio quando allowed contém arquivo específico mas git retorna diretório.
- Correção: listar untracked individualmente (`--untracked-files=all`) e usar formato `-z`/parser adequado para caminhos com caracteres especiais e rename.
- Teste faltante: repo git real com diretório untracked e forbidden descendente; mocks de alterados_fn não reproduzem agrupamento do Git.

## K07 — P1 — SecretsGate ignora segredo em repositório Git sem primeiro commit

- Local: `bauer/core/kernel/gates/secrets.py:41`, `:50` e `:110`.
- Trigger: projeto recém-criado com `git init`, sem HEAD; arquivo novo contém chave sintética detectável.
- Evidência: `secrets_empty_git`: gate passed=true com reason='workspace não é repo git — sem diff a escanear', embora git init tenha sido executado e o scanner direto detecte 3 matches no texto sintético.
- Causa: git diff HEAD falha por ausência de HEAD; retorno None impede até `_untracked`. Falha de infraestrutura é descrita como ausência de repo e passa, apesar de `SecretsGate.critical=True`.
- Impacto: projeto novo pode concluir com segredo introduzido e até fazer primeiro commit sem gate acusar.
- Correção: distinguir ausência de repo, ausência de HEAD e erro real; comparar index/worktree com árvore vazia para HEAD inexistente e sempre escanear untracked. Erro de scanner/diff deve ser explícito no verdict crítico.
- Teste faltante: repo iniciado sem commit e segredo em arquivo tracked/staged/untracked; timeout e falha de git.

## K08 — P1 — continuação e replan rodam com kill-switch ativo; `policy_check` do replan é decorativo

- Local: `bauer/core/kernel/kernel.py:448` e `:734`.
- Trigger A: admit queued; ativar kill-switch antes de thread assíncrona chamar continue_governed/continue_run.
- Evidência A: `continue_ignores_kill_switch`: switch=true, executor_calls=1, final completed.
- Trigger B: gate rejeita primeira saída e ativa kill-switch; próximo replan começa.
- Evidência B: `replan_ignores_kill_switch`: switch=true, executor_calls=2, completed. Replan emite transição policy_check sem chamar `_evaluate_policy`.
- Impacto: `/loop` e `/v1` podem começar após operador desligar runtime; retry/replan podem renovar trabalho sem revalidar kill-switch/policy/budget. Configuração/rules alteradas entre admissão e execução não são conferidas novamente.
- Correção: conferir controle e políticas mutáveis imediatamente antes de cada tentativa/continuação; reusar preflight apropriado sem duplicar aprovação já satisfeita. Persistir operation/metadata necessários para reconstruir request continuado.
- Testes faltantes: queued sob kill-switch posterior, atualização de policy/budget entre tentativas e replan. Existentes exercitam kill-switch somente antes de execute/admit.

## K09 — P2 — limite paralelo desconsidera heartbeat fresco

- Local: `bauer/core/runtime/autonomy.py:139`; contraste `bauer/core/runtime/resilience.py:226`.
- Trigger: run running com updated_at há >900s, mas RunHeartbeat atual, durante turno ou gate longo.
- Evidência: `fresh_heartbeat_ignored`: running vivo, limite paralelo 1, ensure_can_start aceita outro candidato. Recovery corretamente usa heartbeat; BudgetManager não consulta coleção run_heartbeats.
- Impacto: execução longa deixa de contar contra cap e novas tarefas podem ultrapassar limite de concorrência/RAM. Controles divergem sobre o que é um run vivo.
- Correção: centralizar cálculo de liveness para recovery e admissão; considerar heartbeat e waits intencionais na política de vagas.
- Teste faltante: cap paralelo com run antigo+heartbeat fresco versus realmente morto.

## K10 — P2 — duração dos gates é sobrescrita pelo elapsed do executor

- Local: `bauer/core/kernel/kernel.py:739` e `:746`; `bauer/core/runtime/run_manager.py:196`.
- Trigger: executor devolve elapsed_seconds e gate acrescenta tempo após executor.
- Evidência: `elapsed_gate_time_overwritten`: gate espera pelo menos .04s, porém Run final persiste .001s. `_record_elapsed_since` calcula total corretamente; complete_run o sobrescreve com result.elapsed_seconds.
- Impacto: auditoria/performance/budgets baseados na duração mostram valores menores e ignoram gates. Teste `test_terminal_elapsed_includes_quality_gate_time` usa executor sem elapsed e por isso passa.
- Correção: persistir max(total_wall_time, reported_elapsed) na terminal, separar duração executor/gates/total quando necessário.
- Teste faltante: executor com elapsed explícito + gate lento em completed e streaming.

## Designs documentados e limites desta análise

- `admit()` sem evaluator é dívida explicitamente aceita e guardada por ratchet; não reportei a própria existência como bug.
- MemoryManager Markdown e RuntimeMemoryManager têm públicos diferentes e não sincronizam por design, conforme AGENTS; não reportei duplicidade como defeito.
- SqliteStateStore é hoje a fonte operacional e JsonlStateStore é fachada compatível. Docs em AGENTS e medidor ainda contêm descrição anterior de JSONL; isso é dívida documental, não prova de bug de persistência.
- `cap_kernel_coverage` do harness possui lista estática de caminhos, e cobertura de gates conta montagem, não execução destes cenários. Score alto não contraria os achados reproduzidos.
- Não executei suíte completa nesta frente (root a executa), nem fiz chamadas reais a providers. Os cenários são evidências locais diretas de comportamento da implementação, não E2E browser.

## Ordem de remediação sugerida

1. Terminal CAS e interrupção/preflight por tentativa (K02/K08).
2. Contabilidade independente do desfecho + agregação por tentativa + formatos de provider (K03/K04/K05).
3. Gates Git reais para untracked/HEAD/caminhos especiais (K06/K07).
4. Admissão de concorrência sem self-count, atômica, baseada em liveness (K01/K09).
5. Duração total de execução e gates (K10).

Cada grupo deve ganhar testes de integração herméticos com componentes reais e diretórios tmp_path. Validar sprint com ambiente uv lock e gates definidos no AGENTS antes de avançar.
