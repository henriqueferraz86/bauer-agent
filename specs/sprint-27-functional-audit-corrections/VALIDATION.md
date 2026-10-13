# Validação final — Sprint 27

Status: **CONCLUÍDO no escopo funcional autorizado, com revisão independente
final em 2026-10-13**. Os 17 itens funcionais
documentados foram corrigidos e verificados. Segurança permanece excluída;
não se declara que todos os itens do plano misto CORRECTIONS foram encerrados.

## Problemas e comportamento corrigido

| IDs | Resultado |
| --- | --- |
| K01/K09 | Primeiro candidato não conta contra si; vagas de runs persistidos são reservadas em transação. Heartbeat e waits participam da mesma regra de liveness de admissão/recovery. |
| K02 | Primeiro terminal é preservado em CAS entre managers, executor, gates, retry e stream; eventos terminais não se duplicam. Cancelamento antes do lock de sessão não inicia o turno. Timestamp terminal explicitamente fornecido é preservado. |
| K03/K04/K10 | Consumo de falhas/cancelamentos/tentativas/gates é agregado e liquidado sem diminuir com snapshot antigo. CLI, scheduler e HTTP preservam deltas, tools executadas e consumo parcial em erros, callbacks e Ctrl+C; duas tentativas de 0,40 somam 0,80. Tempo total inclui gates. |
| K05 | Usage Anthropic bruto/canônico converge; cache é contado uma vez, custo cloud reportado é preservado e provider local custa zero. Uso nativo chega ao budget. |
| K06 | ScopeGate enumera descendentes untracked individualmente e interpreta porcelain NUL, renames, espaços, CR e bytes não UTF-8. Contrato preserva caminhos literais com whitespace. |
| T07 (cache) | kanban_write não é replayado, invalida leituras e sinaliza mudança ao TestsGate fora de Git. |
| T09/T10 | Schemas seguem catálogo efetivo/overrides/allowlist, com arrays/items e required somente incondicional; handlers continuam responsáveis por condições de ação. |
| WEB-04/05 | Load→execute→save serializado por sessão nos cinco transportes, sem bloquear sessões distintas; gravação atômica. Transcript completo não duplica histórico; incremental é explícito. |
| WEB-06/07 | Batch/SSE `/v1` usam o mesmo motor governado, native/bridge/tools/fallback/custo. Erros do provider chegam ao fallback, sem reaproveitar usage antigo; bridge contabiliza cada retry e falha parcial uma vez. Falhas ao salvar sessões fecham runs preservando consumo. Instruções e modelo ativo são aplicados; max_tokens/temperature/model divergente retornam 422 antes do run. |
| WEB-09/UI-01 | Turno e persistência sobrevivem à navegação; falhas auxiliares e quota de localStorage não derrubam React nem prendem busy. Switch publica modelo/provider reais e status atrasado não apaga seleção nova. |
| Qualidade | Ruff 0.17.0 fixado no roteiro e CI; documentação de Kernel, SQLite e matriz CI atualizada. Spec visível no Git por exceção estreita no gitignore. |

## Testes executados

Todos os comandos foram locais e offline, sem instalar dependências pela rede.

| Verificação | Resultado |
| --- | --- |
| `UV_OFFLINE=1 uv sync --frozen --extra dev` | 79 pacotes conferidos; pyproject/uv.lock preservados |
| Paridade do ambiente e regressões dirigidas | Aprovadas; inclui contratos de tools, Git real temporário, threads/processos, native→budget e caminhos HTTP/CLI/scheduler |
| `UV_OFFLINE=1 PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run --frozen pytest tests/ -q --tb=short -o addopts='-n auto'` | **7.982 passed, 16 skipped, 323 warnings**, 112,60 s; [log](validation/pytest.log) |
| `UV_OFFLINE=1 uv run --frozen --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82` | Aprovado |
| `UV_OFFLINE=1 uv run --frozen --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303` | Aprovado |
| `UV_OFFLINE=1 uv run --frozen mypy bauer/` | Sem erros, 377 arquivos; respeita overrides existentes |
| `npm test -- --reporter=dot` em desktop | **29 testes**, 7 arquivos aprovados |
| `npm run build` em desktop | TypeScript e Vite aprovados; assets distribuídos reconstruídos |
| [validate_browser.py](validate_browser.py) com Playwright 1.63.0/Chromium instalado | Chromium **156.0.8078.4**; navegação durante SSE, session/texto, reload, busy, ambos HUDs e quota/reset de storage aprovados; zero chamadas externas/erros JS; [resultado](validation/browser.json), [captura](validation/browser.png) |
| `git diff --check` | Aprovado |

