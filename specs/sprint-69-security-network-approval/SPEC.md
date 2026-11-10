# SPEC — Sprint 69: fronteiras de rede e aprovação restantes (plano 066)

Plano: [`plans/066-pendencias-pos-064-065.md`](../../plans/066-pendencias-pos-064-065.md),
sprint 69. Fecha três pendências registradas nas sprints 61 e 62.
Branch `codex/sprint-69-security-network-approval`, base `origin/master`
`e6a8c66`.

## Reprodução no código atual (base `e6a8c66`)

Antes de qualquer edição, cada lacuna foi reproduzida com dispatcher web,
cliente MCP, client LLM, `httpx.post`, `run_process` e seletor de modelo
falsos:

| Item | Prova antes da correção | Situação |
|---|---|---|
| 1a `web_search` | `_policy_operation_for_tool('web_search') is None`; com regra `deny` de `network.search` e `policy_enabled=True`, `WebDispatcher.search_as_text` é chamado | reproduz |
| 1b `mcp_call` | sem operação; com `deny` de `mcp.call`, `_mcp_client_for` é chamado por `mcp_call` e `mcp_list_tools` | reproduz |
| 1c `delegate_task` | sem operação; com `deny` de `agent.delegate` (operação que já existia na política padrão), o client LLM da sessão recebe a tarefa | reproduz |
| 2 `lsp_rename` | `approval=True` em `_TOOL_SECURITY`, mas sem operação: uma regra `deny` de `tool.approval` não bloqueia e nenhum approval humano é criado | reproduz |
| 3 `/model --local` | `run_agent_session` não sabe que a sessão é `--local`; o seletor oferece e grava qualquer provider; o rebuild aceita config de nuvem e volta a usar `fallback_models` (não `fallback_models_local`). O turno seguinte vai para o client de nuvem | reproduz |

`tests/test_sprint69_network_approval_boundaries.py` tem 38 casos. Os 30
primeiros cobrem os itens 1 a 3: sem o código da correção, 26 falham. Os 4
restantes são controles positivos (`allow` continua passando; regra dirigida
a `lsp_rename` por nome) e passam nas duas versões. O controle "modo default
continua trocando para a nuvem" falha sem a correção só porque
`run_agent_session` ainda não aceita `local_only`.

Os 8 casos da decisão 8 (delegação em `--local`) falham sem a correção. A
lacuna se reproduz: com a marca ligada e o client local falhando, o
subprocess saía sem `--local`, o client de nuvem da sessão era usado, o POST
ia para o agente remoto e o `run-one` construía o client de nuvem. O
controle "subprocess sem `--local` no modo default" falha antes só porque
`LOCAL_ONLY_ENV` ainda não existia.

## Objetivo

Toda tool que manda conteúdo escolhido pelo modelo para fora do processo tem
operação de política. Com `deny`, nada chega ao transporte. Com `ask`, fica um
approval pendente e nenhum request sai. Toda built-in que exige aprovação
passa pela aprovação humana da política, e não só pelo aprovador LLM. O
`--local` do `bauer agent` continua valendo depois do `/model`.

## Escopo

1. **Contrato de operação de rede** para `web_search`, `mcp_call`,
   `mcp_list_tools` e `delegate_task`. Cada um tem uma operação própria e um
   payload normalizado. O payload enriquecido só é usado na avaliação da
   regra. A autorização e o digest da aprovação continuam presos aos `args`
   originais, como na sprint 62.
2. **`lsp_rename` → `tool.approval`.** Uma catraca garante que nenhuma
   built-in com `approval=True` fique sem operação.
3. **`/model` em `bauer agent --local`.** São três camadas:
   - o seletor só oferece providers desta máquina e recusa os outros antes de
     login, de pedir API key ou de escrever `.env`/`config.yaml`;
   - o rebuild valida o config relido com `validar_execucao_local` antes de
     construir qualquer client e usa `fallback_models_local`;
   - a sessão recusa um client que não seja local e continua no modelo
     anterior.

## Decisões

