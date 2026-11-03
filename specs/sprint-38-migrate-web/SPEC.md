# SPEC — Sprint 38: migrar o caminho web para Agno (plano 062)

## Achado principal (corrige a descrição original do plano 062)

O texto original do sprint 38 em `plans/062-agno-executor-padrao.md`
descreve um trabalho que **já não reflete o código atual**: "parar de
passar `executor=_turn_executor` incondicionalmente em `server_chat.py`;
permitir `executor=None`". Investigação em 2026-11-03 (checkout `6b839a1`)
mostra que isso já não é o estado do código:

- `bauer/server_chat.py:108` (dentro de `run_chat_turn`, chamada pelo
  `executor` closure do `/chat`): `if deps.runtime_mode() == "agno":
  result = deps.run_agno_turn(...)` — **o `/chat` já despacha
  condicionalmente para o Agno**, usando o mesmo toggle `_runtime_mode()`
  (`GET/POST /api/runtime/mode`) que `/stream` já usa desde antes do plano
  062 existir. Não é o padrão `kernel.execute(executor=None)` que o CLI
  (sprint 37) usa — é um padrão mais antigo e diferente, mas funcionalmente
  já resolve "o Agno roda de verdade neste caminho".
- `bauer/server.py` (`/stream`, achado já registrado na correção do sprint
  36 em `plans/062-agno-executor-padrao.md`) tem o mesmo padrão,
  `_run_agno_turn`/`_on_agno_event`.
- `tests/test_approval_chat_e2e.py::test_real_agno_chat_approval_runs_each_command_once`
  já existe e já cobre aprovação assíncrona (approve/deny via API, escopos
  once/run/text, expiração, cancelamento enquanto espera, isolamento entre
  sessões) com Agno real ativo — **mas só via `/stream`** (usa o helper
  `start_stream`). Nenhum teste equivalente existe para `/chat` (POST),
  que é o endpoint síncrono separado, com approval+Agno.

Conclusão: o item "permitir executor=None" do sprint 38 já está feito, só
que por um mecanismo diferente do que o plano previa. O trabalho real que
falta é mais estreito: (a) `/chat` precisa da MESMA cobertura de teste real
que `/stream` já tem para aprovação+Agno, porque hoje ninguém provou que
funciona nesse endpoint especificamente; (b) a documentação do plano 062
precisa ser corrigida para não descrever um trabalho que já não existe.

## Objetivo e escopo desta sprint

1. Corrigir a seção do sprint 38 em `plans/062-agno-executor-padrao.md`
   para descrever o estado real (achado acima), análogo à correção já
   feita para o sprint 36/`/stream`.
2. Escrever teste real (Agno SDK real, sem provider, mesmo padrão de
   `test_approval_chat_e2e.py`) cobrindo `/chat` com
   `runtime_mode=agno` + uma tool que exige aprovação — comprovar que o
   fluxo approve/deny via API funciona igual ao que já está provado para
   `/stream`. Reaproveitar os fixtures/helpers existentes do arquivo
   (`environment`, `TwoCommands`, etc.) — não duplicar infraestrutura de
   teste.
3. Se o teste encontrar uma divergência real de comportamento entre
   `/chat` e `/stream` nesse cenário, tratar como bug a corrigir nesta
   sprint (não é esperado, mas não presumir sem o teste rodar).
4. Verificar se `bauer doctor`/painel já expõem qual executor está ativo
   por canal — item do sprint 39 na redação original, mas útil checar
   aqui se já existe por acidente (não implementar se não existir; só
   registrar o achado).

## Fora de escopo

- Reestruturar `/chat`/`/stream` para usar o padrão
  `kernel.execute(executor=None)` do CLI em vez do `_runtime_mode()`
  bespoke — funciona, está testado (pelo menos para `/stream`), mexer
  nisso sem motivo é risco sem ganho.
- Migrar OpenAI-compat (sprint 39) ou Telegram (sprint 40).
- Qualquer mudança em `AgnoRuntimeAdapter`/`delta_stream.py`/Kernel.

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy`

## Critérios de aceite

- Teste real (não mock do Agno) comprova que `/chat` com `runtime_mode:
  agno` e uma tool call que exige aprovação completa o fluxo
  approve/deny via API corretamente — mesma garantia que `/stream` já
  tem provada.
- `plans/062-agno-executor-padrao.md` (seção do sprint 38) descreve o
  estado real do código, não o planejado-mas-já-obsoleto.
- Suíte completa + Ruff bloqueante/estilo verdes.
- Nenhuma regressão nos testes existentes de `/stream`/`/chat`/approval.

## STOP

Se o teste novo encontrar uma divergência de comportamento entre `/chat`
e `/stream` que exija mudança estrutural maior que corrigir um bug pontual
(ex.: `/chat` precisar de uma reestruturação equivalente ao que `/stream`
já tem para funcionar de verdade), parar e voltar ao operador antes de
fazer uma mudança grande não planejada.