O smoke usa a SPA compilada e uma API controlada em loopback. Nenhum provider
real, login, credencial, serviço externo ou jornada ofensiva foi usado. A suíte
completa conserva testes herméticos existentes. As 323 warnings são ocorrências
da depreciação existente de `on_event` em desktop_api; o total aumentou com a
cobertura adicional. O baseline anterior tinha 7.706 passed/16 skipped.

A primeira validação global encontrou nove falhas: RunManager sobrescrevia
finished_at explícito, dois testes exigiam o comportamento terminal antigo,
fixtures Anthropic/custo local refletiam semântica anterior e um except/pass
novo excedia a catraca de qualidade. O timestamp e o logging foram corrigidos;
os testes agora verificam os contratos documentados, incluindo timeout real do
observador e consumo Anthropic com/sem input não cacheado. Após 154 testes
dirigidos aprovados, a suíte completa da primeira rodada foi repetida e aprovada.

Na retomada, o primeiro global teve 7.973 passed/16 skipped e uma falha na
fixture `test_stream_records_cost_tokens_and_budget`: publicava `last_usage`
antes da chamada, simulando usage stale. A fixture agora publica durante o
stream e mantém as expectativas de 150 tokens/0,0123, com 9999 tokens antigos
para garantir descarte. Revisão independente aprovou a fixture; 121 testes
HTTP/partial tools/bridge passaram após o ajuste. Após concluir os ajustes best-effort de usage nativo e obter aprovação
independente, a suíte global final passou: 7.982 testes, 16 skips, sem falhas.

## Arquivos alterados ou criados nesta manutenção

Código Python:

- `bauer/core/kernel/kernel.py`, `bauer/core/kernel/entry.py`, `bauer/core/kernel/gates/scope.py`.
- `bauer/core/runtime/autonomy.py`, `budget_ledger.py`, `resilience.py`,
  `run_manager.py`, `scheduler.py`, `state_store.py` nesse diretório.
- `bauer/core/task/contract.py`, `bauer/commands/run_cmd.py`.
- `bauer/account_usage.py`, `cost_meter.py`, `usage_pricing.py`, `agent_native_turn.py`, `agent_native_execution.py`, `agent.py`, `agent_response.py`, `serve_loop.py`, `openai_client.py`.
- `bauer/tool_router.py`, `tool_dedup.py`, novo `tool_schema.py`.
- `bauer/server.py`, `server_chat.py`, `server_openai.py`, `session_store.py`.

Frontend e distribuição:

- `desktop/src/components/TitleBar.tsx`, `desktop/src/screens/Chat.tsx`, `Models.tsx`.
- Novos `desktop/src/chatStream.ts`, `chatStream.test.ts`, `modelSelection.ts`, `modelSelection.test.ts`.
- `bauer/static/index.html`; substituição de `assets/index-Ctd8uXpT.js`
  por `assets/index-CrzH8eOz.js` nesse diretório.

Testes Python novos:

- `tests/test_kernel_parallel_runs.py`, `test_kernel_runtime_audit_fixes.py`.
- `tests/test_runtime_accounting_producers.py`, `test_server_accounting_producers.py`.
- `tests/test_turn_partial_accounting.py`, `test_agent_response_accounting.py`.
- `tests/test_cost_meter_audit.py`, `test_scope_gate_git_paths.py`.
- `tests/test_tool_catalog_contracts.py`, `test_kanban_dedup_contract.py`.
- `tests/test_server_session_turns.py`, `test_server_openai_turns.py`.

