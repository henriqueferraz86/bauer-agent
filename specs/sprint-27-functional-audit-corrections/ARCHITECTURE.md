# ARCHITECTURE — Sprint 27

## Runtime

Reutilizar SQLite/RunManager/Kernel e ledger existentes. Mesclar métricas dentro de
transações; atualizar terminais por compare-and-set e publicar eventos apenas para
transições efetivamente aceitas. Cálculo compartilhado de liveness usa timestamps
e heartbeat. Admissão de vagas deve serializar leitura/reserva entre managers,
sem confundir candidato persistido com execução admitida. Tentativas têm métricas
incrementais, agregadas uma vez; consumo é independente do status de saída.

## Contratos de tools

Gerar schemas a partir do catálogo efetivo após override/allowlist. Centralizar
tradução de parâmetros com listas/items e distinguir campos incondicionais dos
exigidos por uma ação. Dedup deve invalidar leituras após kanban_write, sem alterar
regras de aprovação, política ou permissões neste escopo.

## Sessões e transporte

Um ciclo de turno compartilhado recebe transcript/params e produz respostas/eventos
para sinks batch/SSE. Serializar load→execute→save por sessão; sessões diferentes
podem executar simultaneamente. Transcript `/v1` completo é autoritativo, e o modo
incremental precisa de regra explícita que preserve compatibilidade. Preservar
custódia Kernel, accounting, fallback, native/bridge e budgets do motor existente.

O contrato implementado de `/v1` usa `history_mode=transcript` por padrão;
`incremental` é explícito. `model` deve coincidir com o modelo ativo e
`temperature`/`max_tokens` não suportados são rejeitados com 422. Batch e SSE
usam o mesmo motor governado; SSE continua emitindo o resultado após o turno,
sem prometer tokens em tempo real. Locks de sessão combinam RLock e flock no
POSIX; Windows garante serialização apenas no mesmo processo. Timeout ou saída
do observador não transfere custódia: o worker mantém execução e persistência.

## UI

Manter ciclo de requisição e persistência em dono que sobreviva à navegação.
Atualização de modelo usa
fonte compartilhada e publica mudança para Chat/Models; HUD lê estado real.

## Coordenador

Normalizar usage na ponte de custo preservando os demais campos, com semântica
explícita de cache Anthropic. Parser Git usa porcelain NUL e untracked=all, com
renames tratados sem perder caracteres. Roteiro Ruff deve funcionar offline com
ambiente já disponível e descrever fielmente o CI.

## Propriedade de arquivos

Kernel/runtime: kernel.py, run_manager.py, autonomy.py, budget_ledger.py,
resilience.py, state_store.py e testes próprios. Tools: tool_router.py,
tool_dedup.py, tool_schema.py e testes próprios. HTTP/UI: server.py,
server_openai.py, módulos web/session relacionados e desktop. Coordenador:
cost_meter.py/account_usage.py/usage_pricing.py, gates/scope.py, testes próprios e
documentação. Alterações fora dessas áreas exigem aviso ao coordenador para evitar
edição concorrente.

Revisão de integração estendeu K03/K04 a `commands/run_cmd.py` e
`core/runtime/scheduler.py` (coordenador): CLI devolve deltas por invocação do
executor e liquida custo parcial anterior ao callback de rodada; scheduler
preserva métricas de falhas/limite de tools e registra o acumulado do Run.

Revisão retomada em 2026-10-13 estendeu a preservação de consumo aos motores
`agent.py`/`serve_loop.py` (kernel_runtime): exceções carregam
`partial_tool_log`, fallback agrega tentativas e callbacks preservam o trabalho
já executado. `agent_native_turn.py`/`commands/run_cmd.py` incluem interrupções
pelo usuário; `agent_native_execution.py` preserva a medição best-effort
quando o acesso a usage falha. `core/kernel/entry.py` recebe métricas parciais ao traduzir
Ctrl+C em cancelamento. `agent_response.py` e o coletor de `openai_client.py`
(tools_catalog) registram cada tentativa bridge, inclusive usage parcial e
retries internos; wrappers aninhados não duplicam cobrança. O coordenador integra
os logs parciais e falhas de persistência nas fronteiras HTTP; persistência de
sucesso pertence ao executor antes de declarar terminal. WEB-09 cobre também
quota de localStorage na montagem, atualização e reset da tela.

A verificação adicional de fechamento reconcilia status do plano 023 histórico.
Coordenador modifica os status, o comando Ruff em CLAUDE.md e a nota de
revalidação documental, com
fontes e testes já existentes. tools_catalog revisa documento/links/registries;
kernel_runtime confirma evidência de runtime/memória. A manutenção fica na
branch local vigente, sem executar instruções históricas de commit/push.
