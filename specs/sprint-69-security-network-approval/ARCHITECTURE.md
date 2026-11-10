# ARCHITECTURE — Sprint 69

Nenhuma camada nova. As correções reaproveitam o `PolicyEngine`, o
`ApprovalManager`, a operação `tool.approval` (sprint 61) e a função
`validar_execucao_local`/`provider_e_local` (sprint 62).

## Itens 1 e 2 — operação e payload de política

```text
ToolRouter.execute
  └─ _evaluate_runtime_policy(name, args)
       ├─ _policy_operation_for_tool
       │    web_search                → network.search
       │    mcp_call, mcp_list_tools  → mcp.call
       │    delegate_task             → agent.delegate
       │    lsp_rename                → tool.approval
       ├─ _runtime_policy_payload(router, operation, name, args)   (novo, tool_router.py)
       │    network.http   → tools/web.network_policy_payload        (sprint 62)
       │    network.search → tools/web.search_policy_payload         query, backend, contains_secret
       │    mcp.call       → tools/mcp.mcp_policy_payload            server, tool, kind, transport, host, remote, contains_secret
       │    agent.delegate → ExecToolsMixin._delegate_policy_payload dispatch, host, remote, provider, provider_local, contains_secret
       │    tool.approval  → {**args, tool_name}                     tool_name escrito pelo runtime
       ├─ PolicyEngine.evaluate(operation, payload)
       └─ deny → ToolError | ask → ApprovalManager (payload = args originais)
```

Os campos calculados pelo runtime entram depois de `dict(args)`. O modelo não
consegue sobrescrever `remote`, `host`, `tool_name` nem rebaixar
`contains_secret`, que é `flag_do_modelo OR scanner`.

A resolução do payload não faz I/O de rede:

- o MCP usa só config/env (`_resolve_mcp_transport`). Um servidor
  desconhecido vira `unconfigured`;
- a busca lê `_raw_search_backend`, sem auto-detecção;
- a delegação usa `_resolve_delegate_agent`, que lê o registry de agentes do
  disco.

## Item 3 — `/model` em `bauer agent --local`

```text
bauer agent --local
  └─ run_agent_session(..., local_only=True)                 (agent.py, kwarg novo)
       /model
        ├─ run_model_switcher(cfg, local_only=True)           camada 1 (model_switcher.py)
        │    opções ∩ LOCAL_PROVIDER_IDS ({"ollama"}); outro id → recusa sem escrever
        ├─ rebuild_client_fn()
        │    = agent_cmd._rebuild_client_from_config(local=True)  camada 2
        │      validar_execucao_local(cfg relido) → LocalSwitchRefused antes de _build_client
        │      fallbacks: conjunto="local" (fallback_models_local)
        │      client._provider = cfg.model.provider
        └─ provider_e_local(client._provider | provider_from_client)   camada 3 (agent.py)
             não local → mensagem, sessão continua no modelo anterior
```

`LocalSwitchRefused` cai no `except Exception` que já existia no `/model` e
aparece como "Erro ao trocar modelo: --local recusado: …".

## Item 3b — `delegate_task` numa sessão `--local`

```text
bauer agent --local | bauer serve --local
  └─ validar_execucao_local ok → model_router.mark_local_only()   (os.environ BAUER_LOCAL_ONLY=1)

delegate_task → _execute_delegated_task
  local_only = router._local_only or local_only_active()
  ├─ agente com url: host fora da máquina → ToolError (antes do POST)
  ├─ client da sessão: usado só se _client_is_local (_provider | classe Ollama | host local)
  └─ subprocess: bauer agent run-one ... --local   (+ BAUER_LOCAL_ONLY herdado)
        run-one: --local ou marca → provider_e_local(cfg) antes de _build_client
                 nuvem → exit 1 | local → mark_local_only() (aninhado herda)
```

## Arquivos

- `bauer/tool_router.py`: quatro entradas no mapa e `_runtime_policy_payload`.
  A edição em `_evaluate_runtime_policy` é uma linha.
- `bauer/tools/web.py`: `search_policy_payload`.
- `bauer/tools/mcp.py`: `mcp_policy_payload`.
- `bauer/tools/execution.py`: `_delegate_policy_payload`.
- `bauer/core/policy/engine.py`, `config/policy.yaml`: regras padrão de
  `network.search`, `mcp.call` e `agent.delegate`.
- `bauer/core/policy/risk.py`: `contains_secret` vira risco alto em
  `network.search`/`mcp.call`, e no `agent.delegate` remoto. `mcp.call` vira
  risco médio.
- `bauer/model_switcher.py`: `local_only` e `LOCAL_PROVIDER_IDS`.
- `bauer/commands/agent_cmd.py`: `LocalSwitchRefused` e
  `_rebuild_client_from_config`. O `bauer agent` passa `local_only`.
- `bauer/agent.py`: o kwarg `local_only` e a terceira camada no `/model`.
- `bauer/model_router.py`: `LOCAL_ONLY_ENV`, `local_only_active` e
  `mark_local_only`.
- `bauer/commands/serve_cmd.py`: o `serve --local` grava a marca.
- `bauer/commands/agent_cmd.py`: o `agent --local` grava a marca, e o
  `run-one --local` verifica o config e recusa iniciar se ele for de nuvem.
- `bauer/tools/execution.py`: os helpers `_delegate_local_only`,
  `_url_is_local` e `_client_is_local`, e as três guardas no despacho.
- `tests/conftest.py`: a marca é isolada por teste.
- `tests/test_sprint69_network_approval_boundaries.py`: regressões.
