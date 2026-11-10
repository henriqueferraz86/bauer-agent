# BACKLOG — Sprint 69

## Tarefas

### 1. Reproduzir as três lacunas no código atual

Status: concluído

As cinco provas (web_search, mcp_call, delegate_task, lsp_rename e
`/model --local`) reproduzem na base `e6a8c66` (tabela no `SPEC.md`). Sem a
correção, 26 dos 30 casos de
`tests/test_sprint69_network_approval_boundaries.py` falham. Os 4 restantes
são controles positivos.

### 2. `web_search`, `mcp_call`/`mcp_list_tools` e `delegate_task` sob política

Status: concluído

Critérios:
- As operações são `network.search`, `mcp.call` e `agent.delegate`, cada uma
  com payload normalizado calculado pelo runtime.
- Com `deny`, nada chega ao backend de busca, ao cliente MCP, ao provider, ao
  POST remoto ou ao subprocess.
- Com `ask`, fica um approval pendente e nenhum request sai. Com `allow`, a
  chamada passa.
- As regras padrão negam segredo na query, nos argumentos MCP e na tarefa
  para agente remoto. `DEFAULT_RULES` e `config/policy.yaml` concordam.

### 3. `lsp_rename` com operação de política

Status: concluído

Critérios:
- `lsp_rename` passa por `tool.approval`: `waiting_approval` sem chamar o LSP,
  uma execução com `approval_id` aprovado, e `deny` bloqueia.
- O payload de `tool.approval` tem `tool_name` vindo do runtime, para regras
  dirigidas.
- Uma catraca impede built-in com `approval=True` sem operação.

### 4. `/model` em `bauer agent --local`

Status: concluído

Critérios:
- O seletor só oferece `ollama` e recusa os outros providers sem login, sem
  pedir API key e sem escrever nada.
- O rebuild valida o config antes de construir client e usa
  `fallback_models_local`.
- A sessão não troca para client de nuvem. Sem `--local`, a troca continua.

### 5. Gates e revisão de segurança

Status: concluído

Todos os gates passaram: pytest (exit 0, sem falhas), ruff crítico, ruff
estilo e mypy (420 arquivos, sem issues). Revisão do diff:
- os campos do payload calculados pelo runtime sobrescrevem os `args`;
- `contains_secret` só sobe;
- a autorização e o digest continuam presos aos `args` originais;
- a resolução do payload não faz I/O de rede;
- nenhuma regra padrão afrouxa o que já existia (`policy.yaml` antigo cai no
  `allow` de hoje);
- nenhum `except: pass` novo.

## Notas de entrega

- Branch: `codex/sprint-69-security-network-approval`, base `origin/master`
  `e6a8c66`.
- Pendências fora do escopo:
  - o fallback `subprocess` do `delegate_task` (`bauer agent run-one`) não
    herda o `--local` da sessão. Hoje o operador fecha isso por política
    (`agent.delegate` com `when: {dispatch: subprocess}`). Propagar o
    `--local` ao `run-one` muda o contrato dele;
  - se o despacho remoto do `delegate_task` falhar com exceção inesperada, a
    tarefa cai no provider local (comportamento anterior). A regra avalia o
    despacho primário, mas um `deny` de `agent.delegate` bloqueia todos os
    caminhos.