1. **Nomes de operação.** A política já usava o vocabulário `<domínio>.<verbo>`.
   - `web_search` → **`network.search`**. O destino é o backend de busca do
     config, e não uma URL escolhida pelo modelo. Por isso não entra em
     `network.http`. O `TOOLS-REVIEW.md` e o fora-de-escopo da sprint 62 pediam
     um contrato de mapeamento próprio. Payload: `query`, `backend` (valor do
     config, sem auto-detecção, que poderia tocar a rede) e `contains_secret`.
   - `mcp_call` e `mcp_list_tools` → **`mcp.call`**. É o nome que as
     capabilities Agno de MCP já usam (`agno_capabilities/families/mcp.py`,
     `_OUTBOUND_OPERATIONS`). Uma regra do operador vale para os dois caminhos.
     `mcp_list_tools` entrou porque também inicia o servidor ou abre a conexão
     HTTP: sem isso, um `deny` de `mcp.call` ainda deixaria a lista de tools
     chegar ao transporte. Payload: `server`, `tool`,
     `kind` (`call`/`list_tools`), `transport` (`stdio`/`http`/`unconfigured`),
     `host`, `remote` e `contains_secret` (dos `arguments`). O transporte é
     resolvido só a partir de config e env, sem iniciar o servidor.
     `mcp_list_servers` não entrou: só lê config e não inicia nada.
   - `delegate_task` → **`agent.delegate`**. A operação já existia na política
     padrão (`agent.delegate.allow`) e no `team_registry`, mas a tool não
     passava por ela.
2. **O que é "rede" no `delegate_task`.** É o destino da tarefa, definido pelo
   despacho. O payload diz qual é:
   - `dispatch: remote`: o agente do registry tem `url`, e a tarefa vai por
     POST para outro `bauer serve`. `remote` só é verdadeiro quando esse host
     não é desta máquina;
   - `dispatch: provider`: a tarefa vai para o client LLM da sessão, o mesmo
     provider do turno. `provider` e `provider_local` vão no payload;
   - `dispatch: subprocess`: `bauer agent run-one`, que carrega o config
     canônico.

   O agente é resolvido do mesmo jeito que `_delegate_task` resolve, sem I/O
   de rede. Também vão no payload `task`, `agent_name` e `contains_secret`
   (tarefa + contexto).
3. **Regras padrão.** As mesmas regras entram em `DEFAULT_RULES` e em
   `config/policy.yaml`, e um teste trava que os dois concordam:
   - `network.search`: `deny` com `contains_secret`, depois `allow`;
   - `mcp.call`: `deny` com `contains_secret`, depois `allow`. Fica coerente
     com o bridge Agno, que já recusa material secreto em `mcp.call`;
   - `agent.delegate`: `deny` só com `remote: true` **e** `contains_secret`,
     depois o `allow` que já existia. Mandar a tarefa ao provider da sessão
     não é exfiltração nova: o contexto do turno já vai para esse provider.

   Um `policy.yaml` antigo sem essas regras cai no `allow` implícito, ou seja,
   o comportamento de hoje. Nenhuma operação nova entrou em
   `ASK_WHEN_UNMATCHED_OPERATIONS`.
4. **`lsp_rename` usa `tool.approval`, e não uma operação nova.** A
   `tool.approval` é a operação da sprint 61 para "a definição de segurança
   exige aprovação humana". Ela tem default `ask` e pede aprovação mesmo
   quando nenhuma regra casa (`ASK_WHEN_UNMATCHED_OPERATIONS`). Assim, um
   `policy.yaml` antigo não derruba a aprovação. `filesystem.write` não
   serviria: a regra padrão só pergunta fora do workspace, e a aprovação que
   a tool declara se perderia. Para que o operador possa dirigir uma regra a
   uma tool, o payload de `tool.approval` ganhou `tool_name`. O runtime
   escreve esse campo depois dos `args`, então o modelo não consegue forjá-lo.
   A catraca `test_every_builtin_requiring_approval_has_policy_operation` cobre
   built-ins futuras.
5. **`/model --local`: só `ollama` no seletor.** O `custom` ficou de fora. O
   seletor grava o host em `openai.host`, mas a locality do `custom` depende
   de `custom.host` (`provider_e_local`). Aceitá-lo aqui seria confiar num host
   que ninguém verificou. Quem precisa de `custom` local edita o config e
   reinicia, e o boot valida o host. A recusa diz o motivo e lembra de rodar
   sem `--local` para usar a nuvem.
6. **Rótulo de provider no client reconstruído.** O rebuild grava
   `client._provider = cfg.model.provider`. Antes, um client de `_build_client`
   não tinha esse atributo, e o `/model` rotulava qualquer provider como
   `openai`. Na segunda camada, a sessão usa esse rótulo ou, sem ele,
   `cost_meter.provider_from_client`. Provider desconhecido conta como não
   local (falha fechado).
