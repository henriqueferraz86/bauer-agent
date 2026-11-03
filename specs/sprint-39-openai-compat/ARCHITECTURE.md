# ARCHITECTURE — Sprint 39

## Referência direta: o diff do sprint 38

O commit `770fe08` (`fix(chat): /chat now waits for tool approval and
persists Agno's reply`) em `bauer/server_chat.py` é o template exato a
replicar aqui, adaptado para `server_openai.py`. Rode `git show 770fe08 --
bauer/server_chat.py` para ver o diff completo antes de escrever qualquer
linha — não reinventar o padrão.

## Passo a passo

1. `OpenAIRouteDependencies` (`bauer/server_openai.py:~42`): adicionar
   `runtime_mode: Callable[[], str]` e
   `run_agno_turn: Callable[..., dict[str, Any]]`.
2. `bauer/server.py:2706` (`build_openai_router(OpenAIRouteDependencies(...))`):
   adicionar `runtime_mode=_runtime_mode, run_agno_turn=_run_agno_turn,`
   — mesmas closures já passadas para `ChatRouteDependencies` (linhas
   1502-1503).
3. Em `execute_v1` (`bauer/server_openai.py:~186`), onde hoje chama
   incondicionalmente `deps.run_one_turn_with_fallback(...)`: envolver
   com `if deps.runtime_mode() == "agno": result = deps.run_agno_turn(
   message, sid, run.id, request_agent_id, deps.router, active_model,
   resolved); answer, tool_log = result["response"], result["tool_log"]`
   (confira a assinatura exata de `run_agno_turn`/`_run_agno_turn` em
   `server.py` — os argumentos posicionais precisam bater). No branch
   Agno, adicionar `ctx.add_assistant(deps.format_response(answer))`
   depois (mesmo raciocínio do sprint 38: o adapter Agno não escreve em
   `ctx`).
4. Em `execute_completion` (a função externa que envolve `execute_v1`,
   mesmo escopo onde `cost_token`/`ids_token` já são instalados): instalar
   `wait_token = set_policy_approval_wait(True)` e
   `reset_policy_approval_wait(wait_token)` no `finally` existente — igual
   ao padrão do sprint 38, nos dois branches (este endpoint só tem UM
   caminho de execução via Kernel, ao contrário de `/chat` que tinha o
   branch legado sem Kernel também — confirmar lendo o arquivo se existe
   um branch sem Kernel aqui também antes de assumir que só precisa de um
   ponto).
5. Teste: adicionar em `tests/test_approval_chat_e2e.py` (reaproveitar
   fixture `environment`/`TwoCommands`/`pending`, mesmo padrão de
   `start_chat`/`start_stream`) um helper `start_openai_completion`
   (`TestClient(app).post("/v1/chat/completions", json={"messages": [...],
   "session_id": session})` numa thread) e um teste espelhando
   `test_real_agno_post_chat_approval_runs_each_command_once`.
6. `bauer doctor`: procurar onde `preflight.py`/`cli.py::doctor` já
   imprime outras informações de runtime (modelo ativo, provider, etc.) e
   adicionar uma linha "Executor ativo (web): {runtime_mode()}" no mesmo
   formato — não criar uma seção nova do zero se já existe um padrão de
   "linha de status" a seguir.
7. ADR: um arquivo curto, só com a decisão e o porquê — não um documento
   longo. Ver o conteúdo já esboçado na SPEC.md, seção 7.
