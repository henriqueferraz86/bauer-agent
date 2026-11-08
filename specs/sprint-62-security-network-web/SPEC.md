# SPEC — Sprint 62: fronteiras de rede e web (plano 064, lote 2)

## Origem

Auditoria de 2026-10-12 (`docs/audits/2026-10-12/`), lote 2 do
`CORRECTIONS.md`. Esta sprint cobre os achados de rede e web: T02, T03,
WEB-02 e WEB-03. T01, T06 e T08 (também do lote 2) são da sprint 61.

## Reprodução no código atual (base `ddd608c`)

Antes de qualquer edição, cada achado foi reproduzido com transporte,
orquestrador e construtor de client falsos:

| ID | Prova antes da correção | Situação |
|---|---|---|
| T02 | `_policy_operation_for_tool('http_request') is None`; regra `deny` de `network.http` no `.bauer/policy.yaml`, `policy_enabled=True`: as duas requisições falsas chegam ao transporte e a tool devolve `HTTP 200` | reproduz |
| T03 | `https://source.example/start` → 302 → `https://other.example/target`; o segundo hop recebe `Authorization: Bearer audit-token` | reproduz |
| WEB-02 | Sessão por cookie, sem `X-CSRF-Token`: `GET /stream` → 403; `GET /api/teams/fake/stream?task=...` → **200** e `orchestrator.stream.call_count == 1` (`bauer_web_repro.py`) | reproduz |
| WEB-03 | `profile_set='local'`, client Ollama: `POST /models/switch {provider: openai}` → `LOCAL_SWITCH 200 {'active': 'fake-cloud', 'provider': 'openai'}` (`bauer_web_repro2.py`) | reproduz |

Os scripts `bauer_web_repro2.py` e `bauer_web_crossreview_kernel.py`
quebravam em `assert b_started.wait(10)` antes de imprimir o resultado do caso
concorrente. O motivo não era thread que não sobe: o WEB-04 (sprint 27) passou
a serializar load→execute→save por sessão, então B só entra depois que A
salva. Os scripts agora registram `CONCURRENT_B_OVERLAPPED_A False`, liberam
A, esperam B e rodam até o fim.

## Objetivo

Todo cenário T02/T03/WEB-02/WEB-03 é negado antes do handler ou do
transporte. As ações autorizadas continuam funcionando.

## Escopo

- **T02** — `http_request` e `web_fetch` passam a ser mapeadas para a
  operação `network.http`. A política avalia um payload normalizado (URL,
  método, scheme/host/porta, `has_credentials`) e o `contains_secret` é
  detectado pelo runtime. A flag enviada pelo modelo só pode aumentar o risco.
- **T03** — o redirect manual do `http_request` passa a seguir a semântica de
  redirect a cada hop:
  - mudança de origem (scheme, host ou porta efetiva) remove headers de
    credencial;
  - 303, e 301/302 vindos de POST, viram GET sem corpo;
  - 307/308 preservam método e corpo só na mesma origem;
  - o hop interno continua barrado pelo SSRF guard antes do request.
- **WEB-02** — iniciar um time passa a ser `POST /api/teams/{id}/stream`. O
  método é mutável, então o middleware exige CSRF de toda sessão por cookie.
  O GET deixa de existir e devolve 405. Para observar uma run, o caminho
  continua `GET /api/teams/runs/{id}/events`.
- **WEB-03** — com `profile_set='local'`, toda porta que troca ou constrói
  client verifica `provider_e_local` antes de mutar o estado:
  - `/models/switch` recusa com 409, antes de construir client;
  - a seleção salva em `serve-model-state.json` é ignorada no boot;
  - a seleção automática pós-OAuth é recusada (a credencial continua
    guardada);
  - clients de fallback que não são locais são descartados;
  - o roteamento por tier nunca constrói client de profile remoto;
  - `validar_execucao_local` recusa subir quando `profiles_local` aponta para
    a nuvem.

## Fora de escopo

- `web_search`, `mcp_call` e `delegate_task`. Têm contrato próprio: provider
  configurado, servidor MCP declarado e `agent.delegate`. O
  `TOOLS-REVIEW.md` pede que não sejam tratados como a mesma operação sem um
  contrato de mapeamento, e esse contrato fica para uma sprint própria.
- O `/model` do `bauer agent --local` interativo. Ele reescreve o
  `config.yaml` com o operador no teclado, e o achado WEB-03 é sobre a API
  web. Fica registrado como pendência.
