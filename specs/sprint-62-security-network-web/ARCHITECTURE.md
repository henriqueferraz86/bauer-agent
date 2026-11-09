# ARCHITECTURE — Sprint 62

Nenhuma camada nova. As quatro correções reaproveitam o `PolicyEngine`, o
middleware de auth/CSRF e a função `provider_e_local`, que já existiam.

## T02 — política `network.http`

```text
ToolRouter.execute
  └─ _evaluate_runtime_policy(name, args)
       ├─ _policy_operation_for_tool: http_request|web_fetch → "network.http"
       ├─ network_policy_payload(args)    (bauer/tools/web.py)
       │    url, method, scheme, host, port, has_credentials,
       │    contains_secret = flag_do_modelo OR has_secrets(url+corpo+headers não-credenciais)
       ├─ PolicyEngine.evaluate("network.http", payload)
       └─ deny → ToolError | ask → ApprovalManager (payload original `args`)
```

O payload enriquecido só é usado para avaliar a regra. A autorização e o
digest de aprovação continuam usando `args` original. Assim, uma aprovação
continua presa à chamada exata que o modelo fez.

## T03 — redirect por hop

`WebToolsMixin._http_request` já seguia redirects manualmente (por causa do
SSRF). Cada hop agora calcula:

- `_origin(url)`: `(scheme, host em minúsculas e sem ponto final, porta efetiva)`;
- `_redirect_method(status, method)`: devolve `(próximo_método, descartar_corpo)`.

Com isso:

- origem diferente → `_without_sensitive_headers`;
- corpo descartado → `_without_body_headers`;
- origem diferente com corpo ainda presente (307/308) → o loop para e a
  resposta 3xx é devolvida.

## WEB-02 — stream de time

O `desktop_api.team_stream` passou de GET com `Query` para POST com corpo
JSON (`task`, `session_id`, `user_id`). O `_verify_key` do servidor já exige
CSRF de toda sessão por cookie em método mutável (`_csrf_required`), então
não foi preciso adicionar caso especial. O API key continua funcionando sem
CSRF, como no resto da API.

## WEB-03 — garantia `--local` depois do boot

`create_app` deriva `_local_only = profile_set == "local"` e
`_provider_allowed(provider)` (`provider_e_local` com o config do serve). Os
pontos que consultam a função, todos antes de qualquer mutação de `_state`:

| Porta | Comportamento em `--local` |
|---|---|
| `POST /models/switch` | 409 antes de construir client |
| restauração de `serve-model-state.json` | ignorada com warning |
| `_on_openai_auth_connected` | devolve aviso e não constrói client |
| `fallback_clients` | clients não locais descartados na criação do app |
| `_client_for_profile` (roteamento por tier) | devolve `None`, e o turno cai no primário local |
| `validar_execucao_local` (CLI `serve`/`agent --local`) | recusa `profiles_local` com tier remoto |

## Arquivos

- `bauer/tool_router.py`: mapeamento e payload de `network.http`.
- `bauer/tools/web.py`: redirect seguro e `network_policy_payload`.
- `bauer/desktop_api.py`: `POST /api/teams/{id}/stream`.
- `bauer/server.py`: guardas `--local`.
- `bauer/model_router.py`: `validar_execucao_local` com tiers locais.
- `tests/test_sprint62_network_web_boundaries.py`: regressões.
- `tests/test_server_contract.py`: contrato de rotas (GET → POST).
- `docs/audits/2026-10-12/repro/bauer_web_repro2.py`,
  `bauer_web_crossreview_kernel.py`: rodam até o fim.