Testes Python atualizados:

- `tests/test_account_usage.py`, `test_arquitetura_custodia_kernel.py`, `test_tool_dedup.py`.
- `tests/test_custo_local_e_zero.py`, `test_performance_tracker.py`, `test_stream_timeout_nao_sobrescreve.py`, `test_server_extended.py`.

Documentação e qualidade:

- `README.md`, `AGENTS.md`, `CLAUDE.md`, `.gitignore`, `.github/workflows/ci.yml`.
- `plans/README.md`, `plans/023-auditoria-completa-2026-07.md`, `docs/audits/2026-10-12/CORRECTIONS.md`.
- `specs/sprint-27-functional-audit-corrections/{SPEC,ARCHITECTURE,BACKLOG,VALIDATION,REVIEW,STATUS_RECONCILIATION}.md`,
  script de navegador e evidências locais nesse diretório.

Os relatórios/scripts/evidências da auditoria original e plano 060 já estavam
presentes e foram preservados; não são atribuídos como criação desta manutenção.

## Revisão, estado do Git e pendências

A revisão retomada em 2026-10-13 encontrou e corrigiu perdas de tools/usage em
falhas do provider, callbacks, interrupção CLI, persistência HTTP e quota da UI.
`tools_catalog` aprovou independentemente a integração HTTP final: 93 testes,
sem novo finding. `web_ui` aprovou independentemente o accounting bridge em
85 testes; o snapshot final CLI/native/loop foi aprovado em mais 154 testes
independentes, incluindo todas as 37 regressões partial tools e 30 bridge. Histórico completo e
responsabilidades estão em [REVIEW.md](REVIEW.md). A rodada anterior terminou
quando os agentes atingiram limite de uso; esse limite não substitui aprovação,
e motivou retomar a revisão nesta rodada.

Branch local: `codex/sprint-27-functional-audit-corrections`. HEAD preservado em
`feb67a1b3524bd315fcb8b56ca70575cfb7aa9b9`, um único worktree. Nenhum commit,
push ou alteração em configuração sensível; nenhum diff em pyproject/uv.lock.

Nenhuma correção funcional desta sprint permanece pendente. Permanecem excluídos
WEB-01/02/03/08/10, T01/02/03/04/05/06/08, K07/K08 e as partes de segurança de
T07/T09. Os 16 skips preexistentes e Windows/audio/providers reais não foram
validados neste ambiente Linux. Locks multiprocess Windows não são suportados;
SSE `/v1` continua bufferizado; parâmetros não implementados são explicitamente
rejeitados. Callers legados sem run persistido validam capacidade sem reservar
vaga. Depreciação de on_event e overrides de tipagem existentes continuam fora
desta correção. Revisão independente e suíte global final aprovadas, sem findings funcionais
abertos. As 323 warnings são depreciações existentes, sem falhas de validação.

## Verificação adicional de fechamento documental

O time reconferiu os critérios originais sem encontrar problema funcional aberto.
Foram reconciliados nove status TODO do plano 023 histórico e corrigido o comando
Ruff inválido em CLAUDE.md, com 29 regressões e 11 testes de revisão independente
aprovados. Paridade dev (11), ambos gates Ruff e diff-check também passaram.
Links locais foram verificados; nota do CI coincide com a matriz Ubuntu/Windows.

Nesta rodada apenas documentação mudou. A validação global de 7.982 testes acima
permanece aplicável; não foi repetida sem mudança de produção/testes/dependências.
Nenhum novo teste foi necessário para a correção documental; testes existentes
validaram os comportamentos que justificam os status. Revisão independente
aprovada e sem findings restantes; detalhes em
[STATUS_RECONCILIATION.md](STATUS_RECONCILIATION.md).