- T01, T06, T08 (sprint 61), WEB-01, T04, T05 (sprint 59), K07, K08 (sprint 60),
  WEB-08, WEB-10 (sprint 63).

## Decisões

1. **`Authorization` não conta como exfiltração (T02).** A regra padrão
   `network.http.secret_exfiltration.deny` casa com `contains_secret`. Se um
   token no header `Authorization` contasse como segredo, toda chamada
   autenticada seria negada por padrão, e isso quebraria a ação autorizada. Por
   isso o scanner (`secrets_scanner.has_secrets`) olha a URL, o corpo e os
   headers que não são canal de credencial. Os canais declarados
   (`Authorization`, `Cookie`, `X-Api-Key`, nomes com
   `auth/token/secret/key/session/cookie/credential`) aparecem como
   `has_credentials: true`, e o operador pode escrever regra sobre eles. O
   envio de credencial para outra origem fica coberto pelo T03.
2. **307/308 com corpo para outra origem não é seguido (T03).** Reenviar o
   corpo vazaria o payload, e descartá-lo mudaria a semântica do 307. A tool
   devolve a resposta 3xx ao modelo, que decide o próximo passo.
3. **Fragmentos de nome são conservadores (T03).** Um header como
   `X-Session-Id` também cai na mudança de origem. Perder um header não
   sensível num redirect cross-origin custa pouco. Vazar um token custa caro.
4. **GET do stream de time deixa de existir (WEB-02).** A outra opção seria
   exigir CSRF no GET. Mas um GET que inicia trabalho continuaria contrariando
   a semântica HTTP, e o SPA não usa a rota (só `POST /runs` e
   `GET /runs/{id}/events`). Nenhum cliente do repositório precisou mudar, e o
   bundle `bauer/static` não foi reconstruído.
5. **`--local` não foi reinterpretado (WEB-03, condição de STOP).** A garantia
   existente ("nada sai desta máquina", verificada na entrada) passou a valer
   depois do boot com a mesma função `provider_e_local`. `custom` só é local
   quando o host do config é desta máquina. Sem config, falha fechado. O
   switch entre modelos locais continua funcionando. O modo default
   (`profile_set='default'`) não muda.
6. **OAuth em `--local` guarda a credencial e não troca o modelo.** Conectar
   não envia prompt para a nuvem. O callback devolve um aviso em vez de
   selecionar `gpt-5.6-luna`.

## Critérios de aceite

- [x] Uma regra deny de `network.http` interrompe `http_request` e `web_fetch`
  antes do transporte. Com `allow`, a chamada passa. Com `ask`, fica um
  `ApprovalRecord` pendente e nada é enviado.
- [x] Um segredo sintético no corpo, com `contains_secret=False`, é negado
  pela regra padrão. Um `Authorization` legítimo para a própria API passa.
- [x] Num redirect para outro host, outro scheme ou outra porta, os headers de
  credencial são removidos. Na mesma origem (inclusive com `:443` explícito),
  são preservados. O 303 vira GET sem corpo. O 307 preserva método e corpo na
  mesma origem e não é seguido para outra origem. O hop interno é barrado
  antes do request.
- [x] Com sessão por cookie, `GET /api/teams/x/stream` dá 405 e
  `POST /api/teams/x/stream` sem CSRF dá 403, sem chamar
  `orchestrator.stream`. Com CSRF, o POST transmite os eventos.
- [x] Com `--local`, nenhuma destas portas leva a um client cloud: switch,
  seleção salva, OAuth, fallback, roteamento e boot com `profiles_local`
  remoto. O switch local→local continua 200. O modo default continua trocando
  para a nuvem.
- [x] Os scripts `bauer_web_repro2.py` e `bauer_web_crossreview_kernel.py`
  rodam até o fim e imprimem `LOCAL_SWITCH 409`.

## Validação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
uv run python docs/audits/2026-10-12/repro/bauer_web_repro.py         # CSRF_TEAM_STREAM 405 0
uv run python docs/audits/2026-10-12/repro/bauer_web_repro2.py        # LOCAL_SWITCH 409
uv run python docs/audits/2026-10-12/repro/bauer_web_crossreview_kernel.py
```

Regressões: `tests/test_sprint62_network_web_boundaries.py`. Sem o código da
correção, 20 dos 27 casos falham. Os 7 restantes são controles positivos
(ação autorizada continua funcionando) e passam nas duas versões.
