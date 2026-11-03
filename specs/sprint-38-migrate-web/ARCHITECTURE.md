# ARCHITECTURE — Sprint 38

## O que já existe (não reescrever)

`tests/test_approval_chat_e2e.py` já tem toda a infraestrutura real
necessária:

- fixture `environment` (linha ~60): monta uma `app` real via
  `create_app(...)`, com `AgnoRuntimeAdapter._build_model` monkeypatchado
  para um `Model` determinístico (`TwoCommands`, sem rede/provider), e já
  liga `runtime_mode=agno` via `POST /api/runtime/mode` antes de devolver.
  Parametrizável via `request.param` para `_SERVE_POLICY_MODE`
  ("enforce"/"audit") e para instalar um `policy.yaml` customizado no
  workspace.
- `TwoCommands` (classe `Model`, linha ~25): script determinístico de duas
  tool calls em sequência — é o que faz o cenário de aprovação em duas
  etapas funcionar sem provider real.
- `start_stream(app, session)` (linha ~99): roda `GET /stream` numa thread
  separada (porque o endpoint bloqueia esperando aprovação), devolve
  `(thread, result)` — `result["response"]` só fica populado depois do
  `thread.join()`.
- `pending(approvals, *, exclude=(), timeout=6)` (linha ~113): faz polling
  na fila de aprovações pendentes até aparecer uma.
- `test_real_agno_chat_approval_runs_each_command_once` (linha ~185): o
  teste de referência — aprova a primeira tool, confirma idempotência
  (reenviar a mesma aprovação não quebra), aprova a segunda, confirma
  efeitos no workspace (`effects.txt`), confirma estado final do run,
  confirma isolamento entre sessões diferentes (uma sessão nova não herda
  aprovação `scope=run` da anterior).

## O que falta

`/chat` (POST, `bauer/server_chat.py:234`, via `chat_via_kernel`/
`executor` closure) chama o MESMO `run_chat_turn` (linha ~99) que já tem
o branch `if deps.runtime_mode() == "agno":` (linha ~108) — ou seja, o
mecanismo de dispatch já existe e é compartilhado com `/stream`. A
diferença é só a forma de transporte: `/chat` é uma chamada HTTP síncrona
que bloqueia até o turno terminar (igual `/stream` bloqueia dentro da
mesma requisição enquanto espera aprovação via o loop de
`_evaluate_runtime_policy` em `tool_router.py`) — não há SSE, só um
`POST` que demora.

Criar, no MESMO arquivo `tests/test_approval_chat_e2e.py` (reaproveitar
fixtures, não duplicar em arquivo novo):

1. Um helper `start_chat(app, session)` espelhando `start_stream`, mas
   fazendo `TestClient(app).post("/chat", json={"message": "execute
   test", "session_id": session})` numa thread.
2. Um teste `test_real_agno_chat_approval_runs_each_command_once` (nome
   análogo, no mesmo estilo, mas para `/chat`) cobrindo pelo menos: duas
   tool calls em sequência exigindo aprovação, aprovar via
   `POST /api/approvals/{id}/approve`, confirmar que a resposta HTTP final
   do `/chat` (depois do `thread.join()`) tem status 200 e o corpo
   (`ChatResponse.response`) contém o texto esperado, e que os efeitos no
   workspace aconteceram. Não precisa replicar TODOS os sub-casos que
   `/stream` já cobre (scope once/run/text, expiração, cancelamento) — um
   caminho feliz completo + um caso de `deny` já prova que o mecanismo
   funciona igual nesse transporte; duplicar toda a matriz seria
   redundante com o que `/stream` já garante para a MESMA função
   `run_chat_turn`/`_evaluate_runtime_policy` por baixo.

## Correção da documentação

Em `plans/062-agno-executor-padrao.md`, a seção `### Sprint 38` descreve
"parar de passar `executor=_turn_executor` incondicionalmente... permitir
`executor=None`" — isso não corresponde ao mecanismo real (`_runtime_mode()`
dentro do executor closure, não `kernel.execute(executor=None)`). Reescrever
essa seção para descrever o estado real, no mesmo estilo da correção já
feita para o sprint 36 (procure por "Correção de 2026-10-29" no mesmo
arquivo para ver o padrão usado).
