# BACKLOG — Sprint 27

Todas as tarefas exigem reprodução, correção, regressão e revisão independente.
Status final: CONCLUÍDO no escopo funcional. Baseline anterior: 7.706 passed, 16 skipped; Ruff
crítico/amplo e mypy aprovados. K01 self-count já corrigido antes desta sprint.

| Tarefa | Responsável | Status | Aceite |
| --- | --- | --- | --- |
| K02 terminais e transições CAS | kernel_runtime | CONCLUÍDO; revisão tools_catalog aprovada | cancelamento/executor/gate/retry/start/stream e dois managers preservam terminal |
| K03 liquidação de falhas e gates | kernel_runtime | CONCLUÍDO; revisão tools_catalog aprovada | custo conhecido liquidado em falha, cancel, gate e desconexão; snapshots antigos não reduzem consumo |
| K04 métricas por tentativa | kernel_runtime | CONCLUÍDO; revisão tools_catalog aprovada | retry/fallback/replan somam custo/tools/LLM com patches atômicos |
| K10 duração total | kernel_runtime | CONCLUÍDO; revisão tools_catalog aprovada | elapsed explícito inclui gates e cancel/disconnect normal+stream |
| K09 liveness comum | kernel_runtime | CONCLUÍDO; revisão tools_catalog aprovada | heartbeat fresco e waits contam; run morto recupera pela mesma regra |
| K01 admissão atômica | kernel_runtime | CONCLUÍDO; revisão tools_catalog aprovada | candidatos concorrentes caps 1/2, reserva multiprocess e reuso de vaga terminal |
| K05 usage no cost sink | coordenador | CONCLUÍDO; revisão tools_catalog aprovada | uso bruto/canônico, cache, zero local e native→budget; 192 testes combinados passaram |
| K06 parser Git de escopo | coordenador | CONCLUÍDO; revisão tools_catalog aprovada | arquivos individuais, renames, whitespace, CR e bytes não UTF-8; 192 testes combinados passaram |
| T09 schemas efetivos | tools_catalog | CONCLUÍDO; revisão coordenador aprovada | extensão/override/allowlist e schema único; testes dirigidos passaram |
| T10 tipos e required | tools_catalog | CONCLUÍDO; revisão coordenador aprovada | 46 contratos passaram: 35 ações mínimas, arrays/items e SQLite heterogêneo |
| T07 cache Kanban | tools_catalog | CONCLUÍDO; revisão coordenador aprovada | escrita não replaya/leitura invalidada e TestsGate fora Git; sem alterar política |
| WEB-04 sessões concorrentes | web_ui | CONCLUÍDO; revisão coordenador | cinco transportes, sessão compartilhada/distinta, locks entre instâncias/processos e timeout |
| WEB-05 transcript `/v1` | web_ui | CONCLUÍDO; revisão coordenador | transcript autoritativo e incremental explícito; batch/SSE sem duplicação |
| WEB-06 paridade transporte | web_ui | CONCLUÍDO; revisão coordenador | native/bridge/tools/fallback/accounting batch=SSE; falhas parciais e usage anterior |
| WEB-07 parâmetros `/v1` | web_ui | CONCLUÍDO; revisão coordenador | model ativo/instruções aplicados; limites não suportados rejeitados com 422 |
| WEB-09 lifecycle de stream | web_ui | CONCLUÍDO; revisão coordenador | dono do turno sobrevive navegação; storage/quota não prende busy; teste Chromium/reload |
| UI-01 HUD do modelo | web_ui | CONCLUÍDO; revisão coordenador | switch Chat/Models publica seleção; respostas antigas não apagam modelo novo; Chromium |
| Paridade Ruff e docs Kernel/store | coordenador | CONCLUÍDO; revisão tools_catalog | Ruff isolado 0.17.0 fixado em docs e CI; ambos gates aprovados |
| Reconciliação de status do plano 023 histórico | coordenador | CONCLUÍDO; revisão tools_catalog aprovada | nove status; comando CLAUDE/Ruff corrigido; 29 regressões + 11 independentes; links locais e diff-check aprovados |
| Revisão cruzada e validação final | equipe | CONCLUÍDO — revisão independente final aprovada | 7.982 Python/16 skips, Ruff/mypy, 29 frontend, build, cinco checks Chromium e diff-check aprovados; ver REVIEW.md |

Itens de segurança excluídos pelo escopo atual não são marcados concluídos. Se o
usuário ampliar o escopo, atualizar SPEC e acrescentar tarefas antes de editar.

Revisão HTTP de kernel_runtime identificou três falhas (consumo de falha,
replan cumulativo e cancelamento antes do lock); revisão UI de tools_catalog
identificou races de status e falha auxiliar mantendo busy. Implementação de
web_ui e conclusão pelo coordenador cobrem os findings com testes dirigidos e
Chromium controlado. Na primeira rodada, os agentes atingiram limite de uso
antes da última integração; o coordenador assumiu CLI/scheduler, as regressões
restantes e a validação global. Naquele momento a integração final ainda não
tinha aprovação independente; a retomada abaixo encerrou essa pendência.

A retomada em 2026-10-13 resolveu a pendência de revisão final. Revisores
reproduziram e corrigiram gaps nas mesmas tarefas K03/K04/WEB-06/WEB-09:
tools parciais, usage/retries/cancelamento, persistência de sessões e quota de
localStorage. Snapshot HTTP aprovado por tools_catalog; bridge e CLI/native/loop
aprovados por web_ui. 370 testes dirigidos da implementação e 154 independentes
aprovados; ver REVIEW.md. Segurança permanece fora do escopo.

Validação global final em 2026-10-13: **7.982 passed, 16 skipped**, Ruff
crítico/amplo, mypy (377 arquivos), frontend (29), build, Chromium (5 checks)
e diff-check aprovados. Nenhuma correção funcional autorizada pendente.

Verificação adicional de fechamento: equipe confirmou os 17 itens sem novos
findings funcionais (191 testes runtime e 75 tools/Ruff); reconciliação de nove
status antigos concluída, com 29 regressões e mais 11 de revisão independente.
Comando Ruff de CLAUDE.md corrigido e executado offline. Nenhuma produção,
teste ou dependência modificada nesta rodada documental. Ver
[STATUS_RECONCILIATION.md](STATUS_RECONCILIATION.md).
