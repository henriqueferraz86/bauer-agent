# Plan 051: Impor limites reais a timeout de tools e tarefas de memória

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: 050 (DONE)
- **Category**: reliability, resource safety, tests
- **Planned at**: commit `997c84d`, 2026-08-25
- **Completed**: merge `0bb8ae4` on `master`, 2026-08-25

## Objective

Fazer com que os timeouts de tool devolvam controle ao agente no prazo
anunciado e impedir que consultas/gravações de memória lentas criem threads
sem limite a cada turno.

## Evidence

- `bauer/tool_router.py:1765-1775` usa `ThreadPoolExecutor` como context
  manager. Ao ocorrer `future.result(timeout=...)`, o `raise ToolError` sai do
  bloco, mas `Executor.__exit__()` executa `shutdown(wait=True)`: a chamada
  continua bloqueada até a tool lenta terminar. Portanto o timeout não cumpre o
  contrato observável pelo caller.
- `bauer/memory_context.py:164-169` cria duas threads daemon por prefetch e
  aguarda no máximo dois segundos. Se a busca não retorna, ambas permanecem
  vivas; novos turnos criam mais duas threads. `sync_memory_after_turn` em
  `:282-283` também inicia uma thread por resposta substantiva sem limite.

## Scope

- `bauer/tool_router.py`
- `bauer/memory_context.py`
- testes correspondentes em `tests/test_tool_router*.py` e
  `tests/test_memory_context.py`

Fora de escopo: cancelar à força código Python em thread, modificar o contrato
de subprocessos, trocar os backends de memória, ou alterar a semântica das
browser tools (elas já usam executor persistente por afinidade de thread).

## Required behavior

1. Uma tool não-browser com timeout deve retornar `ToolError` próximo ao prazo,
   sem esperar a conclusão do worker. A execução lenta pode não ser cancelável
   em Python, mas não pode provocar criação ilimitada de workers, filas ou
   threads em chamadas repetidas.
2. Preserve o comportamento e a afinidade do executor persistente de browser.
   Exceções e o texto de timeout das demais tools permanecem compatíveis.
3. Prefetch e sync de memória devem ter capacidade global limitada. Quando ela
   estiver saturada, a memória é best-effort: a chamada deve retornar sem
   bloquear e sem enfileirar trabalho ilimitado; resultados parciais/ausentes
   são aceitáveis.
4. Mantenha o contrato de `sync_memory_after_turn` para o caso aceito: ele
   ainda retorna um objeto com `join()` para testes/callers. Para trabalho
   recusado por saturação, `None` é permitido e deve ser documentado.
5. Não introduza nova configuração pública. Use constantes internas pequenas,
   com nomes claros e cleanup/release garantido em `finally`.

## Verification

- Teste determinístico de tool lenta prova que `execute()` retorna erro de
  timeout antes da conclusão do worker; repetição não aumenta trabalhadores
  além do limite interno.
- Testes de memória simulam buscas/gravações bloqueadas e comprovam que o
  número de tarefas ativas/aceitas é limitado, que o prefetch retorna rápido e
  que a capacidade é liberada depois da conclusão.
- `uv sync --frozen --extra dev`
- `uv run pytest tests/test_tool_router.py tests/test_tool_router_gaps.py tests/test_memory_context.py -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
- `uv run ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303`
- `uv run pytest tests/ -q --tb=short`
- `git diff --check`

## Done criteria

- O timeout deixa de bloquear pela espera implícita do context manager.
- Nenhum caminho de memória cria trabalho ilimitado sob backend lento.
- Testes cobrem o limite e a recuperação da capacidade, sem `sleep` frágil ou
  rede real.
- Suíte e lint bloqueante passam; qualquer falha ampla de lint deve ser
  comparada à `master` antes de ser tratada como regressão.

## STOP conditions

- A única forma de garantir timeout exige matar thread ou mudar o contrato de
  uma tool com side effect em curso.
- O limite de memória exige descartar escrita já aceita ou mudar a persistência
  de sessão/decisão de modo incompatível.

## Commit

`fix(runtime): bound timed-out tools and memory workers`

## Execution record

- Timed non-browser tools run through a bounded persistent executor, so a
  timeout returns immediately without waiting for executor shutdown.
- Prefetch and asynchronous memory writes share bounded worker slots; saturated
  best-effort work is skipped instead of creating unlimited threads or queued
  tasks.
- Browser executor behavior remains unchanged.
- Focused tests, critical Ruff, diff check and the full suite passed before and
  after merge. Broad Ruff retains the pre-existing 87-error baseline.