7. **Modo default não muda.** Sem `--local`, o `/model` continua trocando para
   a nuvem (controle positivo no teste).

8. **`--local` na delegação (pedido do coordenador, fechado nesta sprint).**
   Antes, o fallback `subprocess` do `delegate_task` (`bauer agent run-one`)
   carregava o config canônico sem `--local`. Numa sessão `--local` com o
   client local falhando, a delegação caía num modelo de nuvem. A garantia
   agora tem três peças:
   - **Marca de processo `BAUER_LOCAL_ONLY=1`.** O `bauer agent --local` e o
     `bauer serve --local` a gravam depois de validar o boot. Os subprocessos
     a herdam pelo ambiente. A marca só liga a garantia, nunca a desliga.
   - **`run-one --local`.** O flag é passado explicitamente pelo
     `delegate_task`, e a marca herdada tem o mesmo efeito. Antes de
     construir client, o `run-one` verifica `provider_e_local` do config
     resolvido. Sendo nuvem, recusa iniciar (exit 1, nada enviado). Sendo
     local, regrava a marca, e delegações aninhadas herdam a garantia. A
     checagem é a do provider que o `run-one` de fato usa, não
     `validar_execucao_local` inteiro: o `run-one` não roteia por tier nem
     usa fallback, então exigir `profiles_local` recusaria sem motivo.
   - **`delegate_task` em sessão `--local`:**
     - agente remoto cujo host não é desta máquina → `ToolError` antes do
       POST;
     - agente em localhost é aceito, porque é outro processo desta máquina;
     - o client da sessão só é usado se for local. Para isso conta o rótulo
       `_provider`, a classe Ollama ou o host local. Desconhecido conta como
       não local;
     - o subprocess sempre recebe `--local`, inclusive quando o despacho
       remoto falha de forma inesperada e cai no fallback.
   - **Isolamento de testes.** `tests/conftest.py` registra
     `BAUER_LOCAL_ONLY` no `monkeypatch` de cada teste, e a marca gravada por
     um teste não vaza para o seguinte no mesmo worker do xdist.

## Fora de escopo

- O aprovador LLM (`auxiliary.approval_model`) e outros clientes auxiliares
  de uma sessão `--local` não fazem parte da delegação e não foram
  auditados aqui. O `validar_execucao_local` do boot não os cobre.
- `bauer agent run` (agente nomeado) não tem `--local`. O rebuild dele não
  mudou.
- Modo `audit` da política: um `ask` continua sendo auto-aprovado com trilha,
  como já acontece com `shell.execute`. Não é privilégio novo.

## Critérios de aceite

- [x] `web_search`, `mcp_call`/`mcp_list_tools` e `delegate_task`: com
  `deny`, nada chega ao backend de busca, ao cliente MCP, ao provider, ao
  `httpx.post` ou ao `run_process`. Com `ask`, há um `ApprovalRecord`
  pendente e nenhum request. Com `allow`, a chamada passa.
- [x] Pelas regras padrão, um segredo sintético na query, nos argumentos MCP
  ou numa tarefa para agente remoto é negado sem a flag do modelo. A tarefa
  com segredo para o provider local da sessão passa.
- [x] `lsp_rename` fica em `waiting_approval` (`tool.approval`) sem chamar o
  LSP e roda uma vez com o `approval_id` aprovado. Com `deny`, é bloqueado.
  Uma regra `allow` com `when: {tool_name: lsp_rename}` libera só ela.
- [x] Em `--local`, o `/model` não oferece nem grava provider de nuvem, o
  rebuild recusa config de nuvem antes de construir client e usa a cadeia
  local, e a sessão não troca para um client de nuvem. Sem `--local`, a troca
  continua.
- [x] Numa sessão `--local` com client local falhando, o `delegate_task` só
  chega ao `run-one --local`, nunca a um client de nuvem. Um client de nuvem
  na sessão não é usado. Um agente remoto fora da máquina é recusado antes do
  POST. O fallback do despacho remoto continua local. O `run-one` (flag ou
  marca herdada) recusa config de nuvem antes de construir client. Sem
  `--local`, o subprocess não muda.

## Plano de validação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```

Regressões: `tests/test_sprint69_network_approval_boundaries.py`.
