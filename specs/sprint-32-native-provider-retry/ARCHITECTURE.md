# ARCHITECTURE

Retry fica dentro do while native de _run_one_turn, nunca em run_loop_rounds nem
run_one_turn_with_fallback como replayglobal. Cada chamada já passa por consume
LLM/native reportcost. Preserva ctx/tool_log/deduper/guardrail e não restaura
snapshot. Usar classificador existente com allowlist de motivos transitórios.
run_one_turn/_run_one_turn optional interrupt_check callable->str|None, injetado
pelo /loop (_should_stop). Helper espera em fatias<=0.2s e verifica budget.
Interrupção privada ProviderRetryInterrupted reason budget_exhausted/cancelled/
kill_switch carrega partial tools pela fachada existente; serve_loop reconhece
sinal tipado, mantendo cancel e limites como estados corretos de custódia.
Delta sink on_retry(attempt,delay_s) cosmético; emitter best effort, LoopLiveSink
atualiza activity e publica agent.retry metadata bounded sem erro/prompt/args.

A fronteira de request marca privadamente a exceção antes de devolver ao while.
Assim nenhum erro de dispatch/tool pode acionar retry. Retomada emite on_round
para remover estado de espera. Interrupção tipada retorna diagnóstico factual
se nenhum texto foi produzido, mantendo NonEmptyOutput e gates ativos.

/loop sincroniza o delta de custo recebido pelo recorder antes da próxima
requisição/espera. Falhas na consulta de stop/kill-switch propagam; somente o
sink de atividade é best effort. Custo e contagem incluem chamadas com falha;
uso ausente não reaproveita last_usage anterior. /v1 compartilha esse motor e
seu teste de fallback passa a exigir três requests primários + um fallback.
